"""F4–F7 execution state is conservative, total and separate from findings."""

from types import SimpleNamespace

import pytest

from app.models.enums import CheckKind, ResultOutcome
from app.report.module_scope import integrity_modules


def state(kind, outcome=ResultOutcome.passed, detail=None):
    rows = [SimpleNamespace(kind=kind, criterion_id=None, outcome=outcome, detail=detail)]
    return next(row.state for row in integrity_modules(rows, set()) if row.check_kind == kind.value)


@pytest.mark.parametrize(
    "detail",
    [
        ["unexpected"],
        "unexpected",
        42,
        {"coverage_reason": []},
        {"coverage_reason": {}},
        {"coverage_reason": False},
        {"coverage_reason": 42},
        {"comparison_ran": "true"},
        {"comparison_ran": 1},
    ],
)
def test_malformed_saved_detail_is_unknown_without_crashing(detail):
    assert state(CheckKind.originality_reuse, detail=detail) == "not_recorded"


@pytest.mark.parametrize(
    "outcome, expected",
    [
        (ResultOutcome.failed, "incomplete"),
        (ResultOutcome.passed, "completed"),
        (ResultOutcome.not_applicable, "not_applicable"),
        (ResultOutcome.api_down, "incomplete"),
        (ResultOutcome.quota_exhausted, "incomplete"),
    ],
)
def test_statistical_execution_agrees_with_its_existing_scope(outcome, expected):
    assert state(CheckKind.statistical_forensics, outcome, {}) == expected


def test_missing_duplicate_and_criterion_owned_results_cannot_invent_execution():
    assert all(row.state == "not_recorded" for row in integrity_modules([], set()))
    row = SimpleNamespace(
        kind=CheckKind.citation_integrity,
        criterion_id=None,
        outcome=ResultOutcome.passed,
        detail={},
    )
    assert (
        next(
            item.state
            for item in integrity_modules([row, row], set())
            if item.check_kind == row.kind.value
        )
        == "not_recorded"
    )
    row.criterion_id = 7
    assert all(item.state == "not_recorded" for item in integrity_modules([row], set()))


def test_candidate_agreement_pairs_remain_partial_and_empty_library_never_completed():
    assert state(CheckKind.internal_agreement, detail={}) == "partial"
    assert state(CheckKind.originality_reuse, detail={"comparison_ran": False}) == "incomplete"


PAIR = {
    "intent_text": "To build a login module.",
    "intent_anchor": "p. 3",
    "outcome_text": "The login screen was redesigned.",
    "outcome_anchor": "p. 40",
    "reason": "The texts do not say.",
}


def agreement(detail):
    rows = [
        SimpleNamespace(
            kind=CheckKind.internal_agreement,
            criterion_id=None,
            outcome=ResultOutcome.escalated,
            detail=detail,
        )
    ]
    return next(r for r in integrity_modules(rows, set()) if r.check_kind == "internal_agreement")


def test_undecided_agreement_pairs_reach_the_report_as_coverage():
    module = agreement({"undetermined": {"count": 1, "compared_count": 9, "pairs": [PAIR]}})
    assert module.undetermined.count == 1
    assert module.undetermined.compared_count == 9
    assert module.undetermined.pairs[0].outcome_anchor == "p. 40"


@pytest.mark.parametrize(
    "undetermined",
    [
        None,
        "three",
        {"count": 2, "compared_count": 9, "pairs": [PAIR]},
        {"count": 1, "compared_count": 9, "pairs": [{"intent_text": "x"}]},
    ],
)
def test_a_run_without_a_usable_list_shows_none(undetermined):
    assert agreement({"undetermined": undetermined}).undetermined is None
    assert agreement({}).undetermined is None


def scope(detail):
    rows = [
        SimpleNamespace(
            kind=CheckKind.internal_agreement,
            criterion_id=None,
            outcome=ResultOutcome.escalated,
            detail=detail,
        )
    ]
    return next(
        row.agreement_scope
        for row in integrity_modules(rows, set())
        if row.check_kind == "internal_agreement"
    )


def _set_aside(text, page, chapter_role, heading_role=None):
    return {
        "text": text,
        "anchor": page,
        "chapter": "2",
        "chapter_role": chapter_role,
        "heading": None,
        "heading_role": heading_role,
    }


REPORT_44 = {
    "n_intents": 17,
    "n_outcomes": 34,
    "absence_leads_withheld": True,
    "verdict_counts": {"consistent": 30, "unrelated": 1},
    "results_sections": {
        "results_section_identified": True,
        "n_results_kept": 29,
        "n_results_set_aside": 3,
        "n_results_too_short": 2,
        "set_aside": [
            _set_aside("Smith found that users preferred it.", "p. 20", "literature"),
            _set_aside("Cruz reported higher accuracy.", "p. 22", "literature"),
            _set_aside("Lee showed a faster response.", "p. 27", None, "literature"),
        ],
    },
}


def test_what_the_agreement_check_covered_reaches_the_report():
    value = scope(REPORT_44)
    assert (value.n_objectives, value.n_results) == (17, 34)
    assert value.results_section_identified is True
    assert (value.n_results_set_aside, value.n_results_too_short) == (3, 2)
    assert [(r.role, r.count) for r in value.set_aside_by_role] == [("literature", 3)]
    assert value.set_aside[0].text.startswith("Smith found")
    assert value.set_aside[0].page_anchor == "p. 20"
    # The heading's role is used when the chapter's is not a set-aside one.
    assert value.set_aside[2].role == "literature"
    assert value.absence_leads_withheld is True
    # Coverage only: no verdict counts ever reach the report.
    assert "verdict" not in str(value.model_dump())


def test_parts_a_run_did_not_record_stay_unknown_not_zero():
    value = scope({"n_intents": 4, "n_outcomes": 6})
    assert value.results_section_identified is None
    assert value.n_results_set_aside is None and value.n_results_too_short is None
    assert value.set_aside == [] and value.set_aside_by_role == []
    assert value.absence_leads_withheld is False


@pytest.mark.parametrize(
    "unmatched, matched",
    [(8, 9), (0, 17), (17, 0), (None, None), (18, None), (-1, None), ("8", None)],
)
def test_matched_objectives_are_found_less_unmatched_or_unknown(unmatched, matched):
    detail = dict(REPORT_44)
    if unmatched is not None:
        detail["n_unmatched_intents"] = unmatched
    assert scope(detail).n_objectives_matched == matched


def test_a_malformed_set_aside_entry_makes_that_part_unknown():
    detail = {
        "n_intents": 4,
        "results_sections": {"n_results_set_aside": 2, "set_aside": [{"anchor": "p. 2"}]},
    }
    value = scope(detail)
    assert value.n_results_set_aside is None and value.set_aside == []


@pytest.mark.parametrize(
    "detail",
    [None, {}, {"n_intents": None}, {"n_intents": -1}, {"n_intents": True}, {"n_intents": "17"}],
)
def test_a_run_saved_before_the_scope_was_recorded_shows_none(detail):
    assert scope(detail) is None


def test_other_modules_carry_no_agreement_scope():
    rows = [
        SimpleNamespace(
            kind=CheckKind.citation_integrity,
            criterion_id=None,
            outcome=ResultOutcome.passed,
            detail=REPORT_44,
        )
    ]
    module = next(m for m in integrity_modules(rows, set()) if m.check_kind == "citation_integrity")
    assert module.agreement_scope is None


def test_a_shared_copy_keeps_the_counts_but_not_the_statements():
    from app.report.module_scope import public_modules

    rows = [
        SimpleNamespace(
            kind=CheckKind.internal_agreement,
            criterion_id=None,
            outcome=ResultOutcome.escalated,
            detail=REPORT_44,
        )
    ]
    [shared] = [
        m
        for m in public_modules(integrity_modules(rows, set()))
        if m.check_kind == "internal_agreement"
    ]
    assert shared.agreement_scope.n_results_set_aside == 3
    assert [(r.role, r.count) for r in shared.agreement_scope.set_aside_by_role] == [
        ("literature", 3)
    ]
    assert shared.agreement_scope.set_aside == []
