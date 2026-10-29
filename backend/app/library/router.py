"""Library HTTP surface (V-066, rebuilt as a standalone collection by V-077).

HTTP only (CODING.md §2): validation and response shaping here, all logic in
`service.py`. Every route keys a Library record by its manuscript id, the
same "#<id>" reference F7 findings already use for a matched source.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Path, Query, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor, get_upload_instructor
from app.config import get_settings
from app.db import DbInt, get_session
from app.library.schemas import (
    LibraryDecisionIn,
    LibraryEntryStateOut,
    LibraryEntryUpdate,
    LibraryExcerptOut,
    LibraryItemOut,
    LibrarySubmissionIn,
    LibrarySubmissionOut,
    PaginatedLibrary,
)
from app.library.service import (
    LibraryListScope,
    decide_library_entry,
    get_library_document,
    get_library_docx_image,
    get_library_excerpt,
    get_library_file_path,
    get_library_item,
    get_library_original_file,
    get_library_paragraphs,
    list_library,
    remove_library_entry,
    submit_library_entry,
    update_library_entry,
)
from app.models.instructor import Instructor
from app.ratelimit import enforce_action_rate_limit
from app.report.schemas import DocumentParagraphsOut, ManuscriptViewerOut

router = APIRouter(tags=["library"])

# Streaming read size for uploads; purely an I/O buffer, not a policy knob
# (same value and reason as `app.ingest.router`).
_CHUNK_BYTES = 1024 * 1024


def _submission_metadata(
    title: Annotated[str | None, Form()] = None,
    team_name: Annotated[str | None, Form()] = None,
    authors: Annotated[list[str] | None, Form()] = None,
    program: Annotated[str | None, Form()] = None,
    school_year: Annotated[str | None, Form()] = None,
) -> LibrarySubmissionIn:
    """Multipart form fields into the validated model; a too-long value is a
    422 like any other request validation error, never a 500."""
    try:
        return LibrarySubmissionIn(
            title=title,
            team_name=team_name,
            authors=authors or [],
            program=program,
            school_year=school_year,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


@router.get("/library", response_model=PaginatedLibrary)
async def list_library_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    # Bounded so an out-of-range page is a 422, not a 500 deep inside the
    # query.
    page: Annotated[DbInt, Query(ge=1, le=get_settings().list_max_page)] = 1,
    page_size: Annotated[DbInt, Query(ge=1, le=200)] = 50,
    program: str | None = None,
    search: str | None = None,
    scope: LibraryListScope = "active",
) -> PaginatedLibrary:
    return await list_library(
        session,
        instructor.id,
        page=page,
        page_size=page_size,
        program=program,
        search=search,
        scope=scope,
    )


@router.post("/library/entries", response_model=LibrarySubmissionOut)
async def submit_library_entry_route(
    file: UploadFile,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
    metadata: Annotated[LibrarySubmissionIn, Depends(_submission_metadata)],
) -> LibrarySubmissionOut:
    """One file per request (the client sends a batch one at a time). The
    outcome, not the status code, says whether it was added, is an exact copy
    of the requester's own entry, may duplicate one, or could not be read."""
    settings = get_settings()
    enforce_action_rate_limit(settings, "library_submission", instructor.id)

    async def chunks():
        while chunk := await file.read(_CHUNK_BYTES):
            yield chunk

    return await submit_library_entry(
        session,
        chunks(),
        file.filename or "",
        metadata,
        instructor_id=instructor.id,
        settings=settings,
    )


@router.get("/library/{manuscript_id}", response_model=LibraryItemOut)
async def get_library_item_route(
    manuscript_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> LibraryItemOut:
    return await get_library_item(session, instructor.id, manuscript_id)


@router.patch("/library/{manuscript_id}", response_model=LibraryItemOut)
async def update_library_entry_route(
    manuscript_id: DbInt,
    body: LibraryEntryUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> LibraryItemOut:
    return await update_library_entry(session, instructor.id, manuscript_id, body, get_settings())


@router.post("/library/{manuscript_id}/decision", response_model=LibraryEntryStateOut)
async def decide_library_entry_route(
    manuscript_id: DbInt,
    body: LibraryDecisionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
) -> LibraryEntryStateOut:
    return await decide_library_entry(
        session, instructor.id, manuscript_id, body.action, get_settings()
    )


@router.post("/library/{manuscript_id}/remove", response_model=LibraryEntryStateOut)
async def remove_library_entry_route(
    manuscript_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_upload_instructor)],
) -> LibraryEntryStateOut:
    return await remove_library_entry(session, instructor.id, manuscript_id, get_settings())


@router.get("/library/{manuscript_id}/excerpt", response_model=LibraryExcerptOut)
async def get_library_excerpt_route(
    manuscript_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> LibraryExcerptOut:
    """Bounded excerpt of any Library entry the requester can see (see
    `app.library.service.get_library_excerpt`)."""
    return await get_library_excerpt(session, instructor.id, manuscript_id, get_settings())


@router.get("/library/{manuscript_id}/document", response_model=ManuscriptViewerOut)
async def get_library_document_route(
    manuscript_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ManuscriptViewerOut:
    """Full reader for active Library entries (V-079); private uploads stay
    owner-only. Authentication and active membership authorize shared reads."""
    return await get_library_document(session, instructor.id, manuscript_id, get_settings())


@router.get("/library/{manuscript_id}/document/file")
async def get_library_file_route(
    manuscript_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FileResponse:
    path = await get_library_file_path(session, instructor.id, manuscript_id, get_settings())
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/library/{manuscript_id}/document/paragraphs", response_model=DocumentParagraphsOut)
async def get_library_paragraphs_route(
    manuscript_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> DocumentParagraphsOut:
    return await get_library_paragraphs(session, instructor.id, manuscript_id, get_settings())


@router.get("/library/{manuscript_id}/document/images/{image_index}")
async def get_library_docx_image_route(
    manuscript_id: DbInt,
    image_index: Annotated[DbInt, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> Response:
    settings = get_settings()
    enforce_action_rate_limit(settings, "docx_source_image", instructor.id)
    content, content_type = await get_library_docx_image(
        session, instructor.id, manuscript_id, image_index, settings
    )
    return Response(
        content,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/library/{manuscript_id}/document/original")
async def get_library_original_file_route(
    manuscript_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FileResponse:
    """Authenticated original download; a neutral name preserves record-field redaction."""
    path, source_format = await get_library_original_file(
        session, instructor.id, manuscript_id, get_settings()
    )
    suffix = f".{source_format}"
    media_type = (
        "application/pdf"
        if suffix == ".pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"library-record-{manuscript_id}{suffix}",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
