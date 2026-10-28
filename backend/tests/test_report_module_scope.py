"""F4–F7 execution state is conservative, total and separate from findings."""

from types import SimpleNamespace

import pytest

from app.models.enums import CheckKind, ResultOutcome
from app.report.module_scope import integrity_modules


def state(kind, outcome=ResultOutcome.passed, detail=None):
    rows = [SimpleNamespace(kind=kind, criterion_id=None, outcome=outcome, detail=detail)]
    return next(row.state for row in integrity_modules(rows, set()) if row.check_kind == kind.value)


@pytest.mark.parametrize("detail", [["unexpected"], "unexpected", 42,
    {"coverage_reason": []}, {"coverage_reason": {}}, {"coverage_reason": False},
    {"coverage_reason": 42}, {"comparison_ran": "true"}, {"comparison_ran": 1}])
def test_malformed_saved_detail_is_unknown_without_crashing(detail):
    assert state(CheckKind.originality_reuse, detail=detail) == "not_recorded"


@pytest.mark.parametrize("outcome, expected", [
    (ResultOutcome.failed, "incomplete"), (ResultOutcome.passed, "completed"),
    (ResultOutcome.not_applicable, "not_applicable"), (ResultOutcome.api_down, "incomplete"),
    (ResultOutcome.quota_exhausted, "incomplete"),
])
def test_statistical_execution_agrees_with_its_existing_scope(outcome, expected):
    assert state(CheckKind.statistical_forensics, outcome, {}) == expected


def test_missing_duplicate_and_criterion_owned_results_cannot_invent_execution():
    assert all(row.state == "not_recorded" for row in integrity_modules([], set()))
    row = SimpleNamespace(kind=CheckKind.citation_integrity, criterion_id=None,
                          outcome=ResultOutcome.passed, detail={})
    assert next(item.state for item in integrity_modules([row, row], set())
                if item.check_kind == row.kind.value) == "not_recorded"
    row.criterion_id = 7
    assert all(item.state == "not_recorded" for item in integrity_modules([row], set()))


def test_candidate_agreement_pairs_remain_partial_and_empty_library_never_completed():
    assert state(CheckKind.internal_agreement, detail={}) == "partial"
    assert state(CheckKind.originality_reuse, detail={"comparison_ran": False}) == "incomplete"
