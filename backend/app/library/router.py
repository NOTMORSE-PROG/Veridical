"""Library HTTP surface (V-066, new screen)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.library.schemas import LibraryExcerptOut, LibraryItemOut, PaginatedLibrary
from app.library.service import (
    get_library_document,
    get_library_excerpt,
    get_library_file_path,
    get_library_item,
    get_library_paragraphs,
    list_library,
)
from app.models.instructor import Instructor
from app.report.schemas import DocumentParagraphsOut, ManuscriptViewerOut

router = APIRouter(tags=["library"])


@router.get("/library", response_model=PaginatedLibrary)
async def list_library_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    # security-auditor (V-066 review): an out-of-range page/page_size
    # crashed as a bare 500 (pre-existing pattern this router inherited
    # from `/archive`, reproduced there too, not new to this diff) --
    # bounded here rather than left to raise deep inside the query. 200
    # matches this codebase's existing "generously large page" convention
    # (`useManuscripts`'s own page_size=200, ingest/useCheckRun.ts).
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    program: str | None = None,
    search: str | None = None,
) -> PaginatedLibrary:
    return await list_library(
        session, instructor.id, page=page, page_size=page_size, program=program, search=search
    )


@router.get("/library/{manuscript_id}", response_model=LibraryItemOut)
async def get_library_item_route(
    manuscript_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> LibraryItemOut:
    return await get_library_item(session, instructor.id, manuscript_id)


@router.get("/library/{manuscript_id}/excerpt", response_model=LibraryExcerptOut)
async def get_library_excerpt_route(
    manuscript_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> LibraryExcerptOut:
    """Q2's bounded-excerpt path -- available for ANY manuscript in the
    corpus, own or not (see `app.library.service.get_library_excerpt`
    docstring for why this one carries no ownership check)."""
    del instructor  # authenticated, but this route's data isn't ownership-gated
    return await get_library_excerpt(session, manuscript_id, get_settings())


@router.get("/library/{manuscript_id}/document", response_model=ManuscriptViewerOut)
async def get_library_document_route(
    manuscript_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ManuscriptViewerOut:
    """Own manuscripts only (404 otherwise, same convention as every other
    ownership check in this app) -- Q2 forbids the full document for
    another instructor's manuscript outright; `/excerpt` above is that
    path's only route."""
    return await get_library_document(session, instructor.id, manuscript_id, get_settings())


@router.get("/library/{manuscript_id}/document/file")
async def get_library_file_route(
    manuscript_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FileResponse:
    path = await get_library_file_path(session, instructor.id, manuscript_id)
    return FileResponse(path, media_type="application/pdf")


@router.get("/library/{manuscript_id}/document/paragraphs", response_model=DocumentParagraphsOut)
async def get_library_paragraphs_route(
    manuscript_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> DocumentParagraphsOut:
    return await get_library_paragraphs(session, instructor.id, manuscript_id, get_settings())
