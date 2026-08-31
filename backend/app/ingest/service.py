"""Ingestion orchestration: file → extraction → DB row + raw store.

The ingest stage of the pipeline (ENGINEERING.md §4). Extraction itself is
CPU-bound and runs in the default threadpool; this module owns the status
transitions on the manuscript row and the raw-store write.
"""

import asyncio
import zipfile
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages
from app.audit.service import write_audit_event
from app.config import Settings, get_settings
from app.errors import (
    ApiDownError,
    ConflictError,
    FileMalformedError,
    FileTooLargeError,
    NotFoundError,
    QuotaExhaustedError,
)
from app.groups.service import (
    UNSET_PROGRAM_FILTER,
    match_or_create_group_from_proposal,
    program_name_for,
    resolve_or_create_group,
)
from app.ingest import docx, pdf, references, vision
from app.ingest.patterns import load_patterns
from app.ingest.schemas import (
    ExtractionResult,
    ManuscriptListItem,
    ManuscriptQueueStatus,
    ManuscriptSort,
    PaginatedManuscripts,
)
from app.ingest.titlepage import AnchoredValue, TitlePageProposal, extract_title_page
from app.llm import LLMNotConfiguredError, get_llm_client_for
from app.models.citation import Citation
from app.models.enums import CheckRunStatus, IngestFailureReason, IngestStatus, ResultOutcome
from app.models.group import Group, GroupMember, Program
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckResult, CheckRun, ReadinessReport

# One extractor per supported suffix. Each is a sync callable executed off
# the event loop. Legacy .doc is deliberately absent: rejected with a clear
# user-fixable message (V-005 edge case).
EXTRACTORS: dict[str, Callable[[str, Settings], ExtractionResult]] = {
    ".pdf": pdf.extract_document,
    ".docx": docx.extract_document,
}


def raw_store_path(settings: Settings, manuscript_id: int) -> Path:
    return settings.data_dir / f"{manuscript_id}.extraction.json"


def select_extractor(suffix: str) -> Callable[[str, Settings], ExtractionResult]:
    extractor = EXTRACTORS.get(suffix.lower())
    if extractor is None:
        raise FileMalformedError(
            messages.UNSUPPORTED_FILE_TYPE.format(
                suffix=suffix or "(none)", supported=", ".join(sorted(EXTRACTORS))
            )
        )
    return extractor


# Magic numbers are format invariants: %PDF, ZIP local-file header (a DOCX
# is a zip), OLE compound file (legacy .doc).
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def detect_format(file_path: Path) -> str:
    """Sniff the REAL type from content — extensions are user input and
    lie (a DOCX renamed to .pdf must still parse as a DOCX, V-008 edge
    case). Returns an EXTRACTORS key or the honest non-key (".doc") so
    select_extractor can name what was actually uploaded."""
    with file_path.open("rb") as fh:
        head = fh.read(len(_OLE_MAGIC))
    if head.startswith(_PDF_MAGIC):
        return ".pdf"
    if head.startswith(_ZIP_MAGIC):
        try:
            with zipfile.ZipFile(file_path) as zf:
                if any(name.startswith("word/") for name in zf.namelist()):
                    return ".docx"
        except zipfile.BadZipFile:
            pass
        raise FileMalformedError(messages.FILE_UNREADABLE)
    if head.startswith(_OLE_MAGIC):
        return ".doc"  # legacy Word: recognized, then rejected by name
    raise FileMalformedError(messages.FILE_UNREADABLE)


async def save_upload(chunks: AsyncIterator[bytes], dest: Path, settings: Settings) -> Path:
    """Stream an upload to disk, enforcing the size ceiling WHILE reading —
    an oversized file is rejected at the limit, not after it landed."""
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            async for chunk in chunks:
                written += len(chunk)
                if written > limit:
                    raise FileTooLargeError(
                        messages.FILE_TOO_LARGE.format(limit_mb=settings.max_upload_mb)
                    )
                out.write(chunk)
    except FileTooLargeError:
        dest.unlink(missing_ok=True)
        raise
    return dest


async def ingest_manuscript(
    session: AsyncSession,
    manuscript: Manuscript,
    file_path: Path,
    settings: Settings | None = None,
) -> ExtractionResult:
    settings = settings or get_settings()
    manuscript.ingest_status = IngestStatus.processing
    await session.commit()
    loop = asyncio.get_running_loop()
    try:
        # Dispatch by sniffed content (extensions lie) — inside the stage
        # boundary so an unreadable file leaves the row `failed`, not stuck.
        extractor = select_extractor(detect_format(file_path))
        result = await loop.run_in_executor(None, extractor, str(file_path), settings)
        patterns = load_patterns(settings.ingest_patterns_file)
        drafts = await loop.run_in_executor(None, references.extract_references, result, patterns)
        try:
            # Vision pass (F1.3) merges image tables/equations into result
            # BEFORE the raw store is written, so the store is complete.
            llm = await get_llm_client_for(session, settings, manuscript.instructor_id)
            await vision.read_images(result, file_path, llm, patterns, settings)
        except (LLMNotConfiguredError, ApiDownError, QuotaExhaustedError):
            # No client (real mode before V-009), or the queue's own
            # retries were exhausted, or the daily quota ran out: image
            # content stays unread — an honest state (F1.7), never an
            # ingestion failure. Found live (V2 milestone demo,
            # 2026-07-25): a real Gemini vision-call outage previously
            # propagated through the outer `except Exception` below and
            # failed the WHOLE manuscript — the text/structure/references
            # this stage already extracted are still perfectly good and
            # must not be thrown away over an unrelated image-reading
            # problem.
            result.vision_status = "unavailable"
        await loop.run_in_executor(
            None, _write_raw_store, result, raw_store_path(settings, manuscript.id)
        )
    except FileMalformedError:
        # Stage boundary (CODING.md §2): the failure is recorded on the row,
        # then propagates — run-level stage bookkeeping arrives with V-018.
        # BUG-016: the row must say why, not just that it failed.
        manuscript.ingest_status = IngestStatus.failed
        manuscript.ingest_failure_reason = IngestFailureReason.unreadable_format
        await session.commit()
        raise
    except Exception:
        manuscript.ingest_status = IngestStatus.failed
        manuscript.ingest_failure_reason = IngestFailureReason.extraction_failed
        await session.commit()
        raise

    # Re-ingest replaces the previous citation set (idempotent).
    await session.execute(delete(Citation).where(Citation.manuscript_id == manuscript.id))
    session.add_all(Citation(manuscript_id=manuscript.id, **asdict(draft)) for draft in drafts)
    manuscript.section_tree = result.section_tree.model_dump()
    manuscript.ingest_status = IngestStatus.done
    await session.commit()
    return result


async def ingest_upload(
    session: AsyncSession,
    chunks: AsyncIterator[bytes],
    filename: str,
    group_label: str,
    settings: Settings | None = None,
    *,
    instructor_id: int,
) -> tuple[Manuscript, ExtractionResult, int, TitlePageProposal]:
    """Full upload flow for the HTTP surface: save (size-capped) → own row
    → ingest. `instructor_id` is the authenticated caller (BUG-002/D-020) —
    this endpoint used to attach uploads to whichever instructor had the
    lowest id, reachable with no login at all; fixed same session it was
    found."""
    settings = settings or get_settings()
    # BUG-022: group_label defaults to a constant ("Ungrouped"), so it
    # alone can't tell two manuscripts apart in a picker/list — the
    # instructor's own filename usually can. `.replace("\\", "/")` before
    # `.name` makes path-component stripping OS-independent — bare
    # `Path(...).name` only splits on the HOST's own separator, and
    # production runs on Linux (D-003), so a raw Windows-style path from
    # a non-browser client would otherwise pass through untouched.
    # `isprintable()` drops control characters, including a literal NUL
    # byte — verified live: an unescaped NUL in `filename` 500'd this
    # endpoint before this filter existed (Postgres's UTF8 encoding
    # rejects NUL outright; backend-critic finding, BUG-022 review).
    # `[:255]` matches the column's own limit so an unusually long
    # filename never 500s here either.
    clean_name = "".join(ch for ch in filename.replace("\\", "/") if ch.isprintable())
    original_filename = Path(clean_name).name[:255] or None
    # V-062: group_label is no longer stored verbatim -- it's resolved
    # against the instructor's existing groups first (case/whitespace
    # insensitive, AC1), so "Group 4" and "group 4" land on the same row
    # and the manuscript's own group_label becomes whichever spelling that
    # row already settled on.
    group = await resolve_or_create_group(session, instructor_id, group_label)
    manuscript = Manuscript(
        instructor_id=instructor_id,
        group_id=group.id,
        group_label=group.name,
        file_ref="",
        original_filename=original_filename,
    )
    session.add(manuscript)
    await session.commit()

    # Stored under a server-owned name: the id is the identity; the
    # original filename never touches the filesystem (it's persisted for
    # display only, above).
    suffix = Path(filename).suffix.lower()[:8]
    dest = settings.data_dir / "uploads" / f"{manuscript.id}{suffix}"
    try:
        await save_upload(chunks, dest, settings)
    except FileTooLargeError:
        manuscript.ingest_status = IngestStatus.failed
        manuscript.ingest_failure_reason = IngestFailureReason.file_too_large
        await session.commit()
        raise
    manuscript.file_ref = str(dest)
    await session.commit()
    result = await ingest_manuscript(session, manuscript, dest, settings)
    n_citations = (
        await session.scalar(
            select(func.count())
            .select_from(Citation)
            .where(Citation.manuscript_id == manuscript.id)
        )
        or 0
    )
    # V-063: deterministic, no LLM call (Q1 DECIDED) -- proposes only,
    # never applies (the instructor confirms via a separate endpoint).
    patterns = load_patterns(settings.ingest_patterns_file)
    title_page_proposal = extract_title_page(result, patterns)
    return manuscript, result, n_citations, title_page_proposal


def _write_raw_store(result: ExtractionResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(), encoding="utf-8")


async def list_manuscripts(
    session: AsyncSession,
    instructor_id: int,
    *,
    page: int = 1,
    page_size: int = 50,
    q: str | None = None,
    status: ManuscriptQueueStatus | None = None,
    needs_review: bool | None = None,
    group: str | None = None,
    program: str | None = None,
    sort: ManuscriptSort = ManuscriptSort.newest,
) -> PaginatedManuscripts:
    """Server-paginated listing (V-021 edge case: 100+ manuscripts in
    defense season) for both the dashboard table (4e) and the New Check
    modal's manuscript picker (V-018, which just requests a generously
    large page). Each row carries its own latest check_run id/status so
    the dashboard can link "view progress"/"open report" with zero extra
    per-row requests.

    V-071 AC2 applies every search/filter/sort before pagination. ``q``
    searches the instructor-visible group and filename; ``group`` is an
    exact, case-insensitive group filter; ``status`` is the closed Review
    Desk queue vocabulary; and ``needs_review`` is based on criterion-level
    escalations in the same latest DONE run used by the row's report link.

    `program` (V-062, AC5) filters to manuscripts whose group is assigned
    that program -- an inner join, so a manuscript with no group or a
    group whose program is still NULL ("Not set") is correctly excluded
    from any specific-program filter rather than guessed into one.
    `UNSET_PROGRAM_FILTER` is the one sentinel value that asks for exactly
    those excluded rows instead -- a real, selectable filter state, not an
    edge case to hide (`ui-designer` finding while speccing the dashboard
    control this powers)."""
    latest_run_status = (
        select(CheckRun.status)
        .where(CheckRun.manuscript_id == Manuscript.id)
        .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
        .limit(1)
        .correlate(Manuscript)
        .scalar_subquery()
    )
    latest_done_run_id = (
        select(CheckRun.id)
        .where(
            CheckRun.manuscript_id == Manuscript.id,
            CheckRun.status == CheckRunStatus.done,
        )
        .order_by(CheckRun.created_at.desc(), CheckRun.id.desc())
        .limit(1)
        .correlate(Manuscript)
        .scalar_subquery()
    )
    escalation_count = (
        select(func.count(CheckResult.id))
        .where(
            CheckResult.check_run_id == latest_done_run_id,
            CheckResult.criterion_id.is_not(None),
            CheckResult.outcome == ResultOutcome.escalated,
        )
        .correlate(Manuscript)
        .scalar_subquery()
    )
    has_decision = (
        select(ReadinessReport.id)
        .where(
            ReadinessReport.check_run_id == latest_done_run_id,
            ReadinessReport.decision.is_not(None),
        )
        .correlate(Manuscript)
        .exists()
    )

    filters = [Manuscript.instructor_id == instructor_id]
    filters.append(Manuscript.dismissed_at.is_(None))
    count_stmt = (
        select(func.count())
        .select_from(Manuscript)
        .outerjoin(Group, Group.id == Manuscript.group_id)
        .outerjoin(Program, Program.id == Group.program_id)
    )
    manuscripts_stmt = (
        select(Manuscript)
        .outerjoin(Group, Group.id == Manuscript.group_id)
        .outerjoin(Program, Program.id == Group.program_id)
    )

    normalized_q = q.strip() if q else None
    if normalized_q:
        pattern = f"%{normalized_q}%"
        filters.append(
            or_(
                Manuscript.group_label.ilike(pattern),
                Manuscript.original_filename.ilike(pattern),
            )
        )

    normalized_group = group.strip() if group else None
    if normalized_group:
        filters.append(func.lower(Manuscript.group_label) == normalized_group.casefold())

    if program == UNSET_PROGRAM_FILTER:
        filters.append(Program.id.is_(None))
    elif program is not None:
        filters.append(Program.name == program)

    if status == ManuscriptQueueStatus.needs_attention:
        # One reachable instructor-work queue owns both unresolved criterion
        # tasks and recovery states. Keep the union on the server so tenant
        # scoping, filters, totals, sorting, and pagination remain truthful.
        filters.append(
            or_(
                and_(latest_run_status == CheckRunStatus.done, escalation_count > 0),
                latest_run_status.in_((CheckRunStatus.failed, CheckRunStatus.cancelled)),
                and_(
                    latest_run_status.is_(None),
                    Manuscript.ingest_status == IngestStatus.failed,
                ),
            )
        )
    elif status == ManuscriptQueueStatus.ingestion_failed:
        filters.append(Manuscript.ingest_status == IngestStatus.failed)
    elif status == ManuscriptQueueStatus.not_checked:
        filters.extend(
            [
                Manuscript.ingest_status == IngestStatus.done,
                latest_run_status.is_(None),
            ]
        )
    elif status == ManuscriptQueueStatus.checking:
        filters.append(
            latest_run_status.in_(
                (
                    CheckRunStatus.queued,
                    CheckRunStatus.ingesting,
                    CheckRunStatus.structural,
                    CheckRunStatus.semantic,
                    CheckRunStatus.integrity,
                    CheckRunStatus.aggregating,
                )
            )
        )
    elif status == ManuscriptQueueStatus.check_failed:
        filters.append(latest_run_status == CheckRunStatus.failed)
    elif status == ManuscriptQueueStatus.cancelled:
        filters.append(latest_run_status == CheckRunStatus.cancelled)
    elif status == ManuscriptQueueStatus.checked:
        # Queue statuses are mutually useful instructor work states:
        # `checked` means the absolute-latest run finished and its report is
        # waiting for a human decision, while `decided` below means that
        # decision is already recorded. An older DONE report remains
        # readable while a re-run is active/failed/cancelled, but it must not
        # place that manuscript in two contradictory work queues at once.
        filters.extend([latest_run_status == CheckRunStatus.done, ~has_decision])
    elif status == ManuscriptQueueStatus.decided:
        filters.extend([latest_run_status == CheckRunStatus.done, has_decision])

    if needs_review is True:
        # An older report's escalation remains readable, but it is not a
        # current instructor task while a newer run is active or terminal in
        # a different state. This keeps the work queues mutually coherent.
        filters.extend([latest_run_status == CheckRunStatus.done, escalation_count > 0])
    elif needs_review is False:
        filters.append(escalation_count == 0)

    order_by = {
        ManuscriptSort.newest: (Manuscript.created_at.desc(), Manuscript.id.desc()),
        ManuscriptSort.oldest: (Manuscript.created_at.asc(), Manuscript.id.asc()),
        ManuscriptSort.group_asc: (
            func.lower(Manuscript.group_label).asc(),
            Manuscript.created_at.desc(),
            Manuscript.id.desc(),
        ),
        ManuscriptSort.needs_review_desc: (
            escalation_count.desc(),
            Manuscript.created_at.desc(),
            Manuscript.id.desc(),
        ),
    }[sort]

    total = await session.scalar(count_stmt.where(*filters))
    manuscripts = (
        await session.scalars(
            manuscripts_stmt.where(*filters)
            .order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    manuscript_ids = [m.id for m in manuscripts]
    latest_by_manuscript: dict[int, CheckRun] = {}
    # Separate from `latest_by_manuscript` (backend-critic finding,
    # V-055): a manuscript's absolute-latest run can be a failed/in-flight
    # RE-run that supersedes an earlier DONE run with a perfectly valid,
    # still-readable report. Tracking the latest DONE run too means "Open
    # report" never goes dark just because a newer re-run hasn't finished
    # yet — same class of bug BUG-012 was filed for, caught one level
    # down before it shipped.
    latest_done_by_manuscript: dict[int, CheckRun] = {}
    if manuscript_ids:
        runs = (
            await session.scalars(
                select(CheckRun)
                .where(CheckRun.manuscript_id.in_(manuscript_ids))
                .order_by(
                    CheckRun.manuscript_id,
                    CheckRun.created_at.desc(),
                    CheckRun.id.desc(),
                )
            )
        ).all()
        for run in runs:
            # First one seen per manuscript is the latest (rows arrive
            # ordered newest-first within each manuscript_id group).
            latest_by_manuscript.setdefault(run.manuscript_id, run)
            if run.status == CheckRunStatus.done:
                latest_done_by_manuscript.setdefault(run.manuscript_id, run)

    def _latest_id(manuscript_id: int) -> int | None:
        run = latest_by_manuscript.get(manuscript_id)
        return run.id if run is not None else None

    def _latest_status(manuscript_id: int) -> str | None:
        run = latest_by_manuscript.get(manuscript_id)
        return run.status.value if run is not None else None

    def _latest_done_id(manuscript_id: int) -> int | None:
        run = latest_done_by_manuscript.get(manuscript_id)
        return run.id if run is not None else None

    # V-038 / ux-critic finding: one query for every latest-done run's
    # decision, keyed by check_run_id then remapped by manuscript_id below
    # -- avoids an N+1 (one query per row) at defense-season density.
    decision_by_run: dict[int, str] = {}
    readiness_by_run: dict[int, str] = {}
    done_run_ids = [run.id for run in latest_done_by_manuscript.values()]
    if done_run_ids:
        rows = (
            await session.execute(
                select(
                    ReadinessReport.check_run_id,
                    ReadinessReport.decision,
                    ReadinessReport.status,
                ).where(ReadinessReport.check_run_id.in_(done_run_ids))
            )
        ).all()
        decision_by_run = {
            run_id: decision.value for run_id, decision, _status in rows if decision is not None
        }
        readiness_by_run = {run_id: status.value for run_id, _decision, status in rows}

    def _latest_decision(manuscript_id: int) -> str | None:
        run = latest_done_by_manuscript.get(manuscript_id)
        return decision_by_run.get(run.id) if run is not None else None

    def _latest_readiness(manuscript_id: int) -> str | None:
        run = latest_done_by_manuscript.get(manuscript_id)
        return readiness_by_run.get(run.id) if run is not None else None

    # V-041 / ux-critic finding (P1, live-reproduced against real
    # multi-family seeded data): without this, RerunModal had no signal
    # to tell "checked under the same rubric family, genuinely stale" from
    # "checked under a completely unrelated format" -- it defaulted BOTH
    # to selected, capable of silently submitting a manuscript for
    # grading against a rubric it was never invited to run under, burning
    # real quota (D-001) with no indication anywhere in the row. One
    # query, no N+1, same shape as `decision_by_run` above.
    family_by_run: dict[int, str] = {}
    if done_run_ids:
        rubric_ids = {run.rubric_id for run in latest_done_by_manuscript.values()}
        rubric_rows = (
            await session.execute(
                select(Rubric.id, Rubric.rubric_family_id).where(Rubric.id.in_(rubric_ids))
            )
        ).all()
        family_by_rubric = {rubric_id: str(family_id) for rubric_id, family_id in rubric_rows}
        family_by_run = {
            run.id: family_by_rubric[run.rubric_id]
            for run in latest_done_by_manuscript.values()
            if run.rubric_id in family_by_rubric
        }

    def _latest_done_rubric_family_id(manuscript_id: int) -> str | None:
        run = latest_done_by_manuscript.get(manuscript_id)
        return family_by_run.get(run.id) if run is not None else None

    # V-071 (AC1, BUG-058-adjacent): the dashboard's "N escalations awaiting
    # your review" count had no way to point at WHICH manuscript held them
    # -- `newcomer`'s baseline walkthrough had to open reports one at a
    # time, reading each one fully, to find the right row. Same one-query,
    # no-N+1 shape as `decision_by_run` above, scoped to the same latest-
    # done runs.
    escalated_by_run: dict[int, int] = {}
    if done_run_ids:
        rows = (
            await session.execute(
                select(CheckResult.check_run_id, func.count())
                .where(
                    CheckResult.check_run_id.in_(done_run_ids),
                    CheckResult.criterion_id.is_not(None),
                    CheckResult.outcome == ResultOutcome.escalated,
                )
                .group_by(CheckResult.check_run_id)
            )
        ).all()
        escalated_by_run = dict(rows)

    def _escalations_awaiting_review(manuscript_id: int) -> int:
        run = latest_done_by_manuscript.get(manuscript_id)
        return escalated_by_run.get(run.id, 0) if run is not None else 0

    # V-062 (AC5): each row's program, sourced through its group -- every
    # row needs this for display regardless of whether `program` filtered
    # this query, so it's a separate lookup, not reused from the filter
    # join above (which only ran when a filter was actually given).
    program_by_manuscript: dict[int, str] = {}
    if manuscript_ids:
        rows = (
            await session.execute(
                select(Manuscript.id, Program.name)
                .join(Group, Group.id == Manuscript.group_id)
                .join(Program, Program.id == Group.program_id)
                .where(Manuscript.id.in_(manuscript_ids))
            )
        ).all()
        program_by_manuscript = dict(rows)

    def _program(manuscript_id: int) -> str | None:
        return program_by_manuscript.get(manuscript_id)

    items = [
        ManuscriptListItem(
            id=m.id,
            group_label=m.group_label,
            program=_program(m.id),
            original_filename=m.original_filename,
            ingest_status=m.ingest_status.value,
            ingest_failure_reason=(
                m.ingest_failure_reason.value if m.ingest_failure_reason else None
            ),
            created_at=m.created_at,
            latest_check_run_id=_latest_id(m.id),
            latest_check_run_status=_latest_status(m.id),
            latest_done_check_run_id=_latest_done_id(m.id),
            latest_decision=_latest_decision(m.id),
            latest_readiness=_latest_readiness(m.id),
            latest_done_rubric_family_id=_latest_done_rubric_family_id(m.id),
            escalations_awaiting_review=_escalations_awaiting_review(m.id),
        )
        for m in manuscripts
    ]
    return PaginatedManuscripts(items=items, total=total or 0, page=page, page_size=page_size)


async def dismiss_failed_manuscript(
    session: AsyncSession, instructor_id: int, manuscript_id: int
) -> Manuscript:
    """Move a permanently failed upload out of the active Review Desk.

    This is intentionally retention, not deletion: the manuscript row remains
    in the instructor's Archive and an immutable audit event records the
    transition. Repeating the request is idempotent.
    """
    dismissed_at = datetime.now(UTC)
    manuscript = await session.scalar(
        update(Manuscript)
        .where(
            Manuscript.id == manuscript_id,
            Manuscript.instructor_id == instructor_id,
            Manuscript.ingest_status == IngestStatus.failed,
            Manuscript.dismissed_at.is_(None),
        )
        .values(dismissed_at=dismissed_at)
        .returning(Manuscript)
    )
    if manuscript is None:
        current = await session.scalar(
            select(Manuscript).where(
                Manuscript.id == manuscript_id,
                Manuscript.instructor_id == instructor_id,
            )
        )
        if current is None:
            raise NotFoundError(f"No manuscript with id {manuscript_id}.")
        if current.ingest_status != IngestStatus.failed:
            raise ConflictError("Only a permanently failed upload can be dismissed.")
        return current

    await write_audit_event(
        session,
        event_type="manuscript_ingestion_failure_dismissed",
        check_run_id=None,
        manuscript_id=manuscript.id,
        payload={
            "manuscript_id": manuscript.id,
            "ingest_failure_reason": (
                manuscript.ingest_failure_reason.value
                if manuscript.ingest_failure_reason is not None
                else None
            ),
        },
    )
    await session.commit()
    await session.refresh(manuscript)
    return manuscript


def load_raw_store(settings: Settings, manuscript_id: int) -> ExtractionResult:
    """Reads back what `_write_raw_store` wrote — the structural check
    engine (V-016) is the first consumer of the full extraction (blocks,
    tables, geometry) beyond the ingestion pass itself."""
    return ExtractionResult.model_validate_json(
        raw_store_path(settings, manuscript_id).read_text(encoding="utf-8")
    )


async def get_group_proposal(
    session: AsyncSession, settings: Settings, manuscript_id: int, instructor_id: int
) -> TitlePageProposal:
    """V-063 (AC6): re-derives the SAME proposal `ingest_upload` computed
    at upload time, from the persisted raw store -- nothing about the
    proposal itself is stored, it's recomputed on demand. This is what
    makes "dismiss now, decide later" a real, no-dead-end path (AC6)
    rather than a one-shot dialog: the dashboard's own "Set group" action
    calls this exact function whenever the instructor comes back to it.

    ux-critic (V-063 review), reproduced live: reopening on a manuscript
    that was ALREADY confirmed into a real group used to blindly re-derive
    the ORIGINAL title-page extraction every time -- an instructor who'd
    already fixed a garbled proposal would see their fix appear to have
    vanished, and risked silently reverting a good group record back to
    the bad one by re-confirming without noticing. A group only ever gets
    `GroupMember` rows through a real V-063 confirm (the older, plain
    pre-upload free-text path never creates them) -- so "this manuscript's
    current group has recorded members" is a reliable, DERIVED signal
    (no new column) that there's a confirmed state to show instead of the
    stale extraction. The anchor "current group" (never "p. N"/"paragraph
    N") tells the frontend this came from the group record, not the
    document -- the same evidence-honesty rule as an instructor's own
    edit, just sourced differently."""
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None or manuscript.instructor_id != instructor_id:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")

    current_group = await session.get(Group, manuscript.group_id)
    if current_group is not None:
        member_names = (
            await session.scalars(
                select(GroupMember.name).where(GroupMember.group_id == current_group.id)
            )
        ).all()
        if member_names:
            program_name = await program_name_for(session, current_group.program_id)
            return TitlePageProposal(
                title=None,
                short_name=AnchoredValue(current_group.name, "current group"),
                members=[AnchoredValue(name, "current group") for name in member_names],
                program=AnchoredValue(program_name, "current group") if program_name else None,
                adviser=None,
                extraction_failed=False,
            )

    result = load_raw_store(settings, manuscript_id)
    patterns = load_patterns(settings.ingest_patterns_file)
    return extract_title_page(result, patterns)


async def confirm_manuscript_group(
    session: AsyncSession,
    manuscript_id: int,
    instructor_id: int,
    group_name: str,
    member_names: list[str],
    program_id: int | None,
    title: str | None = None,
) -> tuple[Group, bool]:
    """V-063 (AC2): applies a CONFIRMED (possibly instructor-edited)
    proposal -- this is the only place a title-page proposal ever
    actually changes a manuscript's group; `extract_title_page` itself
    has no DB access and no side effects. `program_id` is only ever set
    on a NEWLY created group, never an existing one that already had a
    chance to be corrected some other way -- a later upload's own
    proposal must not silently overwrite a fact about a group that
    already exists. `title` (V-066) follows the identical rule: a second
    manuscript matched into an existing group carries its OWN title page,
    which is not necessarily the group's -- only the group's first confirm
    ever sets it."""
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None or manuscript.instructor_id != instructor_id:
        raise NotFoundError(f"No manuscript with id {manuscript_id}.")
    # backend-critic (V-063 review): reproduced live -- an unvalidated
    # program_id reached the FK and surfaced as a bare, unhandled 500
    # (asyncpg's ForeignKeyViolationError) instead of this codebase's own
    # established pattern for the same instructor-supplied FK
    # (`rubric/service.py::set_rubric_family_program`).
    if program_id is not None and await session.get(Program, program_id) is None:
        raise NotFoundError(f"No program with id {program_id}.")

    group, matched = await match_or_create_group_from_proposal(
        session, instructor_id, group_name, member_names
    )
    if not matched:
        if program_id is not None:
            group.program_id = program_id
        stripped_title = title.strip() if title else None
        if stripped_title:
            group.title = stripped_title

    manuscript.group_id = group.id
    manuscript.group_label = group.name
    await session.commit()
    await session.refresh(group)
    return group, matched
