"""Rubric upload orchestration: file -> extraction -> decomposition ->
persisted rubric + criteria (F2.1, feeds the review screen 4c/4d).

Versioning (a re-upload becoming v2 of the same family, F2.4) is V-013's
job — every upload here starts a brand-new family at version 1.
"""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.ingest.service import detect_format, save_upload, select_extractor
from app.llm.base import LLMClient
from app.models.enums import CriterionType
from app.models.instructor import Instructor
from app.models.rubric import Criterion, Rubric
from app.rubric.decompose import decompose_rubric, raw_text_for_decomposition


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

    parsed = await decompose_rubric(raw_text, llm)

    session.add_all(
        Criterion(
            rubric_id=rubric.id,
            type=CriterionType(criterion.type),
            text=criterion.text,
            evidence=criterion.evidence_needed,
            weight=Decimal(str(criterion.weight)),
            position=position,
        )
        for position, criterion in enumerate(parsed)
    )
    await session.commit()
    await session.refresh(rubric, attribute_names=["criteria"])
    return rubric
