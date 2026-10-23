"""Owned, versioned criterion corrections and immutable private history (V-085)."""

import base64
import json
from copy import deepcopy
from math import isfinite

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.escalation import select_resolution
from app.config import get_settings
from app.errors import ConflictError, NotFoundError
from app.models.audit import AuditLog
from app.models.enums import CheckRunStatus
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.report.assessment import assessment_write, lock_assessment
from app.report.judgment_schemas import (
    JudgmentChoices,
    JudgmentContextOut,
    JudgmentHistoryEntry,
    JudgmentHistoryOut,
    JudgmentRevisionIn,
    JudgmentRevisionOut,
    OriginalJudgmentAssessment,
)
from app.report.judgment_snapshot import judgment_grade_identity, snapshot_judgment
from app.report.schemas import EvidenceItem, RubricLevelOut


async def _owned_parent(session: AsyncSession, run_id: int, actor_id: int):
    run = await session.scalar(
        select(CheckRun)
        .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
        .where(CheckRun.id == run_id, Manuscript.instructor_id == actor_id)
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise NotFoundError(f"No check run with id {run_id}.")
    report = await lock_assessment(session, run_id)
    await session.refresh(run)
    if run.status != CheckRunStatus.done:
        raise ConflictError("This check hasn't finished yet. Its report isn't ready.")
    if report is None:
        raise NotFoundError(f"No report for check run {run_id}.")
    return run, report


async def _bound_result(session: AsyncSession, run_id: int, criterion_id: int):
    pair = (
        await session.execute(
            select(CheckResult, Criterion)
            .join(Criterion, Criterion.id == CheckResult.criterion_id)
            .join(CheckRun, CheckRun.id == CheckResult.check_run_id)
            .where(
                CheckResult.check_run_id == run_id,
                Criterion.id == criterion_id,
                Criterion.rubric_id == CheckRun.rubric_id,
            )
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if pair is None:
        raise NotFoundError(f"No criterion {criterion_id} on check run {run_id}.")
    return pair


def _choices(criterion: Criterion) -> JudgmentChoices:
    if not criterion.levels:
        return JudgmentChoices(kind="pass_fail", exclusion_allowed=True)
    try:
        levels = []
        for value in criterion.levels:
            if not isinstance(value, dict) or type(value.get("level")) is not int:
                raise ValueError
            if not isinstance(value.get("name"), str) or not value["name"].strip():
                raise ValueError
            if (
                type(value.get("points")) not in (int, float)
                or not isfinite(value["points"])
                or value["points"] < 0
            ):
                raise ValueError
            levels.append(
                RubricLevelOut.model_validate({**value, "descriptor": value.get("descriptor", "")})
            )
        if (
            len(levels) < 2
            or len({value.level for value in levels}) != len(levels)
            or max(value.points for value in levels) <= 0
        ):
            raise ValueError
        return JudgmentChoices(kind="levelled", levels=levels, exclusion_allowed=True)
    except (ValueError, TypeError, ValidationError):
        return JudgmentChoices(
            kind="unavailable",
            exclusion_allowed=False,
            unavailable_reason="The saved criterion scale could not be read.",
        )


def _original(result: CheckResult) -> OriginalJudgmentAssessment:
    detail = result.detail if isinstance(result.detail, dict) else {}
    evidence = []
    unavailable = not isinstance(detail.get("evidence", []), list)
    for value in detail.get("evidence", []) if not unavailable else []:
        try:
            evidence.append(EvidenceItem.model_validate(value))
        except ValidationError:
            unavailable = True
    support = detail.get("support_check")
    support = support if isinstance(support, dict) else {}
    original = detail.get("original_result")
    original = original if isinstance(original, dict) else {}
    # No current manual level is relabelled as the original AI grade.
    from app.report.judgment_snapshot import stored_level

    return OriginalJudgmentAssessment(
        kind=result.kind.value,
        basis=detail.get("basis") if isinstance(detail.get("basis"), str) else None,
        reason=detail.get("reason") if isinstance(detail.get("reason"), str) else None,
        reasoning=detail.get("reasoning") if isinstance(detail.get("reasoning"), str) else None,
        ai_majority_verdict=detail.get("verdict")
        if isinstance(detail.get("verdict"), str)
        else None,
        evidence=evidence,
        unverified_evidence=[v for v in detail.get("unverified_evidence", []) if isinstance(v, str)]
        if isinstance(detail.get("unverified_evidence", []), list)
        else [],
        support_withheld=detail.get("support_withheld") is True,
        support_status=support.get("status") if isinstance(support.get("status"), str) else None,
        evidence_unavailable=unavailable,
        original_outcome=original.get("outcome")
        if isinstance(original.get("outcome"), str)
        else None,
        original_score=original.get("score")
        if type(original.get("score")) in (int, float) and isfinite(original["score"])
        else None,
        original_level=stored_level(original),
    )


def _context(run_id: int, report: ReadinessReport, result: CheckResult, criterion: Criterion):
    current = snapshot_judgment(result)
    choices = _choices(criterion)
    unavailable = (
        "judgment_unavailable"
        if current is None
        else "report_decided"
        if report.decision is not None
        else "scale_unavailable"
        if choices.kind == "unavailable"
        else None
    )
    return JudgmentContextOut(
        check_run_id=run_id,
        criterion_id=criterion.id,
        check_result_id=result.id,
        criterion_text=criterion.text,
        assessment_revision=report.assessment_revision,
        decision=report.decision,
        can_revise=unavailable is None,
        unavailable_reason=unavailable,
        current=current,
        choices=choices,
        original_assessment=_original(result),
    )


async def get_judgment_context(
    session: AsyncSession, run_id: int, criterion_id: int, actor_id: int
):
    try:
        _, report = await _owned_parent(session, run_id, actor_id)
        result, criterion = await _bound_result(session, run_id, criterion_id)
        out = _context(run_id, report, result, criterion)
        await session.commit()
        return out
    except BaseException:
        await session.rollback()
        raise


def history_entry(event: AuditLog) -> JudgmentHistoryEntry:
    body = event.payload.get("judgment") if isinstance(event.payload, dict) else None
    if not isinstance(body, dict):
        raise ValueError("This event has no typed judgment snapshot.")
    out = JudgmentHistoryEntry.model_validate(
        {**body, "entry_id": event.id, "performed_at": event.created_at}
    )
    if (
        out.check_run_id != event.check_run_id
        or out.after.actor_id != out.performed_by
        or out.after.recorded_at != out.performed_at
    ):
        raise ValueError("The saved judgment event does not match its recorded attribution.")
    if out.action == "initial_resolution":
        valid = (
            event.event_type == "escalation_resolved"
            and out.before is None
            and out.after.judgment_revision == 1
        )
    else:
        valid = (
            event.event_type == "judgment_revised"
            and out.before is not None
            and out.after.judgment_revision == out.before.judgment_revision + 1
        )
    if not valid or out.assessment_revision_after != out.assessment_revision_before + 1:
        raise ValueError("The saved judgment event has inconsistent version information.")
    return out


async def revise_judgment(
    session: AsyncSession, run_id: int, criterion_id: int, actor_id: int, body: JudgmentRevisionIn
) -> JudgmentRevisionOut:
    from app.report.service import aggregate_and_score, get_report

    async with assessment_write(session, run_id, actor_id, body.expected_revision) as report:
        result, criterion = await _bound_result(session, run_id, criterion_id)
        before = snapshot_judgment(result)
        if before is None:
            raise ConflictError(
                "This criterion has no available recorded instructor judgment to revise."
            )
        if before.judgment_revision != body.expected_judgment_revision:
            raise ConflictError(
                "This recorded judgment changed. Review the current judgment before saving."
            )
        if _choices(criterion).kind == "unavailable":
            raise ConflictError("The saved criterion scale could not be read.")
        choice = select_resolution(
            criterion, result.detail, body.resolution, level=body.level, allow_accept_majority=False
        )
        detail = deepcopy(result.detail)
        if choice.level is None:
            detail.pop("level", None)
        else:
            detail["level"] = choice.level
        performed_at = await session.scalar(select(func.clock_timestamp()))
        detail["resolution"] = {
            "type": body.resolution,
            "reason": body.reason,
            "by_instructor_id": actor_id,
            "at": performed_at.isoformat(),
            "ai_majority_verdict": before
            and result.detail["resolution"].get("ai_majority_verdict"),
        }
        detail["judgment_history_incomplete"] = (
            result.detail.get("judgment_history_incomplete", True) is not False
        )
        result.detail = detail
        result.outcome, result.score = choice.outcome, choice.score
        result.judgment_revision = before.judgment_revision + 1
        await session.flush([result])
        await session.refresh(result, ["score"])
        after = snapshot_judgment(result)
        if (
            judgment_grade_identity(before) == judgment_grade_identity(after)
            and before.reason.strip() == after.reason.strip()
        ):
            # assessment_write has already incremented its epoch: raising here
            # rolls back the candidate grade, epoch and token together.
            raise ConflictError("Change the judgment or enter a new reason before saving.")
        event = AuditLog(
            event_type="judgment_revised",
            check_run_id=run_id,
            created_at=performed_at,
            payload={
                "judgment": {
                    "schema_version": 1,
                    "check_run_id": run_id,
                    "criterion_id": criterion_id,
                    "check_result_id": result.id,
                    "action": "judgment_revised",
                    "assessment_revision_before": body.expected_revision,
                    "assessment_revision_after": report.assessment_revision,
                    "before": before.model_dump(mode="json"),
                    "after": after.model_dump(mode="json"),
                    "performed_by": actor_id,
                }
            },
        )
        session.add(event)
        await session.flush([event])
        entry = history_entry(event)
        await aggregate_and_score(session, run_id, commit=False)
        current_report = await get_report(session, run_id, actor_id)
        out = JudgmentRevisionOut(
            context=_context(run_id, report, result, criterion), entry=entry, report=current_report
        )
    return out


async def get_judgment_history(
    session: AsyncSession,
    run_id: int,
    criterion_id: int,
    actor_id: int,
    *,
    expected_revision: int,
    expected_judgment_revision: int,
    page_size: int | None = None,
    cursor: str | None = None,
) -> JudgmentHistoryOut:
    settings = get_settings()
    size = settings.judgment_history_page_size if page_size is None else page_size
    if type(size) is not int or not 1 <= size <= settings.judgment_history_max_page_size:
        raise ConflictError("Choose a supported judgment history page size.")
    try:
        _, report = await _owned_parent(session, run_id, actor_id)
        result, _ = await _bound_result(session, run_id, criterion_id)
        current = snapshot_judgment(result)
        if current is None:
            raise ConflictError(
                "No recorded instructor judgment history is available for this criterion."
            )
        if (
            type(expected_revision) is not int
            or type(expected_judgment_revision) is not int
            or report.assessment_revision != expected_revision
            or current.judgment_revision != expected_judgment_revision
        ):
            raise ConflictError("This judgment or assessment changed. Reload judgment history.")
        query = select(AuditLog).where(
            AuditLog.check_run_id == run_id,
            AuditLog.event_type.in_(("escalation_resolved", "judgment_revised")),
            or_(
                AuditLog.payload["judgment"]["check_result_id"].astext == str(result.id),
                AuditLog.payload["check_result_id"].astext == str(result.id),
            ),
        )
        upper = (
            await session.scalar(
                select(func.max(AuditLog.id)).where(
                    AuditLog.id.in_(query.with_only_columns(AuditLog.id))
                )
            )
            or 0
        )
        binding = [run_id, criterion_id, result.id, expected_revision, expected_judgment_revision]
        before_id = upper + 1
        if cursor is not None:
            try:
                if len(cursor) > settings.judgment_history_cursor_max_length:
                    raise ValueError
                values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if (
                    not isinstance(values, list)
                    or len(values) != 7
                    or any(type(v) is not int for v in values)
                ):
                    raise ValueError
                if values[:5] != binding or values[5] != upper or not 0 < values[6] <= upper:
                    raise ValueError
                before_id = values[6]
            except (ValueError, TypeError, UnicodeDecodeError):
                raise ConflictError(
                    "This judgment history page is no longer current. Reload history."
                ) from None
        rows = list(
            (
                await session.scalars(
                    query.where(AuditLog.id <= upper, AuditLog.id < before_id)
                    .order_by(AuditLog.id.desc())
                    .limit(size + 1)
                )
            ).all()
        )
        more = len(rows) > size
        incomplete = result.detail.get("judgment_history_incomplete", True) is not False
        entries = []
        for event in rows[:size]:
            try:
                entry = history_entry(event)
                if entry.criterion_id != criterion_id or entry.check_result_id != result.id:
                    raise ValueError
                entries.append(entry)
            except (ValueError, ValidationError):
                incomplete = True
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps([*binding, upper, rows[size - 1].id], separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
            if more
            else None
        )
        out = JudgmentHistoryOut(
            check_run_id=run_id,
            criterion_id=criterion_id,
            check_result_id=result.id,
            assessment_revision=expected_revision,
            judgment_revision=expected_judgment_revision,
            current=current,
            entries=entries,
            history_incomplete=incomplete,
            next_cursor=next_cursor,
        )
        await session.commit()
        return out
    except BaseException:
        await session.rollback()
        raise
