"""Readiness report HTTP surface (screen 4h)."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.models.instructor import Instructor
from app.report.export import build_report_pdf
from app.report.schemas import (
    DecisionIn,
    DocumentParagraphsOut,
    EscalatedItemOut,
    FlagSummaryOut,
    ManuscriptViewerOut,
    ReopenIn,
    ReportOut,
    ResolveEscalationIn,
    ResolveEscalationOut,
    ReuseMatchesOut,
)
from app.report.service import (
    decide_report,
    get_manuscript_file_path,
    get_manuscript_paragraphs,
    get_manuscript_viewer,
    get_report,
    get_report_export_data,
    list_escalated_for_run,
    list_flags_for_run,
    list_reuse_passage_matches,
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
    # V-070 made this meaningfully heavier (page rendering already happened
    # in `get_report_export_data`; this is now reportlab layout PLUS
    # embedding those images) -- offload the CPU-bound build off the event
    # loop rather than blocking VERIDICAL's single uvicorn worker for its
    # duration (CODING.md's PyMuPDF/embeddings rule).
    loop = asyncio.get_running_loop()
    pdf_bytes = await loop.run_in_executor(None, build_report_pdf, data)
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


@router.get("/check-runs/{check_run_id}/document", response_model=ManuscriptViewerOut)
async def get_manuscript_viewer_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ManuscriptViewerOut:
    """V-065 AC1/2/7: the manuscript-viewer screen's metadata + per-flag
    anchor regions. Split from the raw file bytes below (ui-designer spec
    §2.2) — a streamed PDF has no business inside a JSON envelope."""
    return await get_manuscript_viewer(session, check_run_id, instructor.id)


@router.get("/check-runs/{check_run_id}/document/reuse-matches", response_model=ReuseMatchesOut)
async def list_reuse_passage_matches_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    include_reference_list: bool = False,
    include_block_quote: bool = False,
) -> ReuseMatchesOut:
    """V-072 (F7.4), `ui-designer` spec §4.2: the exclusion-toggle
    exploration panel's data source. Both toggles default to `False`
    (ticket AC3, "on by default") — matches this returns are never scored
    `Flag` rows regardless of what's passed here."""
    return await list_reuse_passage_matches(
        session,
        check_run_id,
        instructor.id,
        include_reference_list=include_reference_list,
        include_block_quote=include_block_quote,
    )


@router.get("/check-runs/{check_run_id}/document/paragraphs", response_model=DocumentParagraphsOut)
async def get_manuscript_paragraphs_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> DocumentParagraphsOut:
    """V-065 AC1 (DOCX gap): the reconstructed-text pane's actual
    paragraph content, split from `get_manuscript_viewer`'s metadata the
    same way the PDF file bytes are (`.../document/file` below) -- DOCX
    sources only, never a purged manuscript."""
    return await get_manuscript_paragraphs(session, check_run_id, instructor.id)


@router.get("/check-runs/{check_run_id}/document/file")
async def get_manuscript_file_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FileResponse:
    """PDF sources only -- the frontend's PDF.js pane fetches the actual
    submitted bytes here (V-065 Q1: never re-rasterized server-side). A
    purged manuscript raises `GoneError` (410), not a silent empty body."""
    path = await get_manuscript_file_path(session, check_run_id, instructor.id)
    return FileResponse(path, media_type="application/pdf")


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
        session,
        check_run_id,
        check_result_id,
        instructor.id,
        body.resolution,
        body.reason,
        level=body.level,
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
