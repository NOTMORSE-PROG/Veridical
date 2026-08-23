"""Originality/reuse (F7) archive HTTP surface (V-042, screen 4t)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.archive.schemas import PaginatedArchive, PurgeOut
from app.archive.service import list_archive, purge_manuscript
from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.models.instructor import Instructor

router = APIRouter(tags=["archive"])


@router.get("/archive", response_model=PaginatedArchive)
async def list_archive_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    # security-auditor (V-066 review, 2026-08-23): an out-of-range
    # page/page_size crashed as a bare 500 -- reproduced live, pre-existing
    # since this endpoint shipped. Same bound as `/library`'s own fix.
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaginatedArchive:
    return await list_archive(session, instructor.id, page=page, page_size=page_size)


@router.delete("/archive/{manuscript_id}", response_model=PurgeOut)
async def purge_manuscript_route(
    manuscript_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> PurgeOut:
    return await purge_manuscript(session, instructor.id, manuscript_id, get_settings())
