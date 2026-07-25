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
from app.models.instructor import Instructor
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckRun
from app.rubric.decompose import raw_text_for_decomposition
from app.rubric.schemas import RubricListItem, UpdateCriteriaRequest
from app.rubric.validate import attempt_decomposition


async def _get_or_create_demo_instructor(session: AsyncSession) -> Instructor:
    """Uploads attach to the demo instructor until auth lands (V-014) —
    same convention as manuscript ingestion (app/ingest/service.py)."""
    instructor = await session.scalar(select(Instructor).order_by(Instructor.id).limit(1))
    if instructor is None:
        instructor = Instructor(email="instructor@demo.local", display_name="Demo Instructor")
        session.add(instructor)
        await session.commit()
    return instructor


async def create_rubric_from_upload(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    title: str,
    llm: LLMClient,
    settings: Settings | None = None,
    family_id: uuid.UUID | None = None,
) -> Rubric:
    """`family_id=None` (V-010 default) starts a brand-new family at
    version 1. `family_id` set (V-013, "Upload new format" on screen 4m)
    adds the next version to an EXISTING family — old versions, and
    anything pinned to them, are untouched."""
    settings = settings or get_settings()
    instructor = await _get_or_create_demo_instructor(session)

    version = 1
    if family_id is not None:
        siblings = (
            await session.scalars(select(Rubric).where(Rubric.rubric_family_id == family_id))
        ).all()
        if not siblings:
            raise NotFoundError(f"No rubric family {family_id}.")
        version = max(r.version for r in siblings) + 1

    # The row exists (and has its id) before the file does — same pattern
    # as manuscript ingestion: the server-owned id names the stored file,
    # never the user-supplied filename.
    rubric = Rubric(
        instructor_id=instructor.id,
        title=title,
        source_file="",
        **({"rubric_family_id": family_id, "version": version} if family_id is not None else {}),
    )
    session.add(rubric)
    await session.commit()
    await session.refresh(rubric)

    suffix = Path(filename).suffix.lower()[:8]
    dest = settings.data_dir / "rubric_uploads" / f"{rubric.id}{suffix}"
    await save_upload(chunks, dest, settings)
    rubric.source_file = str(dest)
    await session.commit()

    extractor = select_extractor(detect_format(dest))
    loop = asyncio.get_running_loop()
    # Extraction is CPU-bound — off the event loop (CODING.md §2), same as
    # manuscript ingestion.
    result = await loop.run_in_executor(None, extractor, str(dest), settings)
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


async def get_rubric(session: AsyncSession, rubric_id: int) -> Rubric:
    rubric = await session.scalar(
        select(Rubric).where(Rubric.id == rubric_id).options(selectinload(Rubric.criteria))
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
    return rubric


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
    session: AsyncSession, rubric_id: int, body: UpdateCriteriaRequest
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
    rubric = await get_rubric(session, rubric_id)
    if rubric.is_active and await _has_reports(session, rubric_id):
        raise ConflictError(
            "This version has reports based on it and can no longer be edited — "
            "upload a new version instead (old reports stay pinned to this one)."
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
        # A confirmed rubric is, by definition, no longer "needs manual
        # completion" — the instructor's review IS the resolution (HITL).
        await _activate_only(session, rubric)
        rubric.parse_status = RubricParseStatus.parsed
        rubric.parse_issues = None

    await session.commit()
    return await get_rubric(session, rubric_id)


async def activate_rubric(session: AsyncSession, rubric_id: int) -> Rubric:
    """Screen 4m's "Activate" action — switch which EXISTING version is
    active without touching its criteria (unlike confirm, which follows
    an edit)."""
    rubric = await get_rubric(session, rubric_id)
    await _activate_only(session, rubric)
    await session.commit()
    return await get_rubric(session, rubric_id)


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
    )


async def list_rubric_versions(session: AsyncSession, family_id: uuid.UUID) -> list[RubricListItem]:
    rubrics = (
        await session.scalars(
            select(Rubric)
            .where(Rubric.rubric_family_id == family_id)
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


async def delete_rubric(session: AsyncSession, rubric_id: int) -> None:
    """F2.4 AC: blocked (with an explanation) when reports are pinned to
    this version — history is immutable."""
    rubric = await get_rubric(session, rubric_id)
    if await _has_reports(session, rubric_id):
        raise ConflictError(
            "This version has reports based on it and can't be deleted — "
            "history stays available for comparability."
        )
    await session.delete(rubric)
    await session.commit()
