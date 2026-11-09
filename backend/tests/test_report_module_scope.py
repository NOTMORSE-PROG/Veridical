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
    assert module.undetermined.locally_unassessed_count is None
    assert module.undetermined.pairs[0].comparison_method == "unknown"


def test_locally_declined_pair_retains_actual_zero_comparisons_and_source_method():
    module = agreement(
        {
            "undetermined": {
                "count": 1,
                "compared_count": 0,
                "locally_unassessed_count": 1,
                "pairs": [dict(PAIR, comparison_method="phase_guard")],
            }
        }
    )
    assert module.undetermined.compared_count == 0
    assert module.undetermined.locally_unassessed_count == 1
    assert module.undetermined.pairs[0].comparison_method == "phase_guard"


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


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("source_reading_order_unresolved", "source_reading_order_unresolved"),
        ("free text", None),
        (None, None),
    ],
)
def test_a_check_carries_its_coverage_reason_as_a_known_code_only(reason, expected):
    rows = [
        SimpleNamespace(
            kind=CheckKind.citation_integrity,
            criterion_id=None,
            outcome=ResultOutcome.escalated,
            detail={"coverage_reason": reason},
        )
    ]
    [module] = [
        row for row in integrity_modules(rows, set()) if row.check_kind == "citation_integrity"
    ]
    assert module.coverage_reason == expected


def test_states_alone_carry_no_counts_pairs_or_statements():
    from app.report.module_scope import module_states

    full = integrity_modules(
        [
            SimpleNamespace(
                kind=CheckKind.internal_agreement,
                criterion_id=None,
                outcome=ResultOutcome.escalated,
                detail={
                    **REPORT_44,
                    "undetermined": {"count": 1, "compared_count": 2, "pairs": []},
                },
            )
        ],
        set(),
    )
    states = module_states(full)
    assert [(m.check_kind, m.state) for m in states] == [(m.check_kind, m.state) for m in full]
    for module in states:
        assert module.agreement_scope is None and module.undetermined is None
        assert set(module.model_dump(exclude_none=True)) <= {
            "check_kind",
            "state",
            "coverage_reason",
        }


async def test_a_link_without_saved_detail_still_says_whether_each_check_ran(monkeypatch):
    """A stale or unrecorded share snapshot falls back to the live report's
    check states, and to nothing else from the report."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock

    from app.report.schemas import AgreementScopeOut, IntegrityModuleOut, UndeterminedPairsOut
    from app.share import service as share

    live = [
        IntegrityModuleOut(
            check_kind="internal_agreement",
            state="partial",
            undetermined=UndeterminedPairsOut(count=1, compared_count=2, pairs=[]),
            agreement_scope=AgreementScopeOut(n_objectives=3, n_results=4),
        ),
        IntegrityModuleOut(
            check_kind="citation_integrity",
            state="partial",
            coverage_reason="source_reading_order_unresolved",
        ),
    ]
    report = SimpleNamespace(integrity_modules=live)
    link = SimpleNamespace(
        revoked_at=None, expires_at=datetime.now(UTC) + timedelta(days=1), check_run_id=7
    )
    run = SimpleNamespace(id=7)
    session = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, key: link if key == "tok" else run)
    )
    public = SimpleNamespace(integrity_modules=[], subject=None)
    monkeypatch.setattr(share, "lock_assessment", AsyncMock())
    monkeypatch.setattr(share, "report_out_for_check_run", AsyncMock(return_value=report))
    monkeypatch.setattr(share, "flags_for_check_run", AsyncMock(return_value=[]))
    monkeypatch.setattr(share, "_public_details", AsyncMock(return_value=(None, "stale")))
    monkeypatch.setattr(share, "_to_public_report", lambda _report: public)
    import app.report.evidence_service as evidence

    monkeypatch.setattr(evidence, "authorize_evidence_run", AsyncMock(return_value=run))
    monkeypatch.setattr(share, "SharedReportOut", lambda **fields: SimpleNamespace(**fields))
    out = await share.get_shared_report(session, "tok")
    states = out.report.integrity_modules
    assert [(m.check_kind, m.state, m.coverage_reason) for m in states] == [
        ("internal_agreement", "partial", None),
        ("citation_integrity", "partial", "source_reading_order_unresolved"),
    ]
    assert all(m.undetermined is None and m.agreement_scope is None for m in states)


def test_states_alone_never_carry_an_unknown_coverage_code():
    from app.report.module_scope import module_states
    from app.report.schemas import IntegrityModuleOut

    module = IntegrityModuleOut(
        check_kind="citation_integrity", state="partial", coverage_reason="PRIVATE_FREE_TEXT"
    )
    assert module_states([module])[0].coverage_reason is None


def _status(**changes):
    from app.report.schemas import IntegrityCheckStatusOut

    values = dict(
        check_kind="originality_reuse",
        outcome="passed",
        n_skipped_quota=1,
        n_skipped_api_down=2,
        n_skipped_parse_failure=3,
        coverage_reason="source_reading_order_partial",
        n_metadata_skipped_insufficient=4,
        n_linkage_unassessed=5,
        n_source_pages_unassessed=3,
        source_pages_unassessed=[14, 12, 12, 13],
        pages_not_compared=[160, 158, 159],
        not_compared_from="p. 158",
        n_claim_support_unassessed=6,
        n_images_unassessed=7,
        n_recovery_tables_unassessed=8,
        n_equations_unassessed=9,
    )
    values.update(changes)
    return IntegrityCheckStatusOut(**values)


def test_a_shared_copy_names_what_each_check_left_out_and_nothing_else():
    from app.report.module_scope import public_coverage

    [out] = public_coverage([_status()])
    assert out.model_dump() == {
        "check_kind": "originality_reuse",
        "coverage_reason": "source_reading_order_partial",
        "n_pages_not_read": 3,
        "pages_not_read": [12, 13, 14],
        "pages_not_compared": [158, 159, 160],
        "not_compared_from": "p. 158",
        "n_skipped_service": 2,
        "n_skipped_capacity": 1,
        "n_skipped_unreadable": 3,
        "n_references_unverified": 4,
        "n_citation_links_unchecked": 5,
        "n_claims_unchecked": 6,
        "n_images_unchecked": 7,
        "n_tables_unchecked": 8,
        "n_equations_unchecked": 9,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"coverage_reason": "PRIVATE_FREE_TEXT"},
        {"not_compared_from": "PRIVATE the list ends near the appendix"},
        {"not_compared_from": "page 3"},
        {"source_pages_unassessed": [0, -2, 5], "pages_not_compared": [0]},
    ],
)
def test_a_shared_copy_drops_values_outside_their_known_shapes(changes):
    from app.report.module_scope import public_coverage

    [out] = public_coverage([_status(**changes)])
    encoded = out.model_dump_json()
    assert "PRIVATE" not in encoded and "page 3" not in encoded
    assert all(page > 0 for page in out.pages_not_read + out.pages_not_compared)


def test_a_shared_copy_skips_checks_it_does_not_know():
    from app.report.module_scope import public_coverage

    assert public_coverage([_status(check_kind="structural")]) == []


def test_the_shared_coverage_schema_rejects_extra_fields():
    from pydantic import ValidationError

    from app.report.module_scope import public_coverage
    from app.report.schemas import PublicCoverageOut

    values = public_coverage([_status()])[0].model_dump()
    for extra in ("reason", "image_coverage_items", "outcome", "source_pages_unassessed"):
        with pytest.raises(ValidationError):
            PublicCoverageOut.model_validate({**values, extra: "anything"})


@pytest.mark.parametrize("bad", [-1, True, 2.0, "3"])
def test_a_badly_saved_count_shows_as_zero_and_never_breaks_the_link(bad):
    from app.report.module_scope import public_coverage

    status = _status()
    status = status.model_copy(update={"n_linkage_unassessed": bad, "n_images_unassessed": bad})
    [out] = public_coverage([status])
    assert out.n_citation_links_unchecked == 0 and out.n_images_unchecked == 0
    assert out.n_tables_unchecked == 8 and out.pages_not_read == [12, 13, 14]
