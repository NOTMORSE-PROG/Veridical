"""Readiness report HTTP surface (screen 4h)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.models.instructor import Instructor
from app.report.schemas import ReportOut
from app.report.service import get_report

router = APIRouter(tags=["report"])


@router.get("/check-runs/{check_run_id}/report", response_model=ReportOut)
async def get_report_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ReportOut:
    return await get_report(session, check_run_id, instructor.id)
