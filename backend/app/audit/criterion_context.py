"""Read producing-format context without changing the recorded judgment (F8.10)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.schemas import AuditCriterionContext
from app.db_types import BIGINT_MAX
from app.models.audit import AuditLog
from app.models.enums import CheckKind
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun

_JUDGMENT_EVENTS = frozenset({"escalation_resolved", "judgment_recorded", "judgment_revised"})
_ID_FIELDS = ("check_run_id", "criterion_id", "check_result_id")


def _recorded_id(value: object) -> bool:
    return type(value) is int and 0 < value <= BIGINT_MAX


def _binding(event: AuditLog) -> tuple[int, int] | None:
    if event.event_type not in _JUDGMENT_EVENTS or not _recorded_id(event.check_run_id):
        return None
    payload = event.payload
    if not isinstance(payload, dict):
        return None
    if "judgment" in payload:
        saved = payload["judgment"]
        if not isinstance(saved, dict) or not all(_recorded_id(saved.get(k)) for k in _ID_FIELDS):
            return None
        # A malformed typed receipt must not fall back to looser legacy fields.
        from app.report.judgments import history_entry

        try:
            history = history_entry(event)
        except (ValueError, TypeError):
            return None
        criterion_id, result_id = history.criterion_id, history.check_result_id
    elif event.event_type == "escalation_resolved":
        criterion_id, result_id = payload.get("criterion_id"), payload.get("check_result_id")
        if not _recorded_id(criterion_id) or not _recorded_id(result_id):
            return None
    else:
        return None
    expected = dict(zip(_ID_FIELDS, (event.check_run_id, criterion_id, result_id), strict=True))
    if any(
        k in payload and (not _recorded_id(payload[k]) or payload[k] != v)
        for k, v in expected.items()
    ):
        return None
    return criterion_id, result_id


async def get_criterion_context(
    session: AsyncSession, event: AuditLog, instructor_id: int
) -> AuditCriterionContext | None:
    binding = _binding(event)
    if binding is None:
        return None
    criterion_id, result_id = binding
    row = (
        await session.execute(
            select(Criterion, Rubric, CheckRun.manuscript_id)
            .join(Rubric, Rubric.id == Criterion.rubric_id)
            .join(CheckResult, CheckResult.criterion_id == Criterion.id)
            .join(CheckRun, CheckRun.id == CheckResult.check_run_id)
            .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
            .where(
                CheckRun.id == event.check_run_id,
                CheckResult.id == result_id,
                Criterion.id == criterion_id,
                Criterion.rubric_id == CheckRun.rubric_id,
                CheckResult.kind.in_((CheckKind.structural, CheckKind.semantic)),
                Manuscript.instructor_id == instructor_id,
                Rubric.instructor_id == instructor_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    criterion, rubric, manuscript_id = row
    if (
        event.manuscript_id is not None
        and event.manuscript_id != manuscript_id
        or not criterion.text.strip()
    ):
        return None
    return AuditCriterionContext(
        check_run_id=event.check_run_id,
        criterion_id=criterion.id,
        check_result_id=result_id,
        criterion_text=criterion.text,
        criterion_position=criterion.position,
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        rubric_title=rubric.title,
    )
