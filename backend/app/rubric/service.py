"""Rubric upload orchestration: file -> extraction -> decomposition ->
persisted rubric + criteria (F2.1, feeds the review screen 4c/4d), plus
versioning (F2.4, V-013): re-upload into an existing family, activate a
version, list families/versions, delete a version — all guarded so a
rubric with reports pinned to it is never silently mutated or dropped
(measurement integrity, TESTING.md §2).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.errors import ConflictError, NotFoundError
from app.ingest.service import detect_format, save_upload, select_extractor
from app.llm.base import LLMClient
from app.models.enums import CriterionType, RubricParseStatus
from app.models.group import Program
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckRun
from app.rubric.decompose import raw_text_for_decomposition
from app.rubric.schemas import RubricListItem, UpdateCriteriaRequest
from app.rubric.validate import attempt_decomposition


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
    """`family_id=None` (V-010 default) starts a brand-new family at
    version 1. `family_id` set (V-013, "Upload new format" on screen 4m)
    adds the next version to an EXISTING family — old versions, and
    anything pinned to them, are untouched. `instructor_id` is the
    authenticated caller (BUG-001/D-020) — a family_id owned by a
    different instructor reads as NotFoundError, same as an unknown one."""
    settings = settings or get_settings()

    version = 1
    # V-064 (`backend-critic` finding, P1, live-reproduced): a new version
    # uploaded into an existing family used to always start at
    # `program_id=None`, silently reverting a family that had a program
    # set back to "eligible for everything" the moment this new version
    # was activated -- no error, nothing to notice. Every sibling shares
    # the same `program_id` (kept in sync by `set_rubric_family_program`,
    # which updates every row at once), so any one of them names the
    # family's current value; carried forward here so a routine "upload a
    # corrected format" action can never silently undo program scoping.
    family_program_id: int | None = None
    if family_id is not None:
        siblings = (
            await session.scalars(
                select(Rubric).where(
                    Rubric.rubric_family_id == family_id, Rubric.instructor_id == instructor_id
                )
            )
        ).all()
        if not siblings:
            raise NotFoundError(f"No rubric family {family_id}.")
        version = max(r.version for r in siblings) + 1
        family_program_id = siblings[0].program_id

    # The row exists (and has its id) before the file does — same pattern
    # as manuscript ingestion: the server-owned id names the stored file,
    # never the user-supplied filename.
    rubric = Rubric(
        instructor_id=instructor_id,
        title=title,
        source_file="",
        program_id=family_program_id,
        **({"rubric_family_id": family_id, "version": version} if family_id is not None else {}),
    )
    session.add(rubric)
    await session.commit()
    await session.refresh(rubric)

    suffix = Path(filename).suffix.lower()[:8]
    dest = settings.data_dir / "rubric_uploads" / f"{rubric.id}{suffix}"
    try:
        await save_upload(chunks, dest, settings)
        rubric.source_file = str(dest)
        await session.commit()

        extractor = select_extractor(detect_format(dest))
        loop = asyncio.get_running_loop()
        # Extraction is CPU-bound — off the event loop (CODING.md §2), same
        # as manuscript ingestion.
        result = await loop.run_in_executor(None, extractor, str(dest), settings)
    except Exception:
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
        # `backend-critic` finding: if the exception that landed us here
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


async def get_rubric(session: AsyncSession, rubric_id: int, instructor_id: int) -> Rubric:
    """Row-level authorized (BUG-001/D-020): a rubric owned by a different
    instructor reads as NotFoundError, identical to an unknown id — no
    existence leak, same pattern as `_owned_check_run` in
    `app/report/service.py`."""
    rubric = await session.scalar(
        select(Rubric)
        .where(Rubric.id == rubric_id, Rubric.instructor_id == instructor_id)
        .options(selectinload(Rubric.criteria))
    )
    if rubric is None:
        raise NotFoundError(f"No rubric with id {rubric_id}.")
    max_version = await session.scalar(
        select(func.max(Rubric.version)).where(Rubric.rubric_family_id == rubric.rubric_family_id)
    )
    # Not an ORM column — attached so RubricOut.model_validate(rubric,
    # from_attributes=True) can read it like any other field (CODING.md
    # §2: routers stay thin, shaping stays here).
    rubric.is_latest_version = max_version == rubric.version  # type: ignore[attr-defined]
    rubric.program = await _program_name(session, rubric.program_id)  # type: ignore[attr-defined]
    return rubric


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
    rubric = await get_rubric(session, rubric_id, instructor_id)
    if rubric.is_active and await _has_reports(session, rubric_id):
        raise ConflictError(
            "This version has reports based on it and can no longer be edited. "
            "Upload a new version instead (old reports stay pinned to this one)."
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
        if criterion_in.id is None:
            session.add(
                Criterion(
                    rubric_id=rubric_id,
                    type=CriterionType(criterion_in.type),
                    text=criterion_in.text,
                    evidence=criterion_in.evidence,
                    weight=Decimal(str(criterion_in.weight)),
                    position=position,
                )
            )
        else:
            row = existing[criterion_in.id]
            row.type = CriterionType(criterion_in.type)
            row.text = criterion_in.text
            row.evidence = criterion_in.evidence
            row.weight = Decimal(str(criterion_in.weight))
            row.position = position

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
    return await get_rubric(session, rubric_id, instructor_id)


async def activate_rubric(session: AsyncSession, rubric_id: int, instructor_id: int) -> Rubric:
    """Screen 4m's "Activate" action — switch which EXISTING version is
    active without touching its criteria (unlike confirm, which follows
    an edit)."""
    rubric = await get_rubric(session, rubric_id, instructor_id)
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
    rubric = await get_rubric(session, rubric_id, instructor_id)
    if await _has_reports(session, rubric_id):
        raise ConflictError(
            "This version has reports based on it and can't be deleted. "
            "History stays available for comparability."
        )
    await session.delete(rubric)
    await session.commit()
