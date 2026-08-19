"""Groups/programs HTTP surface (V-062) — read-only today: `program` rows
are seeded once (migration 0025) and `Group` rows are created implicitly
by `POST /manuscripts/ingest`'s resolve-or-create, never through a
dedicated create endpoint (no management screen exists yet)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.groups.schemas import GroupOut, ProgramOut
from app.groups.service import list_groups, list_programs
from app.models.instructor import Instructor

router = APIRouter(tags=["groups"])


@router.get("/programs", response_model=list[ProgramOut])
async def list_programs_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    # Auth-only: every instructor sees the same program list (it isn't
    # instructor-scoped data), but this is still an authenticated route
    # like every other manuscript-adjacent endpoint (BUG-002/D-020).
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[ProgramOut]:
    programs = await list_programs(session)
    return [ProgramOut.model_validate(p) for p in programs]


@router.get("/groups", response_model=list[GroupOut])
async def list_groups_route(
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[GroupOut]:
    rows = await list_groups(session, instructor.id)
    return [
        GroupOut(id=group.id, name=group.name, program=program_name) for group, program_name in rows
    ]
