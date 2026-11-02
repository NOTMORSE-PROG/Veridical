"""F4–F7: saved execution state, independent of finding counts or severity."""

from collections import Counter

from pydantic import ValidationError

from app.checks.agreement.sections import NOT_OWN_RESULTS
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult
from app.report.schemas import (
    COVERAGE_REASONS,
    AgreementScopeOut,
    IntegrityModuleOut,
    UndeterminedPairsOut,
)
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
        agreement_scope = (
            _agreement_scope(saved[0].detail)
            if kind == CheckKind.internal_agreement and len(saved) == 1
            else None
        )
        reason = (
            saved[0].detail.get("coverage_reason")
            if len(saved) == 1 and isinstance(saved[0].detail, dict)
            else None
        )
        output.append(
            IntegrityModuleOut(
                check_kind=kind.value,
                state=state,
                undetermined=undetermined,
                agreement_scope=agreement_scope,
                coverage_reason=reason if reason in COVERAGE_REASONS else None,
            )
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


def _count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _agreement_scope(detail) -> AgreementScopeOut | None:
    """What the agreement check covered, or None for a run saved before it
    recorded its objectives and results. Parts it did not record stay None;
    a malformed set-aside entry makes the set-aside part unknown rather than
    silently shorter."""
    if not isinstance(detail, dict) or _count(detail.get("n_intents")) is None:
        return None
    sections = detail.get("results_sections")
    sections = sections if isinstance(sections, dict) else {}
    identified = sections.get("results_section_identified")
    entries = []
    raw = sections.get("set_aside")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            entries = None
            break
        role = (
            item.get("chapter_role")
            if item.get("chapter_role") in NOT_OWN_RESULTS
            else item.get("heading_role")
        )
        anchor = item.get("anchor")
        entries.append(
            {
                "text": item["text"],
                "page_anchor": anchor if isinstance(anchor, str) else None,
                "role": role if isinstance(role, str) and role else "other",
            }
        )
    roles = Counter(entry["role"] for entry in entries or [])
    objectives = _objective_sections(detail)
    try:
        return AgreementScopeOut(
            n_objectives=_count(detail.get("n_intents")),
            n_results=_count(detail.get("n_outcomes")),
            n_objectives_matched=_matched(detail),
            results_section_identified=identified if isinstance(identified, bool) else None,
            n_results_set_aside=_count(sections.get("n_results_set_aside"))
            if entries is not None
            else None,
            n_results_too_short=_count(sections.get("n_results_too_short")),
            set_aside_by_role=[
                {"role": role, "count": count} for role, count in roles.most_common()
            ],
            set_aside=entries or [],
            absence_leads_withheld=detail.get("absence_leads_withheld") is True,
            n_pairs_skipped=_skipped(detail),
            **objectives,
        )
    except ValidationError:
        return None


def _set_aside_entries(raw, role_of) -> list[dict] | None:
    """Saved set-aside statements as panel entries; a malformed entry makes
    the whole list unknown rather than silently shorter."""
    entries = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            return None
        role = role_of(item)
        anchor, chapter = item.get("anchor"), item.get("chapter")
        entries.append(
            {
                "text": item["text"],
                "page_anchor": anchor if isinstance(anchor, str) else None,
                "role": role if isinstance(role, str) and role else "other",
                "chapter": chapter if isinstance(chapter, str) and chapter.strip() else None,
            }
        )
    return entries


def _objective_sections(detail: dict) -> dict:
    """BUG-639: the objectives the check set aside before pairing, read from
    its saved `objective_sections`; nothing for a run saved before it."""
    sections = detail.get("objective_sections")
    if not isinstance(sections, dict):
        return {}
    identified = sections.get("objectives_section_identified")
    entries = _set_aside_entries(sections.get("set_aside"), lambda item: item.get("chapter_role"))
    despite = sections.get("kept_despite_role")
    roles = Counter(entry["role"] for entry in entries or [])
    return {
        "n_objectives_extracted": _count(detail.get("n_intents_extracted")),
        "objectives_section_identified": identified if isinstance(identified, bool) else None,
        "n_objectives_set_aside": _count(sections.get("n_objectives_set_aside"))
        if entries is not None
        else None,
        "objectives_set_aside_by_role": [
            {"role": role, "count": count} for role, count in roles.most_common()
        ],
        "objectives_set_aside": entries or [],
        "n_objectives_kept_despite_role": len(despite)
        if isinstance(despite, list) and despite
        else None,
    }


_SKIP_FIELDS = (
    "n_skipped_quota",
    "n_skipped_api_down",
    "n_skipped_parse_failure",
    "n_skipped_ai_not_configured",
    "n_skipped_ai_key_rejected",
)


def _matched(detail: dict) -> int | None:
    found, unmatched = _count(detail.get("n_intents")), _count(detail.get("n_unmatched_intents"))
    if found is None or unmatched is None or unmatched > found:
        return None
    return found - unmatched


def _skipped(detail: dict) -> int | None:
    counts = [_count(detail.get(field)) for field in _SKIP_FIELDS if field in detail]
    if not counts or None in counts:
        return None
    return sum(counts)


def module_states(modules: list[IntegrityModuleOut]) -> list[IntegrityModuleOut]:
    """Each check's state and coverage code only: what a shared link may show
    when it holds no saved detail of its own. No counts, pairs or statements."""
    return [
        IntegrityModuleOut(
            check_kind=module.check_kind,
            state=module.state,
            coverage_reason=module.coverage_reason,
        )
        for module in modules
    ]


def public_modules(modules: list[IntegrityModuleOut]) -> list[IntegrityModuleOut]:
    """Shared (adviser) copies keep the agreement counts but not the quoted
    statements or their locations."""
    return [
        module.model_copy(
            update={
                "agreement_scope": module.agreement_scope.model_copy(
                    update={"set_aside": [], "objectives_set_aside": []}
                )
            }
        )
        if module.agreement_scope is not None
        else module
        for module in modules
    ]
