"""Rubric upload orchestration: file -> extraction -> decomposition ->
persisted rubric + criteria (F2.1, feeds the review screen 4c/4d), plus
versioning (F2.4, V-013): re-upload into an existing family, activate a
version, list families/versions, delete a version — all guarded so a
rubric with reports pinned to it is never silently mutated or dropped
(measurement integrity, TESTING.md §2).
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.checks.citations.recency import CitationRecencyFormatSettings, effective_settings
from app.config import Settings, get_settings
from app.errors import ConflictError, NotFoundError
from app.ingest.admission import PreparationLease, preparation_scope
from app.ingest.service import detect_format, save_upload, select_extractor
from app.llm.base import LLMClient
from app.models.enums import CriterionType, RubricParseStatus
from app.models.group import Program
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckRun
from app.rubric.decompose import raw_text_for_decomposition
from app.rubric.schemas import (
    CitationRecencySettingsOut,
    RubricListItem,
    UpdateCriteriaRequest,
)
from app.rubric.validate import attempt_decomposition
from app.storage import get_storage, storage_key_for

_logger = logging.getLogger(__name__)


async def create_rubric_from_upload(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    title: str,
    llm: LLMClient,
    settings: Settings | None = None,
    *,
    instructor_id: int,
    family_id: uuid.UUID | None = None,
) -> Rubric:
    """Share upload admission before a format family/version or stored file changes."""
    settings = settings or get_settings()
    with preparation_scope(settings) as lease:
        work = asyncio.create_task(
            _create_rubric_from_upload(
                session,
                chunks,
                filename,
                title,
                llm,
                settings,
                instructor_id=instructor_id,
                family_id=family_id,
                preparation_lease=lease,
            )
        )
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not work.cancelled():
                work.exception()
            raise


async def _create_rubric_from_upload(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    title: str,
    llm: LLMClient,
    settings: Settings | None = None,
    *,
    instructor_id: int,
    preparation_lease: PreparationLease,
    family_id: uuid.UUID | None = None,
) -> Rubric:
    """`family_id=None` (V-010 default) starts a brand-new family at
    version 1. `family_id` set (V-013, "Upload new format" on screen 4m)
    adds the next version to an EXISTING family — old versions, and
    anything pinned to them, are untouched. `instructor_id` is the
    authenticated caller (BUG-001/D-020) — a family_id owned by a
    different instructor reads as NotFoundError, same as an unknown one."""
    settings = settings or get_settings()

    version = 1
    # V-064 (backend review finding, P1, live-reproduced): a new version
    # uploaded into an existing family used to always start at
    # `program_id=None`, silently reverting a family that had a program
    # set back to "eligible for everything" the moment this new version
    # was activated -- no error, nothing to notice. Every sibling shares
    # the same `program_id` (kept in sync by `set_rubric_family_program`,
    # which updates every row at once), so any one of them names the
    # family's current value; carried forward here so a routine "upload a
    # corrected format" action can never silently undo program scoping.
    family_program_id: int | None = None
    # The family's citation recency setting is carried forward the same way.
    family_citation_recency: dict | None = None
    if family_id is not None:
        await _lock_rubric_family(session, family_id, instructor_id)
        siblings = (
            await session.scalars(
                select(Rubric)
                .where(Rubric.rubric_family_id == family_id, Rubric.instructor_id == instructor_id)
                .execution_options(populate_existing=True)
            )
        ).all()
        if not siblings:
            raise NotFoundError(f"No rubric family {family_id}.")
        version = max(r.version for r in siblings) + 1
        family_program_id = siblings[0].program_id
        family_citation_recency = siblings[0].citation_recency

    # The row exists (and has its id) before the file does — same pattern
    # as manuscript ingestion: the server-owned id names the stored file,
    # never the user-supplied filename.
    rubric = Rubric(
        instructor_id=instructor_id,
        title=title,
        source_file="",
        program_id=family_program_id,
        citation_recency=family_citation_recency,
        **({"rubric_family_id": family_id, "version": version} if family_id is not None else {}),
    )
    session.add(rubric)
    await session.commit()
    await session.refresh(rubric)

    suffix = Path(filename).suffix.lower()[:8]
    dest = settings.data_dir / settings.rubric_upload_subdir / f"{rubric.id}{suffix}"
    publication_attempted = False
    try:
        await save_upload(chunks, dest, settings)
        extractor = select_extractor(detect_format(dest))
        result = await preparation_lease.run(extractor, str(dest), settings)
        storage = get_storage(settings)
        try:
            publication_attempted = True
            await preparation_lease.run(
                storage.put_file, dest, storage_key_for(settings, str(dest))
            )
        finally:
            storage.close()
        rubric.source_file = str(dest)
        await session.commit()

    except Exception:
        if publication_attempted:
            try:
                storage = get_storage(settings)
                try:
                    await preparation_lease.run(
                        storage.delete, storage_key_for(settings, str(dest))
                    )
                finally:
                    storage.close()
            except Exception as cleanup_error:
                _logger.warning(
                    "Required-format source cleanup failed (%s)", type(cleanup_error).__name__
                )
        # BUG-027: an unreadable/corrupt file raises here, before there is
        # anything for the instructor to review (attempt_decomposition
        # below never raises, V-011 — everything after this point always
        # succeeds with a real, if possibly `needs_review`, outcome). Left
        # uncaught, the rubric row created above survives as a permanent
        # 0-criteria "Draft" — the family's new highest version — which
        # makes ReviewCriteriaPage's `readOnly` check treat the real
        # active version as superseded, contradicting its own "This
        # rubric is active" message on the same screen. Unlike a failed
        # MANUSCRIPT ingestion (kept as a permanent, informative row,
        # BUG-016 — the dashboard's whole job is listing every upload
        # attempt), a rubric VERSION that never had anything reviewable
        # was never real: delete it rather than leave a ghost in Version
        # History.
        #
        # backend review finding: if the exception that landed us here
        # was itself a DB-level failure mid-flush (a dropped connection,
        # a Neon idle-suspend blip — app/db.py's check_connectivity
        # docstring already documents this as a live condition), the
        # session needs a rollback before it can issue the DELETE below —
        # otherwise that commit raises its own DBAPIError, the original
        # exception never propagates, and the orphaned row survives
        # anyway (reproduced live: forcing a poisoned transaction here
        # left the ghost row in place). Same pattern as BUG-032
        # (app/pipeline/machine.py's except blocks) — rollback expires
        # the ORM object's attributes, so a refresh is needed before
        # `session.delete(rubric)` touches it again.
        await session.rollback()
        await session.refresh(rubric)
        dest.unlink(missing_ok=True)
        await session.delete(rubric)
        await session.commit()
        raise

    raw_text = raw_text_for_decomposition(result.blocks)

    # Never raises (V-011): a persistent failure comes back as a
    # needs_review outcome, not an exception — the upload always succeeds
    # and lands the instructor on a reviewable rubric (charter rule 1).
    outcome = await attempt_decomposition(
        session, rubric.id, result.blocks, raw_text, llm, settings
    )

    session.add_all(
        Criterion(
            rubric_id=rubric.id,
            type=CriterionType(criterion.type),
            text=criterion.text,
            evidence=criterion.evidence_needed,
            weight=Decimal(str(criterion.weight)),
            position=position,
            # V-069 AC1: structured, never squashed into `evidence` prose.
            levels=[lvl.model_dump() for lvl in criterion.levels] if criterion.levels else None,
        )
        for position, criterion in enumerate(outcome.criteria)
    )
    rubric.parse_status = (
        RubricParseStatus.needs_review if outcome.needs_review else RubricParseStatus.parsed
    )
    rubric.parse_issues = outcome.issues or None
    await session.commit()
    await session.refresh(rubric, attribute_names=["criteria"])
    return rubric


async def _lock_rubric_family(
    session: AsyncSession, family_id: uuid.UUID, instructor_id: int
) -> None:
    # Family writers must acquire siblings in one order before touching a target.
    # Otherwise confirmation can deadlock with a family-wide program change.
    ids = (
        await session.scalars(
            select(Rubric.id)
            .where(Rubric.rubric_family_id == family_id, Rubric.instructor_id == instructor_id)
            .order_by(Rubric.id)
            .with_for_update()
        )
    ).all()
    if not ids:
        raise NotFoundError(f"No rubric family {family_id}.")


async def get_rubric(
    session: AsyncSession, rubric_id: int, instructor_id: int, *, for_update: bool = False
) -> Rubric:
    """Row-level authorized (BUG-001/D-020): a rubric owned by a different
    instructor reads as NotFoundError, identical to an unknown id — no
    existence leak, same pattern as `_owned_check_run` in
    `app/report/service.py`."""
    if for_update:
        family_id = await session.scalar(
            select(Rubric.rubric_family_id).where(
                Rubric.id == rubric_id, Rubric.instructor_id == instructor_id
            )
        )
        if family_id is None:
            raise NotFoundError(f"No rubric with id {rubric_id}.")
        await _lock_rubric_family(session, family_id, instructor_id)
    query = (
        select(Rubric)
        .where(Rubric.id == rubric_id, Rubric.instructor_id == instructor_id)
        .options(selectinload(Rubric.criteria))
    )
    if for_update:
        query = query.execution_options(populate_existing=True)
    rubric = await session.scalar(query)
    if rubric is None:
        raise NotFoundError(f"No rubric with id {rubric_id}.")
    max_version = await session.scalar(
        select(func.max(Rubric.version)).where(Rubric.rubric_family_id == rubric.rubric_family_id)
    )
    # Not an ORM column — attached so RubricOut.model_validate(rubric,
    # from_attributes=True) can read it like any other field (CODING.md
    # §2: routers stay thin, shaping stays here).
    rubric.is_latest_version = max_version == rubric.version  # type: ignore[attr-defined]
    rubric.can_edit_criteria = (  # type: ignore[attr-defined]
        rubric.is_latest_version and not await _has_reports(session, rubric_id)
    )
    rubric.program = await _program_name(session, rubric.program_id)  # type: ignore[attr-defined]
    rubric.citation_recency_settings = _recency_view(rubric.citation_recency)  # type: ignore[attr-defined]
    return rubric


def _recency_view(stored: dict | None) -> CitationRecencySettingsOut:
    try:
        return CitationRecencySettingsOut(**effective_settings(stored, get_settings()))
    except ValueError:
        # A saved setting that no longer validates is shown as such; the
        # check reports it as unassessed instead of guessing a window.
        return CitationRecencySettingsOut(
            **effective_settings(None, get_settings()), saved_setting_invalid=True
        )


async def set_rubric_family_citation_recency(
    session: AsyncSession,
    family_id: uuid.UUID,
    body: CitationRecencyFormatSettings,
    instructor_id: int,
) -> CitationRecencySettingsOut:
    """Sets the whole family's citation recency setting in one write, like
    `set_rubric_family_program`. A field left null uses the configured
    default; all fields null clears the setting. Reports already made keep
    the values recorded with them."""
    await _lock_rubric_family(session, family_id, instructor_id)
    stored = body.model_dump(exclude_none=True) or None
    await session.execute(
        update(Rubric)
        .where(Rubric.rubric_family_id == family_id, Rubric.instructor_id == instructor_id)
        .values(citation_recency=stored)
    )
    await session.commit()
    return _recency_view(stored)


async def _program_name(session: AsyncSession, program_id: int | None) -> str | None:
    if program_id is None:
        return None
    return await session.scalar(select(Program.name).where(Program.id == program_id))


async def _has_reports(session: AsyncSession, rubric_id: int) -> bool:
    count = await session.scalar(
        select(func.count()).select_from(CheckRun).where(CheckRun.rubric_id == rubric_id)
    )
    return bool(count)


async def _activate_only(session: AsyncSession, rubric: Rubric) -> None:
    """Exactly one active version per family (F2.4 AC) — deactivate every
    sibling before activating this one, in the same transaction."""
    await session.execute(
        update(Rubric)
        .where(Rubric.rubric_family_id == rubric.rubric_family_id, Rubric.id != rubric.id)
        .values(is_active=False)
    )
    rubric.is_active = True


async def update_criteria(
    session: AsyncSession, rubric_id: int, body: UpdateCriteriaRequest, instructor_id: int
) -> Rubric:
    """Screen 4d's Save/Confirm action (F2.3): replaces the full criteria
    set (edits, additions, deletions) in one transaction, and — only when
    the instructor explicitly confirms — activates the rubric. Nothing
    runs against a rubric before that flip (charter rule 1: the human
    confirms the parse).

    F2.4 edge case: editing the ACTIVE version once it has reports pinned
    to it would silently change what those reports measured — blocked;
    the instructor uploads a new version instead (`family_id=` on
    create_rubric_from_upload)."""
    rubric = await get_rubric(session, rubric_id, instructor_id, for_update=True)
    if not rubric.can_edit_criteria:
        raise ConflictError(
            "This version is used by a check or belongs to the version history "
            "and can no longer be edited. Upload a new version instead "
            "(old reports stay pinned to this one)."
        )
    existing = {c.id: c for c in rubric.criteria}
    incoming_ids = {c.id for c in body.criteria if c.id is not None}

    unknown_ids = incoming_ids - existing.keys()
    if unknown_ids:
        raise NotFoundError(f"Criteria {sorted(unknown_ids)} do not belong to rubric {rubric_id}.")

    stale_ids = existing.keys() - incoming_ids
    if stale_ids:
        await session.execute(delete(Criterion).where(Criterion.id.in_(stale_ids)))

    for position, criterion_in in enumerate(body.criteria):
        # V-069: round-tripped as given, never invented client-side — this
        # ticket adds no per-level editing UI, so `levels` here is either
        # exactly what was last persisted (an existing row) or `None` (a
        # hand-added new row, which has no scale unless a future ticket
        # adds one).
        levels = [lvl.model_dump() for lvl in criterion_in.levels] if criterion_in.levels else None
        if criterion_in.id is None:
            session.add(
                Criterion(
                    rubric_id=rubric_id,
                    type=CriterionType(criterion_in.type),
                    text=criterion_in.text,
                    evidence=criterion_in.evidence,
                    weight=Decimal(str(criterion_in.weight)),
                    position=position,
                    levels=levels,
                )
            )
        else:
            row = existing[criterion_in.id]
            row.type = CriterionType(criterion_in.type)
            row.text = criterion_in.text
            row.evidence = criterion_in.evidence
            row.weight = Decimal(str(criterion_in.weight))
            row.position = position
            row.levels = levels

    if body.confirm:
        # BUG-052: confirming used to reset `parse_status` to `parsed` and
        # wipe `parse_issues` unconditionally -- true in the narrow sense
        # that the instructor's review IS the human-in-the-loop resolution
        # (charter rule 1), but it erased the one piece of information the
        # rest of the product needs to keep showing: THAT the parser
        # flagged this rubric before it was confirmed. `parse_status`/
        # `parse_issues` are left exactly as `attempt_decomposition` (or a
        # prior save) set them -- confirming activates the rubric, it does
        # not retroactively rewrite the parser's own history. Nothing else
        # in the pipeline gates on `parse_status` (grepped: only the
        # pre-confirm review-screen banner reads it), so a rubric staying
        # `needs_review` after activation blocks nothing; it only means
        # the report can honestly say so (`ReportOut.rubric_needs_review`).
        await _activate_only(session, rubric)

    await session.commit()
    await session.refresh(rubric, attribute_names=["criteria"])
    return await get_rubric(session, rubric_id, instructor_id)


async def activate_rubric(session: AsyncSession, rubric_id: int, instructor_id: int) -> Rubric:
    """Screen 4m's "Activate" action — switch which EXISTING version is
    active without touching its criteria (unlike confirm, which follows
    an edit)."""
    rubric = await get_rubric(session, rubric_id, instructor_id, for_update=True)
    await _activate_only(session, rubric)
    await session.commit()
    return await get_rubric(session, rubric_id, instructor_id)


async def _to_list_item(session: AsyncSession, rubric: Rubric) -> RubricListItem:
    criteria_count = await session.scalar(
        select(func.count()).select_from(Criterion).where(Criterion.rubric_id == rubric.id)
    )
    report_count = await session.scalar(
        select(func.count()).select_from(CheckRun).where(CheckRun.rubric_id == rubric.id)
    )
    return RubricListItem(
        id=rubric.id,
        rubric_family_id=rubric.rubric_family_id,
        version=rubric.version,
        title=rubric.title,
        is_active=rubric.is_active,
        created_at=rubric.created_at,
        criteria_count=criteria_count or 0,
        report_count=report_count or 0,
        program=await _program_name(session, rubric.program_id),
    )


async def list_rubric_versions(
    session: AsyncSession, family_id: uuid.UUID, instructor_id: int
) -> list[RubricListItem]:
    """Row-level authorized (BUG-001/D-020): a family owned by a different
    instructor reads as NotFoundError, same as an unknown id."""
    rubrics = (
        await session.scalars(
            select(Rubric)
            .where(Rubric.rubric_family_id == family_id, Rubric.instructor_id == instructor_id)
            .order_by(Rubric.version.desc())
        )
    ).all()
    if not rubrics:
        raise NotFoundError(f"No rubric family {family_id}.")
    return [await _to_list_item(session, r) for r in rubrics]


async def list_rubric_families(session: AsyncSession, instructor_id: int) -> list[RubricListItem]:
    """One row per family (screen 4m's top-level list) — the active
    version if one exists, else the latest version. Two families (e.g.
    a CS format and an IT format) coexist independently (ticket edge
    case)."""
    rubrics = (
        await session.scalars(
            select(Rubric)
            .where(Rubric.instructor_id == instructor_id)
            .order_by(Rubric.rubric_family_id, Rubric.is_active.desc(), Rubric.version.desc())
        )
    ).all()
    seen: set[uuid.UUID] = set()
    representatives = []
    for rubric in rubrics:
        if rubric.rubric_family_id not in seen:
            seen.add(rubric.rubric_family_id)
            representatives.append(rubric)
    return [await _to_list_item(session, r) for r in representatives]


async def set_rubric_family_program(
    session: AsyncSession, family_id: uuid.UUID, program_id: int | None, instructor_id: int
) -> list[RubricListItem]:
    """Screen 4m (V-064, AC1): sets (or clears, `program_id=None`) the
    WHOLE family's program in one write -- every version row, not just
    the latest, so an older (still-readable, V-013) version of the
    family never disagrees with its own family's current program.
    Row-level authorized: a family owned by a different instructor reads
    as NotFoundError, same as every other rubric lookup in this module."""
    await _lock_rubric_family(session, family_id, instructor_id)
    rubrics = (
        await session.scalars(
            select(Rubric).where(
                Rubric.rubric_family_id == family_id, Rubric.instructor_id == instructor_id
            )
        )
    ).all()
    if not rubrics:
        raise NotFoundError(f"No rubric family {family_id}.")
    if program_id is not None and await session.get(Program, program_id) is None:
        raise NotFoundError(f"No program with id {program_id}.")
    await session.execute(
        update(Rubric).where(Rubric.rubric_family_id == family_id).values(program_id=program_id)
    )
    await session.commit()
    return await list_rubric_versions(session, family_id, instructor_id)


async def delete_rubric(session: AsyncSession, rubric_id: int, instructor_id: int) -> None:
    """F2.4 AC: blocked (with an explanation) when reports are pinned to
    this version — history is immutable."""
    rubric = await get_rubric(session, rubric_id, instructor_id, for_update=True)
    if await _has_reports(session, rubric_id):
        raise ConflictError(
            "This version has reports based on it and can't be deleted. "
            "History stays available for comparability."
        )
    await session.delete(rubric)
    await session.commit()
