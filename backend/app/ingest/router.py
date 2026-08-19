"""Ingestion HTTP surface (V-008) — upload a manuscript, get the parsed
summary. Feeds screen 4f's states; taxonomy errors surface through the
app-wide exception handler as the structured envelope, never a bare 500.

HTTP only (CODING.md §2): validation and response shaping here, all logic
in service.py. Auth arrives with V-014.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages
from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.groups.service import DEFAULT_GROUP_LABEL
from app.ingest.schemas import IngestSummary, PaginatedManuscripts
from app.ingest.service import ingest_upload, list_manuscripts
from app.models.instructor import Instructor
from app.ratelimit import enforce_action_rate_limit

router = APIRouter(tags=["ingestion"])

# Streaming read size for uploads; purely an I/O buffer, not a policy knob.
_CHUNK_BYTES = 1024 * 1024


@router.post("/manuscripts/ingest", response_model=IngestSummary)
async def ingest_manuscript_upload(
    file: UploadFile,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    # BUG-043: a bare scalar is a QUERY parameter in FastAPI, even on a
    # multipart endpoint, and silently dropped a form-field `group_label`
    # with no error. `Form()` reads it from the multipart body instead.
    # The query param is still accepted for one release so an in-flight
    # client isn't broken mid-deploy; the form field wins when both are
    # sent, since it's the one that doesn't leak group identity into logs.
    group_label: Annotated[str | None, Form()] = None,
    group_label_query: Annotated[str | None, Query(alias="group_label")] = None,
) -> IngestSummary:
    resolved_group_label = group_label or group_label_query or DEFAULT_GROUP_LABEL

    async def chunks():
        while chunk := await file.read(_CHUNK_BYTES):
            yield chunk

    settings = get_settings()
    enforce_action_rate_limit(settings, "manuscript_ingest", instructor.id)
    manuscript, result, n_citations = await ingest_upload(
        session, chunks(), file.filename or "", resolved_group_label, instructor_id=instructor.id
    )
    notes = [messages.IMAGE_ONLY_NOTE] if result.image_only else []
    return IngestSummary(
        manuscript_id=manuscript.id,
        group_label=manuscript.group_label,
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


@router.get("/manuscripts", response_model=PaginatedManuscripts)
async def list_manuscripts_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    page: int = 1,
    page_size: int = 50,
    # V-062 (AC5): filters to manuscripts whose group has this program.
    # `UNSET_PROGRAM_FILTER` ("__unset__") asks for manuscripts with no
    # program set instead -- a real filter state, not an unreachable one.
    program: str | None = None,
) -> PaginatedManuscripts:
    return await list_manuscripts(
        session, instructor.id, page=page, page_size=page_size, program=program
    )
