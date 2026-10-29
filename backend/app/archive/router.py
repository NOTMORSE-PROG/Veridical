"""Originality/reuse (F7) archive HTTP surface (V-042, screen 4t)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.archive.schemas import PaginatedArchive, PurgeOut, SourceAvailabilityOut
from app.archive.service import list_archive, purge_manuscript, source_availability
from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import DbInt, get_session
from app.models.instructor import Instructor

router = APIRouter(tags=["archive"])
_LIST_MAX_PAGE = get_settings().list_max_page
_LIST_MAX_PAGE_SIZE = get_settings().list_max_page_size


@router.get("/archive/{manuscript_id}/availability", response_model=SourceAvailabilityOut)
async def source_availability_route(
    manuscript_id: DbInt,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> SourceAvailabilityOut:
    response.headers["Cache-Control"] = "no-store"
    return await source_availability(session, instructor.id, manuscript_id, get_settings())


@router.get("/archive", response_model=PaginatedArchive)
async def list_archive_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    # An out-of-range page/page_size used to crash as a bare 500 inside the
    # query. Same bounds as `/library`.
    page: Annotated[DbInt, Query(ge=1, le=_LIST_MAX_PAGE)] = 1,
    page_size: Annotated[DbInt, Query(ge=1, le=_LIST_MAX_PAGE_SIZE)] = 50,
) -> PaginatedArchive:
    return await list_archive(session, instructor.id, page=page, page_size=page_size)


@router.delete("/archive/{manuscript_id}", response_model=PurgeOut)
async def purge_manuscript_route(
    manuscript_id: DbInt,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> PurgeOut:
    return await purge_manuscript(session, instructor.id, manuscript_id, get_settings())
