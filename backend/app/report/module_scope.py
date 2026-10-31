"""F4–F7: saved execution state, independent of finding counts or severity."""

from pydantic import ValidationError

from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult
from app.report.schemas import IntegrityModuleOut, UndeterminedPairsOut
from app.report.statistical_scope import statistical_check_scope

MODULE_KINDS = (
    CheckKind.internal_agreement,
    CheckKind.citation_integrity,
    CheckKind.statistical_forensics,
    CheckKind.originality_reuse,
)


def integrity_modules(
    rows: list[CheckResult], incomplete_kinds: set[str]
) -> list[IntegrityModuleOut]:
    output = []
    for kind in MODULE_KINDS:
        saved = [row for row in rows if row.kind == kind and row.criterion_id is None]
        state = "not_recorded"
        if len(saved) == 1:
            row = saved[0]
            if row.detail is not None and not isinstance(row.detail, dict):
                output.append(IntegrityModuleOut(check_kind=kind.value, state="not_recorded"))
                continue
            detail = row.detail or {}
            if (
                detail.get("coverage_reason") is not None
                and not isinstance(detail["coverage_reason"], str)
            ) or ("comparison_ran" in detail and type(detail["comparison_ran"]) is not bool):
                output.append(IntegrityModuleOut(check_kind=kind.value, state="not_recorded"))
                continue
            if row.outcome in {ResultOutcome.api_down, ResultOutcome.quota_exhausted}:
                state = "incomplete"
            elif kind.value in incomplete_kinds or row.outcome in {
                ResultOutcome.escalated,
                ResultOutcome.unverifiable,
            }:
                state = "partial"
            elif row.outcome == ResultOutcome.not_applicable:
                state = "not_applicable"
            elif row.outcome in {ResultOutcome.passed, ResultOutcome.failed}:
                state = "completed"
            if kind == CheckKind.internal_agreement and row.outcome == ResultOutcome.passed:
                state = "partial"  # Saved candidate pairs have not established statement roles.
            if kind == CheckKind.statistical_forensics:
                scope = statistical_check_scope([row.outcome], [row.detail])
                if scope.state == "incomplete":
                    state = (
                        "partial"
                        if scope.unassessed_descriptive_rows
                        or (
                            scope.reconstruction
                            and scope.reconstruction.state in {"incomplete", "not_recorded"}
                        )
                        else "incomplete"
                    )
                elif scope.state == "not_recorded":
                    state = "not_recorded"
            if kind == CheckKind.originality_reuse and (
                detail.get("comparison_ran") is False
                or detail.get("coverage_reason")
                in {
                    "no_comparison_archive",
                    "source_reading_order_unresolved",
                }
            ):
                state = "incomplete"
        undetermined = (
            _undetermined(saved[0].detail)
            if kind == CheckKind.internal_agreement and len(saved) == 1
            else None
        )
        output.append(
            IntegrityModuleOut(check_kind=kind.value, state=state, undetermined=undetermined)
        )
    return output


def _undetermined(detail) -> UndeterminedPairsOut | None:
    """The saved list of pairs the agreement check could not decide, or None
    when the run predates it or the record does not have the expected shape."""
    raw = detail.get("undetermined") if isinstance(detail, dict) else None
    if not isinstance(raw, dict):
        return None
    try:
        value = UndeterminedPairsOut.model_validate(raw)
    except ValidationError:
        return None
    return value if value.count == len(value.pairs) else None
