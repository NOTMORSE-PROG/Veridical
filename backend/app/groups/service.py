"""V-062: resolves the free-text group name an instructor types into a
real, deduplicated `Group` row.

Business logic only (CODING.md §2) — HTTP shaping lives in `router.py`.
"""

import re

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import sqlalchemy_url
from app.errors import ConflictError, FileMalformedError
from app.models.group import Group, GroupMember, Program

# The one literal every `group_label`-default reader compares against
# (frontend's `UNGROUPED_LABEL`, `manuscriptIdentity()`) — centralized here
# so the backend side of that comparison has exactly one source.
DEFAULT_GROUP_LABEL = "Ungrouped"

# V-062 (AC5, design review finding while speccing the dashboard's program
# filter): a real program NAME can never collide with this (Program.name
# is instructor/config-seeded free text like "CS"/"IT", never a value
# starting with two underscores) -- lets `GET /manuscripts?program=...`
# express "no program set" as a real, selectable filter state instead of
# that state being unreachable (an inner join alone can only ever match
# manuscripts that HAVE a program, never the ones that don't).
UNSET_PROGRAM_FILTER = "__unset__"

# ASCII-only on purpose, not Python's broader `\s` (which matches extra
# Unicode whitespace, e.g. U+00A0 NBSP): must stay EXACTLY the character
# class Postgres's `regexp_replace(..., '\s+', ...)` matches (POSIX ARE's
# `\s` is `[ \t\n\r\f\v]`, ASCII-only -- see migration 0025's
# `_NORMALIZE_SQL`), or the two sides compute different keys for the same
# input. backend review (V-062 review) found the previous version of
# this diverged for real: the old code did `.strip()` (Python, Unicode-
# broad) BEFORE collapsing runs, while the SQL did `trim()` (Postgres,
# ASCII-space-only) before its own collapse -- a leading/trailing tab
# survived Postgres's `trim()` untouched, then got CONVERTED to a space by
# the collapse step, leaving a stray boundary space only on the SQL side.
# Live-reproduced: a manuscript backfilled from a tab-padded group_label
# landed in a DIFFERENT group than a clean re-upload of the "same" name.
# Fixed by using the identical operation in the identical order on both
# sides -- collapse every whitespace run to one space FIRST (so a
# boundary tab becomes a boundary space on both sides identically), THEN
# trim the (now guaranteed-ASCII-space) boundary character.
_WHITESPACE_RUN = re.compile(r"[ \t\n\r\f\v]+")


def normalize_group_name(name: str) -> str:
    """Collapses "Group  4", " group 4 ", "GROUP 4" to the same key so two
    differently-typed spellings of the same team resolve to one `Group`
    row (AC1). Matches migration 0025's backfill SQL
    (`lower(trim(regexp_replace(x, '\\s+', ' ', 'g')))`) exactly -- the two
    must never diverge, or a manuscript ingested through the app could
    land in a different group than the one its own backfilled siblings
    ended up in. See `_WHITESPACE_RUN`'s comment for why this must stay
    ASCII-only and collapse-then-trim, not the other order."""
    return _WHITESPACE_RUN.sub(" ", name).strip(" ").lower()


async def list_programs(session: AsyncSession) -> list[Program]:
    return list((await session.scalars(select(Program).order_by(Program.name))).all())


async def program_name_for(session: AsyncSession, program_id: int | None) -> str | None:
    if program_id is None:
        return None
    return await session.scalar(select(Program.name).where(Program.id == program_id))


async def seed_default_programs() -> list[str]:
    """Idempotent (find-or-create per name), reads `Settings.default_programs`.

    Lives here, not in `scripts/`, because `app/main.py`'s boot lifespan
    calls it directly (V-062) -- `scripts/` is dev/ops-only tooling never
    copied into the Docker image (found live: a module-level `from
    scripts... import` in `app/main.py` crashed the container on start
    with `ModuleNotFoundError`, since `scripts/` isn't part of the built
    image at all). `scripts/seed_programs.py` is now a thin CLI wrapper
    around this same function, for a by-hand run against any environment.

    Opens its OWN short-lived engine rather than reusing `app.db`'s
    process-wide cached one (`get_engine()`) -- that global is monkeypatch-
    fragile across a test suite that swaps `DATABASE_URL` between scratch
    databases without always resetting `app.db._engine` (several existing
    fixtures do this reset explicitly; this function must not depend on
    every future caller remembering to)."""
    settings = get_settings()
    engine = create_async_engine(sqlalchemy_url(settings.database_url))
    created: list[str] = []
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            existing = set((await session.scalars(select(Program.name))).all())
            for name in settings.default_programs_list:
                if name not in existing:
                    session.add(Program(name=name))
                    created.append(name)
            if created:
                await session.commit()
    finally:
        await engine.dispose()
    return created


async def list_groups(session: AsyncSession, instructor_id: int) -> list[tuple[Group, str | None]]:
    """Instructor-scoped, each paired with its program name (or None,
    "Not set") -- one query, no N+1."""
    rows = (
        await session.execute(
            select(Group, Program.name)
            .outerjoin(Program, Program.id == Group.program_id)
            .where(Group.instructor_id == instructor_id)
            .order_by(Group.name)
        )
    ).all()
    return list(rows)


def validate_group_fields(name: str, members: list[str], title: str | None = None) -> None:
    """Reject values that cannot fit the schema, including normalized Unicode."""
    if (
        len(name) > Group.name.type.length
        or len(normalize_group_name(name)) > Group.name_normalized.type.length
    ):
        raise FileMalformedError("The group name is too long. Enter a shorter name.")
    if any(len(n) > GroupMember.name.type.length for n in members):
        raise FileMalformedError("A member name is too long. Enter a shorter name.")
    if title is not None and len(title) > Group.title.type.length:
        raise FileMalformedError("The group title is too long. Enter a shorter title.")


async def resolve_or_create_group(
    session: AsyncSession, instructor_id: int, raw_name: str
) -> Group:
    """Resolve a free-text upload label without rolling back the caller."""
    display_name = raw_name.strip() or DEFAULT_GROUP_LABEL
    validate_group_fields(display_name, [])
    normalized = normalize_group_name(display_name)
    for _ in range(get_settings().group_create_max_attempts):
        existing = await session.scalar(
            select(Group).where(
                Group.instructor_id == instructor_id, Group.name_normalized == normalized
            )
        )
        if existing is not None:
            return existing
        try:
            async with session.begin_nested():
                group = Group(
                    instructor_id=instructor_id, name=display_name, name_normalized=normalized
                )
                session.add(group)
                await session.flush()
            return group
        except IntegrityError:
            continue
    raise ConflictError("The group changed while saving. Try again.")


async def _proposal_candidates(
    session: AsyncSession, instructor_id: int, normalized: str, *, lock: bool = False
) -> list[Group]:
    query = (
        select(Group)
        .where(
            Group.instructor_id == instructor_id,
            or_(Group.name_normalized == normalized, Group.proposal_name_normalized == normalized),
        )
        .order_by(Group.id)
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    return list((await session.scalars(query)).all())


async def _member_names(session: AsyncSession, group_id: int) -> set[str]:
    return {
        normalize_group_name(n)
        for n in (
            await session.scalars(select(GroupMember.name).where(GroupMember.group_id == group_id))
        ).all()
    }


async def find_rule3_collision(
    session: AsyncSession,
    instructor_id: int,
    short_name: str,
    member_names: list[str],
    exclude_group_id: int | None = None,
) -> Group | None:
    """V-063 (owner's call, 2026-08-19): a READ-ONLY pre-check for the
    confirm form, run BEFORE the instructor clicks Confirm -- rule 3 of
    `match_or_create_group_from_proposal`'s own matching rule (ticket's
    DECIDED section) says "PROPOSE the match, do not apply it" for a
    same-short-name-zero-overlap collision, but the shipped confirm flow
    only ever disclosed that collision AFTER the fact, via a
    disambiguating suffix on the PATCH response (backend review, V-063
    review). This lets the frontend show "a group named X already exists,
    but none of these members match its records" while the instructor can
    still change what they're about to submit, instead of only after.

    Returns the colliding group, or `None` if there's no collision --
    either no same-named group exists at all, or one does and at least
    one member already overlaps (the ordinary match case, not a
    collision). No incoming members at all is never a collision (nothing
    to compare against yet, e.g. the instructor hasn't finished typing)."""
    display_name = short_name.strip() or DEFAULT_GROUP_LABEL
    base_normalized = normalize_group_name(display_name)
    normalized_incoming = {normalize_group_name(m) for m in member_names if m.strip()}
    if not normalized_incoming:
        return None

    candidates = [
        g
        for g in await _proposal_candidates(session, instructor_id, base_normalized)
        if g.id != exclude_group_id
    ]
    for candidate in candidates:
        if normalized_incoming & await _member_names(session, candidate.id):
            return None
    return candidates[0] if candidates else None


async def match_or_create_group_from_proposal(
    session: AsyncSession, instructor_id: int, short_name: str, member_names: list[str]
) -> tuple[Group, bool]:
    """V-063's own matching rule (ticket's DECIDED section, proven against
    the owner's own real documents): short name matches AND at least one
    member overlaps -> the SAME group (any newly observed member is
    added, never overwrites what's already recorded). Short name matches
    but zero members overlap -> rule 3, two DIFFERENT teams sharing an
    acronym -- never silently merged. No short-name match at all -> a
    plain new group.

    Returns `(group, matched)` -- `matched=True` means an existing group
    was found and reused; `False` means a new one was created, so the
    caller can disclose which happened (AC4-adjacent honesty, matching
    this module's own `resolve_or_create_group` precedent).

    A rule-3 collision (short name taken by an unrelated group with no
    member overlap) is disambiguated with a numeric suffix so both can
    coexist under V-062's `(instructor_id, name_normalized)` uniqueness
    guarantee, which still protects the ordinary "same instructor,
    typo/case variant" case unchanged -- retried on `IntegrityError`
    exactly like `resolve_or_create_group`, RE-QUERYING candidates from
    scratch on every attempt (not just re-suffixing), for the same
    concurrent-upload reason `resolve_or_create_group` re-queries at the
    top of its own loop. backend review (V-063 review) reproduced live
    why the re-query matters: two concurrent submissions for the SAME
    team (overlapping members, no pre-existing group) both see an empty
    `candidates` list before either commits, so a suffix-only retry would
    split one team across "VERIDICAL" and "VERIDICAL (2)" instead of the
    loser's retry finding the winner's row via the overlap check."""
    display_name = short_name.strip() or DEFAULT_GROUP_LABEL
    base_normalized = normalize_group_name(display_name)
    normalized_incoming = {normalize_group_name(m) for m in member_names if m.strip()}

    validate_group_fields(display_name, member_names)
    for _ in range(get_settings().group_create_max_attempts):
        candidates = await _proposal_candidates(session, instructor_id, base_normalized, lock=True)
        matches = []
        for candidate in candidates:
            if normalized_incoming & await _member_names(session, candidate.id):
                matches.append(candidate)
        if len(matches) > 1:
            raise ConflictError(
                "More than one saved group matches these names. "
                "Use the exact saved group name to distinguish the assignment."
            )
        if matches:
            group = matches[0]
            existing = await _member_names(session, group.id)
            for member in member_names:
                stripped = member.strip()
                normalized = normalize_group_name(stripped)
                if stripped and normalized not in existing:
                    session.add(GroupMember(group_id=group.id, name=stripped))
                    existing.add(normalized)
            await session.flush()
            return group, True

        used = set(
            (
                await session.scalars(
                    select(Group.name_normalized).where(Group.instructor_id == instructor_id)
                )
            ).all()
        )
        # Among N occupied names, at least one of N+1 candidates is free.
        for suffix in range(1, len(used) + 2):
            name = display_name if suffix == 1 else f"{display_name} ({suffix})"
            normalized = normalize_group_name(name)
            if normalized not in used:
                break
        validate_group_fields(name, member_names)
        try:
            # All potentially conflicting writes belong inside the SAVEPOINT.
            async with session.begin_nested():
                group = Group(
                    instructor_id=instructor_id,
                    name=name,
                    name_normalized=normalized,
                    proposal_name_normalized=base_normalized,
                )
                session.add(group)
                await session.flush()
                seen = set()
                for member in member_names:
                    stripped = member.strip()
                    normalized_member = normalize_group_name(stripped)
                    if stripped and normalized_member not in seen:
                        session.add(GroupMember(group_id=group.id, name=stripped))
                        seen.add(normalized_member)
                await session.flush()
            return group, False
        except IntegrityError:
            continue
    raise ConflictError("The group changed while saving. Try again.")
