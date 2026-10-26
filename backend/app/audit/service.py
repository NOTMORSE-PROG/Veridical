"""Audit log read surface (F8.10, screen 4s): every AI call is already
written by `LLMQueue` (V-009/V-022); every escalation resolution/override
writes its own row too (V-023/V-026). This module only ever READS —
`audit_log` itself is DB-append-only (migration 0001's trigger).

Scoping: a row is returned only when either its `check_run_id` resolves to
one of THIS instructor's manuscripts or its direct `manuscript_id` does.
The direct attribution is for instructor-visible lifecycle events that can
happen before a check run exists, such as dismissing a failed ingestion.
Rows with neither attribution remain excluded rather than guessed into an
instructor's history.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.schemas import AuditLogDetail, AuditLogSummary, LLMExecutionMode, PaginatedAuditLog
from app.errors import NotFoundError
from app.models.audit import AuditLog
from app.models.enums import LLMMode
from app.models.manuscript import Manuscript
from app.models.run import CheckRun


def instructor_audit_query(instructor_id: int) -> Select[tuple[AuditLog, str, str | None]]:
    """Use stored manuscript ownership, never untrusted payload attribution."""
    return (
        select(AuditLog, Manuscript.group_label, Manuscript.original_filename)
        .outerjoin(CheckRun, CheckRun.id == AuditLog.check_run_id)
        .join(
            Manuscript,
            Manuscript.id == func.coalesce(CheckRun.manuscript_id, AuditLog.manuscript_id),
        )
        .where(Manuscript.instructor_id == instructor_id)
    )


def _llm_execution_mode(event_type: str, payload: dict[str, Any]) -> LLMExecutionMode | None:
    """BUG-219: honest per-row provenance for the audit list/detail, derived
    only from the already-stored `fake_llm` fact — never inferred from
    anything else, and never defaulted to "real" for a row that simply
    predates this field. Non-LLM events (overrides, decisions, routing...)
    don't carry a `fake_llm` fact at all, because the concept doesn't apply
    to them; `None` says that plainly rather than reporting a guess.

    Same `fake`/`real`/`unknown` vocabulary BUG-049's `LLMMode` already made
    instructor-facing on the report/flag/adviser surfaces — this is a
    separate, per-ROW derivation (not a re-read of `CheckRun.llm_mode`)
    because several audit event types (`rubric_parse_attempt`'s LLM calls,
    a bare `ping`) have no `check_run_id` to join through at all, but it
    reports the same fact using the same words on purpose."""
    if not event_type.startswith("llm_"):
        return None
    fake_llm = payload.get("fake_llm")
    if fake_llm is True:
        return LLMMode.fake.value
    if fake_llm is False:
        return LLMMode.real.value
    return LLMMode.unknown.value


def _summary(row: AuditLog, group_label: str | None) -> AuditLogSummary:
    payload = row.payload or {}
    return AuditLogSummary(
        id=row.id,
        event_type=row.event_type,
        check_run_id=row.check_run_id,
        manuscript_id=row.manuscript_id,
        manuscript_group_label=group_label,
        prompt_type=payload.get("prompt_type"),
        prompt_version=row.prompt_version,
        agreement_score=float(row.agreement_score) if row.agreement_score is not None else None,
        llm_execution_mode=_llm_execution_mode(row.event_type, payload),
        created_at=row.created_at,
    )


async def list_audit_log(
    session: AsyncSession,
    instructor_id: int,
    *,
    check_run_id: int | None = None,
    event_type: str | None = None,
    event_type_prefix: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedAuditLog:
    query = instructor_audit_query(instructor_id)
    if check_run_id is not None:
        query = query.where(AuditLog.check_run_id == check_run_id)
    if event_type is not None:
        query = query.where(AuditLog.event_type == event_type)
    if event_type_prefix is not None:
        query = query.where(AuditLog.event_type.startswith(event_type_prefix))
    if date_from is not None:
        query = query.where(AuditLog.created_at >= date_from)
    if date_to is not None:
        query = query.where(AuditLog.created_at <= date_to)

    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.execute(
            # V-081: historical rows use different clocks and transaction-
            # start times. Present immutable entry-number order, not a
            # claimed event/commit chronology; retain stored date filters.
            query.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = [_summary(row, group_label) for row, group_label, _original_filename in rows]
    return PaginatedAuditLog(items=items, total=total or 0, page=page, page_size=page_size)


async def get_audit_log_detail(
    session: AsyncSession, instructor_id: int, audit_id: int
) -> AuditLogDetail:
    row = (
        await session.execute(instructor_audit_query(instructor_id).where(AuditLog.id == audit_id))
    ).first()
    if row is None:
        raise NotFoundError(f"No audit log entry {audit_id}.")
    entry, group_label, original_filename = row
    summary = _summary(entry, group_label)
    group_selection = None
    if (
        entry.event_type == "manuscript_group_selected"
        and entry.check_run_id is None
        and entry.manuscript_id is not None
    ):
        # Selection writes use the audit helper too; import after module setup.
        from app.groups.selection import selection_audit_event

        validated = selection_audit_event(
            entry.payload, manuscript_id=entry.manuscript_id, instructor_id=instructor_id
        )
        if validated is not None:
            group_selection = validated.model_dump(mode="json")
    return AuditLogDetail(
        **summary.model_dump(),
        manuscript_original_filename=original_filename,
        input_hash=entry.input_hash,
        payload=entry.payload or {},
        group_selection=group_selection,
    )


def _decimal_or_none(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


async def write_audit_event(
    session: AsyncSession,
    *,
    event_type: str,
    check_run_id: int | None,
    payload: dict[str, Any],
    manuscript_id: int | None = None,
    prompt_version: str | None = None,
    input_hash: str | None = None,
    agreement_score: float | None = None,
    created_at: datetime | None = None,
) -> AuditLog:
    """Shared write helper for the non-LLM audit events this ticket adds
    on top of V-009's queue-level writes (escalation resolutions, flag
    overrides, final decisions) — one consistent row shape."""
    entry = AuditLog(
        event_type=event_type,
        check_run_id=check_run_id,
        manuscript_id=manuscript_id,
        prompt_version=prompt_version,
        input_hash=input_hash,
        agreement_score=_decimal_or_none(agreement_score),
        payload=payload,
    )
    if created_at is not None:
        # An action can reuse the exact database time of its winning write.
        # Ordinary callers retain the existing database default.
        entry.created_at = created_at
    session.add(entry)
    return entry
