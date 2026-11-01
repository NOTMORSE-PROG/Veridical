"""F4–F7: saved execution state, independent of finding counts or severity."""

import re
from collections import Counter

from pydantic import ValidationError

from app.checks.agreement.sections import NOT_OWN_RESULTS
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult
from app.report.schemas import (
    COVERAGE_REASONS,
    AgreementScopeOut,
    IntegrityCheckStatusOut,
    IntegrityModuleOut,
    PublicCoverageOut,
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
        )
    except ValidationError:
        return None


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
            coverage_reason=_known_reason(module.coverage_reason),
        )
        for module in modules
    ]


def public_modules(modules: list[IntegrityModuleOut]) -> list[IntegrityModuleOut]:
    """Shared (adviser) copies keep the agreement counts but not the quoted
    statements or their locations."""
    return [
        module.model_copy(
            update={"agreement_scope": module.agreement_scope.model_copy(update={"set_aside": []})}
        )
        if module.agreement_scope is not None
        else module
        for module in modules
    ]


def _known_reason(reason: str | None) -> str | None:
    return reason if reason in COVERAGE_REASONS else None


# The only shapes the originality check writes for where its left-out text
# starts; anything else saved there is not shared.
_LOCATION = re.compile(r"(?:p\. |¶)[1-9][0-9]{0,5}")


def _location(value: str | None) -> str | None:
    return value if isinstance(value, str) and _LOCATION.fullmatch(value) else None


def _pages(values) -> list[int]:
    return sorted({page for page in values or () if type(page) is int and page > 0})


_PUBLIC_KINDS = {
    "internal_agreement",
    "citation_integrity",
    "statistical_forensics",
    "originality_reuse",
}


def public_coverage(statuses: list[IntegrityCheckStatusOut]) -> list[PublicCoverageOut]:
    """What each integrity check left out, as the adviser may see it: the
    recorded code, page numbers and counts, copied field by field from the
    report's coverage record. Nothing else in that record crosses. A count saved
    in any other shape than a whole number of zero or more shows as 0, so one
    bad record never stops the link from opening."""
    return [
        PublicCoverageOut(
            check_kind=status.check_kind,
            coverage_reason=_known_reason(status.coverage_reason),
            n_pages_not_read=_count(status.n_source_pages_unassessed) or 0,
            pages_not_read=_pages(status.source_pages_unassessed),
            pages_not_compared=_pages(status.pages_not_compared),
            not_compared_from=_location(status.not_compared_from),
            n_skipped_service=_count(status.n_skipped_api_down) or 0,
            n_skipped_capacity=_count(status.n_skipped_quota) or 0,
            n_skipped_unreadable=_count(status.n_skipped_parse_failure) or 0,
            n_references_unverified=_count(status.n_metadata_skipped_insufficient) or 0,
            n_citation_links_unchecked=_count(status.n_linkage_unassessed) or 0,
            n_claims_unchecked=_count(status.n_claim_support_unassessed) or 0,
            n_images_unchecked=_count(status.n_images_unassessed) or 0,
            n_tables_unchecked=_count(status.n_recovery_tables_unassessed) or 0,
            n_equations_unchecked=_count(status.n_equations_unassessed) or 0,
        )
        for status in statuses
        if status.check_kind in _PUBLIC_KINDS
    ]
