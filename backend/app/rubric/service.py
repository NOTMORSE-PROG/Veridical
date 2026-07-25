"""Rubric upload orchestration: file -> extraction -> decomposition ->
persisted rubric + criteria (F2.1, feeds the review screen 4c/4d).

Versioning (a re-upload becoming v2 of the same family, F2.4) is V-013's
job — every upload here starts a brand-new family at version 1.
"""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.errors import NotFoundError
from app.ingest.service import detect_format, save_upload, select_extractor
from app.llm.base import LLMClient
from app.models.enums import CriterionType, RubricParseStatus
from app.models.instructor import Instructor
from app.models.rubric import Criterion, Rubric
from app.rubric.decompose import raw_text_for_decomposition
from app.rubric.schemas import UpdateCriteriaRequest
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
) -> Rubric:
    settings = settings or get_settings()
    instructor = await _get_or_create_demo_instructor(session)

    # The row exists (and has its id) before the file does — same pattern
    # as manuscript ingestion: the server-owned id names the stored file,
    # never the user-supplied filename.
    rubric = Rubric(instructor_id=instructor.id, title=title, source_file="")
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
    return rubric


async def update_criteria(
    session: AsyncSession, rubric_id: int, body: UpdateCriteriaRequest
) -> Rubric:
    """Screen 4d's Save/Confirm action (F2.3): replaces the full criteria
    set (edits, additions, deletions) in one transaction, and — only when
    the instructor explicitly confirms — activates the rubric. Nothing
    runs against a rubric before that flip (charter rule 1: the human
    confirms the parse)."""
    rubric = await get_rubric(session, rubric_id)
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
        rubric.is_active = True
        rubric.parse_status = RubricParseStatus.parsed
        rubric.parse_issues = None

    await session.commit()
    return await get_rubric(session, rubric_id)
