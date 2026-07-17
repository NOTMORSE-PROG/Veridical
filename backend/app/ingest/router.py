"""Ingestion HTTP surface (V-008) — upload a manuscript, get the parsed
summary. Feeds screen 4f's states; taxonomy errors surface through the
app-wide exception handler as the structured envelope, never a bare 500.

HTTP only (CODING.md §2): validation and response shaping here, all logic
in service.py. Auth arrives with V-014.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages
from app.db import get_session
from app.ingest.schemas import IngestSummary
from app.ingest.service import ingest_upload

router = APIRouter(tags=["ingestion"])

# Streaming read size for uploads; purely an I/O buffer, not a policy knob.
_CHUNK_BYTES = 1024 * 1024


@router.post("/manuscripts/ingest", response_model=IngestSummary)
async def ingest_manuscript_upload(
    file: UploadFile,
    session: Annotated[AsyncSession, Depends(get_session)],
    group_label: str = "Ungrouped",
) -> IngestSummary:
    async def chunks():
        while chunk := await file.read(_CHUNK_BYTES):
            yield chunk

    manuscript, result, n_citations = await ingest_upload(
        session, chunks(), file.filename or "", group_label
    )
    notes = [messages.IMAGE_ONLY_NOTE] if result.image_only else []
    return IngestSummary(
        manuscript_id=manuscript.id,
        ingest_status=manuscript.ingest_status,
        page_count=result.page_count,
        anchor_kind=result.anchor_kind,
        image_only=result.image_only,
        text_chars=result.text_chars,
        images=len(result.images),
        tables=len(result.tables),
        equations=len(result.equations),
        vision_status=result.vision_status,
        citations=n_citations,
        section_tree=result.section_tree,
        notes=notes,
    )
