"""Flag evidence/annotation/override HTTP surface (F8.2/F8.4, screen 4i)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.flags.schemas import (
    AnnotateFlagIn,
    ConfirmCitationSourceIn,
    ConfirmCitationSourceOut,
    FlagOut,
    OverrideFlagIn,
    OverrideFlagOut,
)
from app.flags.service import annotate_flag, confirm_citation_source, get_flag, override_flag
from app.models.instructor import Instructor
from app.report.service import get_report

router = APIRouter(prefix="/flags", tags=["flags"])


@router.get("/{flag_id}", response_model=FlagOut)
async def get_flag_route(
    flag_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FlagOut:
    return await get_flag(session, flag_id, instructor.id)


@router.post("/{flag_id}/annotate", response_model=FlagOut)
async def annotate_flag_route(
    flag_id: int,
    body: AnnotateFlagIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FlagOut:
    return await annotate_flag(session, flag_id, instructor.id, body.annotation)


@router.post("/{flag_id}/override", response_model=OverrideFlagOut)
async def override_flag_route(
    flag_id: int,
    body: OverrideFlagIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> OverrideFlagOut:
    flag_out, check_run_id = await override_flag(session, flag_id, instructor.id, body.reason)
    report = await get_report(session, check_run_id, instructor.id)
    return OverrideFlagOut(**flag_out.model_dump(), report=report)


@router.post("/{flag_id}/confirm-source", response_model=ConfirmCitationSourceOut)
async def confirm_citation_source_route(
    flag_id: int,
    body: ConfirmCitationSourceIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ConfirmCitationSourceOut:
    flag_out, check_run_id = await confirm_citation_source(
        session, flag_id, instructor.id, body.reason
    )
    report = await get_report(session, check_run_id, instructor.id)
    return ConfirmCitationSourceOut(**flag_out.model_dump(), report=report)
