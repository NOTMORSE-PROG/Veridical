"""Rubric HTTP surface (F2.1/F2.3/F2.4, screens 4c/4d/4m)."""

import uuid
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor, get_upload_instructor
from app.checks.citations.recency import CitationRecencyFormatSettings
from app.config import get_settings
from app.db import DbInt, DbText, get_session
from app.errors import FormatAIConfigurationError
from app.llm import LLMNotConfiguredError, get_llm_client_for
from app.models.instructor import Instructor
from app.ratelimit import enforce_action_rate_limit
from app.rubric.documents import (
    RubricDocumentOut,
    RubricParagraphsOut,
    document_file,
    document_image,
    document_metadata,
    document_paragraphs,
    read_rubric_document,
)
from app.rubric.schemas import (
    CitationRecencySettingsOut,
    RubricListItem,
    RubricOut,
    SetRubricFamilyProgramRequest,
    UpdateCriteriaRequest,
)
from app.rubric.service import (
    activate_rubric,
    create_rubric_from_upload,
    delete_rubric,
    get_rubric,
    list_rubric_families,
    list_rubric_versions,
    set_rubric_family_citation_recency,
    set_rubric_family_program,
    update_criteria,
)

router = APIRouter(tags=["rubric"])
_PRIVATE_SOURCE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}
SourceHash = Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")]

# Streaming read size for uploads — an I/O buffer, not a policy knob
# (matches app/ingest/router.py).
_CHUNK_BYTES = 1024 * 1024


@router.get("/rubrics/{rubric_id}/document", response_model=RubricDocumentOut)
async def rubric_document_route(
    rubric_id: DbInt,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> RubricDocumentOut:
    response.headers.update(_PRIVATE_SOURCE_HEADERS)
    return await read_rubric_document(
        session, rubric_id, instructor.id, get_settings(), document_metadata
    )


@router.get("/rubrics/{rubric_id}/document/paragraphs", response_model=RubricParagraphsOut)
async def rubric_paragraphs_route(
    rubric_id: DbInt,
    source_sha256: SourceHash,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> RubricParagraphsOut:
    response.headers.update(_PRIVATE_SOURCE_HEADERS)
    return await read_rubric_document(
        session,
        rubric_id,
        instructor.id,
        get_settings(),
        partial(document_paragraphs, expected_hash=source_sha256),
    )


@router.get("/rubrics/{rubric_id}/document/file")
async def rubric_pdf_route(
    rubric_id: DbInt,
    source_sha256: SourceHash,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FileResponse:
    path, mime, _ = await read_rubric_document(
        session,
        rubric_id,
        instructor.id,
        get_settings(),
        partial(document_file, expected_hash=source_sha256, pdf_only=True),
    )
    return FileResponse(path, media_type=mime, headers=_PRIVATE_SOURCE_HEADERS)


@router.get("/rubrics/{rubric_id}/document/original")
async def rubric_original_route(
    rubric_id: DbInt,
    source_sha256: SourceHash,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FileResponse:
    path, mime, filename = await read_rubric_document(
        session,
        rubric_id,
        instructor.id,
        get_settings(),
        partial(document_file, expected_hash=source_sha256),
    )
    return FileResponse(path, media_type=mime, filename=filename, headers=_PRIVATE_SOURCE_HEADERS)


@router.get("/rubrics/{rubric_id}/document/images/{image_index}")
async def rubric_image_route(
    rubric_id: DbInt,
    image_index: Annotated[DbInt, PathParam(ge=0)],
    source_sha256: SourceHash,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> Response:
    settings = get_settings()
    enforce_action_rate_limit(settings, "docx_source_image", instructor.id)
    content, mime = await read_rubric_document(
        session,
        rubric_id,
        instructor.id,
        settings,
        partial(document_image, expected_hash=source_sha256, image_index=image_index),
    )
    return Response(content, media_type=mime, headers=_PRIVATE_SOURCE_HEADERS)


@router.post("/rubrics", response_model=RubricOut)
async def upload_rubric(
    file: UploadFile,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
    title: DbText = "Untitled rubric",
    family_id: uuid.UUID | None = None,
) -> RubricOut:
    async def chunks():
        while chunk := await file.read(_CHUNK_BYTES):
            yield chunk

    settings = get_settings()
    enforce_action_rate_limit(settings, "rubric_upload", instructor.id)
    try:
        llm = await get_llm_client_for(session, settings, instructor.id)
    except LLMNotConfiguredError:
        # Only this boundary proves the attempt has not started creation.
        raise FormatAIConfigurationError() from None
    rubric = await create_rubric_from_upload(
        session,
        chunks(),
        file.filename or "",
        title,
        llm,
        settings,
        instructor_id=instructor.id,
        family_id=family_id,
    )
    return RubricOut.model_validate(await get_rubric(session, rubric.id, instructor.id))


@router.get("/rubrics/{rubric_id}", response_model=RubricOut)
async def get_rubric_route(
    rubric_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> RubricOut:
    rubric = await get_rubric(session, rubric_id, instructor.id)
    return RubricOut.model_validate(rubric)


@router.put("/rubrics/{rubric_id}/criteria", response_model=RubricOut)
async def put_rubric_criteria(
    rubric_id: DbInt,
    body: UpdateCriteriaRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> RubricOut:
    rubric = await update_criteria(session, rubric_id, body, instructor.id)
    return RubricOut.model_validate(rubric)


@router.post("/rubrics/{rubric_id}/activate", response_model=RubricOut)
async def activate_rubric_route(
    rubric_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> RubricOut:
    rubric = await activate_rubric(session, rubric_id, instructor.id)
    return RubricOut.model_validate(rubric)


@router.delete("/rubrics/{rubric_id}", status_code=204)
async def delete_rubric_route(
    rubric_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> None:
    await delete_rubric(session, rubric_id, instructor.id)


@router.get("/rubric-families", response_model=list[RubricListItem])
async def list_rubric_families_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[RubricListItem]:
    return await list_rubric_families(session, instructor.id)


@router.get("/rubric-families/{family_id}/versions", response_model=list[RubricListItem])
async def list_rubric_versions_route(
    family_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[RubricListItem]:
    return await list_rubric_versions(session, family_id, instructor.id)


@router.put("/rubric-families/{family_id}/program", response_model=list[RubricListItem])
async def set_rubric_family_program_route(
    family_id: uuid.UUID,
    body: SetRubricFamilyProgramRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[RubricListItem]:
    return await set_rubric_family_program(session, family_id, body.program_id, instructor.id)


@router.put(
    "/rubric-families/{family_id}/citation-recency", response_model=CitationRecencySettingsOut
)
async def set_rubric_family_citation_recency_route(
    family_id: uuid.UUID,
    body: CitationRecencyFormatSettings,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> CitationRecencySettingsOut:
    return await set_rubric_family_citation_recency(session, family_id, body, instructor.id)
