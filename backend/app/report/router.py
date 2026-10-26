"""Readiness report HTTP surface (screen 4h)."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_instructor
from app.config import get_settings
from app.db import get_session
from app.models.instructor import Instructor
from app.ratelimit import enforce_action_rate_limit
from app.report.evidence_images import evidence_image_admission
from app.report.evidence_service import (
    EvidenceDocumentOut,
    EvidenceLocationOut,
    EvidenceManifestOut,
    EvidencePageOut,
    get_evidence_manifest,
    get_evidence_page,
    get_private_evidence_location,
    get_private_evidence_reader_source,
)
from app.report.export import build_report_pdf
from app.report.judgment_schemas import (
    JudgmentContextOut,
    JudgmentHistoryOut,
    JudgmentRecordIn,
    JudgmentRecordOut,
    JudgmentRevisionIn,
    JudgmentRevisionOut,
)
from app.report.judgments import (
    get_judgment_context,
    get_judgment_history,
    record_judgment,
    revise_judgment,
)
from app.report.page_text import PdfPageTextOut, get_manuscript_page_text
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
    attach_report_figures,
    decide_report,
    get_manuscript_docx_image,
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


@router.get("/check-runs/{check_run_id}/evidence", response_model=EvidenceManifestOut)
async def get_evidence_manifest_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> EvidenceManifestOut:
    return await get_evidence_manifest(
        session, get_settings(), check_run_id=check_run_id, instructor_id=instructor.id
    )


@router.get(
    "/check-runs/{check_run_id}/evidence/{reference_id}/page", response_model=EvidencePageOut
)
async def get_evidence_page_route(
    check_run_id: int,
    reference_id: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
    snapshot_id: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> EvidencePageOut:
    settings = get_settings()
    enforce_action_rate_limit(settings, "evidence_source", instructor.id)
    return await get_evidence_page(
        session,
        reference_id,
        snapshot_id,
        settings,
        check_run_id=check_run_id,
        instructor_id=instructor.id,
    )


@router.get(
    "/check-runs/{check_run_id}/evidence/{reference_id}/location",
    response_model=EvidenceLocationOut,
)
async def get_evidence_location_route(
    check_run_id: int,
    reference_id: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
    snapshot_id: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> EvidenceLocationOut:
    settings = get_settings()
    enforce_action_rate_limit(settings, "evidence_source", instructor.id)
    return await get_private_evidence_location(
        session, check_run_id, instructor.id, reference_id, snapshot_id, settings
    )


@router.get("/check-runs/{check_run_id}/evidence/source-file")
async def get_evidence_source_file_route(
    check_run_id: int,
    source_identity: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> Response:
    settings = get_settings()
    enforce_action_rate_limit(settings, "evidence_source", instructor.id)
    source = await get_private_evidence_reader_source(
        session, check_run_id, instructor.id, source_identity, settings
    )
    if source.source_format != "pdf" or source.pdf_bytes is None:
        from app.errors import ConflictError

        raise ConflictError("This source is not a PDF. Open its extracted manuscript text.")
    return Response(
        source.pdf_bytes,
        media_type="application/pdf",
        headers={
            "X-Evidence-Source-Identity": source.source_identity,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get(
    "/check-runs/{check_run_id}/evidence/source-document", response_model=EvidenceDocumentOut
)
async def get_evidence_source_document_route(
    check_run_id: int,
    source_identity: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> EvidenceDocumentOut:
    settings = get_settings()
    enforce_action_rate_limit(settings, "evidence_source", instructor.id)
    source = await get_private_evidence_reader_source(
        session, check_run_id, instructor.id, source_identity, settings
    )
    if source.source_format != "docx" or source.document is None:
        from app.errors import ConflictError

        raise ConflictError("This source is not DOCX. Open its original PDF pages.")
    return EvidenceDocumentOut(source_identity=source.source_identity, document=source.document)


@router.get(
    "/check-runs/{check_run_id}/criteria/{criterion_id}/judgment", response_model=JudgmentContextOut
)
async def judgment_context_route(
    check_run_id: int,
    criterion_id: int,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> JudgmentContextOut:
    response.headers["Cache-Control"] = "no-store"
    return await get_judgment_context(session, check_run_id, criterion_id, instructor.id)


@router.get(
    "/check-runs/{check_run_id}/criteria/{criterion_id}/judgment/history",
    response_model=JudgmentHistoryOut,
)
async def judgment_history_route(
    check_run_id: int,
    criterion_id: int,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
    expected_revision: Annotated[int, Query(ge=0)],
    expected_judgment_revision: Annotated[int, Query(ge=0)],
    page_size: Annotated[int | None, Query(ge=1)] = None,
    cursor: str | None = None,
) -> JudgmentHistoryOut:
    response.headers["Cache-Control"] = "no-store"
    return await get_judgment_history(
        session,
        check_run_id,
        criterion_id,
        instructor.id,
        expected_revision=expected_revision,
        expected_judgment_revision=expected_judgment_revision,
        page_size=page_size,
        cursor=cursor,
    )


@router.post(
    "/check-runs/{check_run_id}/criteria/{criterion_id}/judgment/revisions",
    response_model=JudgmentRevisionOut,
)
async def judgment_revision_route(
    check_run_id: int,
    criterion_id: int,
    body: JudgmentRevisionIn,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> JudgmentRevisionOut:
    response.headers["Cache-Control"] = "no-store"
    out = await revise_judgment(session, check_run_id, criterion_id, instructor.id, body)
    return out.model_copy(update={"report": await attach_report_figures(session, out.report)})


@router.post(
    "/check-runs/{check_run_id}/criteria/{criterion_id}/judgment/records",
    response_model=JudgmentRecordOut,
)
async def judgment_record_route(
    check_run_id: int,
    criterion_id: int,
    body: JudgmentRecordIn,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> JudgmentRecordOut:
    response.headers["Cache-Control"] = "no-store"
    out = await record_judgment(session, check_run_id, criterion_id, instructor.id, body)
    return out.model_copy(update={"report": await attach_report_figures(session, out.report)})


@router.get("/check-runs/{check_run_id}/report", response_model=ReportOut)
async def get_report_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ReportOut:
    report = await get_report(session, check_run_id, instructor.id)
    await session.commit()
    return await attach_report_figures(session, report)


@router.get("/check-runs/{check_run_id}/report/export.pdf")
async def export_report_pdf_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> Response:
    enforce_action_rate_limit(get_settings(), "report_export", instructor.id)
    with evidence_image_admission(get_settings()):
        data = await get_report_export_data(session, check_run_id, instructor.id)
        # Admission covers image collection and final layout. Executor work
        # cannot be forcibly stopped: drain before releasing this reservation.
        work = asyncio.create_task(asyncio.to_thread(build_report_pdf, data))
        try:
            pdf_bytes = await asyncio.shield(work)
        except asyncio.CancelledError:
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            # Retrieve the terminal exception without exposing it over a
            # cancelled response or leaving an unobserved task exception.
            if not work.cancelled():
                work.exception()
            raise
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
    anchor regions. Split from the raw file bytes below (design review spec
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
    """V-072 (F7.4), design review spec §4.2: the exclusion-toggle
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


@router.get("/check-runs/{check_run_id}/document/page-text", response_model=PdfPageTextOut)
async def get_manuscript_page_text_route(
    check_run_id: int,
    page: Annotated[int, Query(ge=1)],
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> PdfPageTextOut:
    settings = get_settings()
    enforce_action_rate_limit(settings, "pdf_page_text", instructor.id)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return await get_manuscript_page_text(session, check_run_id, instructor.id, page, settings)


@router.get("/check-runs/{check_run_id}/document/images/{image_index}")
async def get_manuscript_docx_image_route(
    check_run_id: int,
    image_index: Annotated[int, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> Response:
    settings = get_settings()
    enforce_action_rate_limit(settings, "docx_source_image", instructor.id)
    content, content_type = await get_manuscript_docx_image(
        session, check_run_id, instructor.id, image_index, settings
    )
    return Response(
        content,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/check-runs/{check_run_id}/document/file")
async def get_manuscript_file_route(
    check_run_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> FileResponse:
    """PDF sources only -- the frontend's PDF.js pane fetches the actual
    submitted bytes here (V-065 Q1: never re-rasterized server-side). A
    purged manuscript raises `GoneError` (410), not a silent empty body."""
    path = await get_manuscript_file_path(session, check_run_id, instructor.id, get_settings())
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


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
    out = await resolve_escalation_for_run(
        session,
        check_run_id,
        check_result_id,
        instructor.id,
        body.resolution,
        body.reason,
        level=body.level,
        expected_revision=body.expected_revision,
    )
    out.report = await attach_report_figures(session, out.report)
    return out


@router.post("/check-runs/{check_run_id}/decision", response_model=ReportOut)
async def decide_report_route(
    check_run_id: int,
    body: DecisionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ReportOut:
    report = await decide_report(
        session,
        check_run_id,
        instructor.id,
        body.decision,
        body.note,
        expected_revision=body.expected_revision,
    )
    return await attach_report_figures(session, report)


@router.post("/check-runs/{check_run_id}/reopen", response_model=ReportOut)
async def reopen_report_route(
    check_run_id: int,
    body: ReopenIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    instructor: Annotated[Instructor, Depends(get_current_instructor)],
) -> ReportOut:
    report = await reopen_report(
        session,
        check_run_id,
        instructor.id,
        body.reason,
        expected_revision=body.expected_revision,
    )
    return await attach_report_figures(session, report)
