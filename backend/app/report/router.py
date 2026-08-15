"""Readiness report HTTP surface (screen 4h)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.models.instructor import Instructor
from app.report.export import build_report_pdf
from app.report.schemas import (
    DecisionIn,
    EscalatedItemOut,
    FlagSummaryOut,
    ReopenIn,
    ReportOut,
    ResolveEscalationIn,
    ResolveEscalationOut,
)
from app.report.service import (
    decide_report,
    get_report,
    get_report_export_data,
    list_escalated_for_run,
    list_flags_for_run,
    reopen_report,
    resolve_escalation_for_run,
)

router = APIRouter(tags=["report"])


@router.get("/check-runs/{check_run_id}/report", response_model=ReportOut)
async def get_report_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ReportOut:
    return await get_report(session, check_run_id, instructor.id)


@router.get("/check-runs/{check_run_id}/report/export.pdf")
async def export_report_pdf_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> Response:
    data = await get_report_export_data(session, check_run_id, instructor.id)
    pdf_bytes = build_report_pdf(data)
    filename = f"veridical-report-{check_run_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/check-runs/{check_run_id}/escalated", response_model=list[EscalatedItemOut])
async def list_escalated_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[EscalatedItemOut]:
    return await list_escalated_for_run(session, check_run_id, instructor.id)


@router.get("/check-runs/{check_run_id}/flags", response_model=list[FlagSummaryOut])
async def list_flags_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> list[FlagSummaryOut]:
    return await list_flags_for_run(session, check_run_id, instructor.id)


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


@router.post("/check-runs/{check_run_id}/decision", response_model=ReportOut)
async def decide_report_route(
    check_run_id: int,
    body: DecisionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ReportOut:
    return await decide_report(session, check_run_id, instructor.id, body.decision, body.note)


@router.post("/check-runs/{check_run_id}/reopen", response_model=ReportOut)
async def reopen_report_route(
    check_run_id: int,
    body: ReopenIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ReportOut:
    return await reopen_report(session, check_run_id, instructor.id, body.reason)
