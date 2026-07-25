"""Readiness report HTTP surface (screen 4h)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.models.instructor import Instructor
from app.report.schemas import (
    EscalatedItemOut,
    ReportOut,
    ResolveEscalationIn,
    ResolveEscalationOut,
)
from app.report.service import get_report, list_escalated_for_run, resolve_escalation_for_run

router = APIRouter(tags=["report"])


@router.get("/check-runs/{check_run_id}/report", response_model=ReportOut)
async def get_report_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ReportOut:
    return await get_report(session, check_run_id, instructor.id)


@router.get("/check-runs/{check_run_id}/escalated", response_model=list[EscalatedItemOut])
async def list_escalated_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[EscalatedItemOut]:
    return await list_escalated_for_run(session, check_run_id, instructor.id)


@router.post(
    "/check-runs/{check_run_id}/escalated/{check_result_id}/resolve",
    response_model=ResolveEscalationOut,
)
async def resolve_escalation_route(
    check_run_id: int,
    check_result_id: int,
    body: ResolveEscalationIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ResolveEscalationOut:
    return await resolve_escalation_for_run(
        session, check_run_id, check_result_id, instructor.id, body.resolution, body.reason
    )
