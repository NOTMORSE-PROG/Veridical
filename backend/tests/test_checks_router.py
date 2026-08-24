"""V-015 unit tests: pure routing logic, no DB (app/checks/router.py +
app/checks/rules registry). Live persistence (audit log + not_applicable
check_result) is covered in test_checks_router_live.py.
"""

import random
from dataclasses import dataclass

import pytest

from app.checks.escalation import NEEDS_REVIEW_OUTCOMES
from app.checks.router import route_criteria, route_criterion
from app.checks.rules import (
    RuleSpec,
    _clear_registry_for_tests,
    _restore_registry,
    _snapshot_registry,
    register_rule,
)
from app.messages import (
    CRITERION_NOT_ASSESSABLE_FROM_DOCUMENT,
    CRITERION_TYPE_UNRECOGNIZED,
    STRUCTURAL_RULE_UNIMPLEMENTED,
)
from app.models.enums import CheckKind, ResultOutcome


def _noop_run(criterion, ctx):  # pragma: no cover - never invoked in these routing-only tests
    raise NotImplementedError


@dataclass
class FakeCriterion:
    id: int
    type: str
    text: str
    evidence: str | None = None


@pytest.fixture(autouse=True)
def clean_registry():
    """These tests exercise ROUTING in isolation (an empty registry is the
    honest pre-V-016 baseline the snapshot test documents) — snapshot the
    real V-016 rules first and restore them afterward so later test
    modules still see the production registry, not a permanently-cleared
    one (test isolation, not just this file's own correctness)."""
    snapshot = _snapshot_registry()
    _clear_registry_for_tests()
    yield
    _restore_registry(snapshot)


def test_semantic_criterion_always_routes_semantic():
    c = FakeCriterion(id=1, type="semantic", text="The argument is well developed")
    decision = route_criterion(c, criterion_id=c.id, raw_type=c.type)
    assert decision.kind == CheckKind.semantic
    assert decision.rule_id is None
    assert not decision.degraded
    assert not decision.unroutable


def test_structural_criterion_with_no_registered_rule_degrades_to_semantic():
    c = FakeCriterion(id=2, type="structural", text="Margins must be exactly 1 inch")
    decision = route_criterion(c, criterion_id=c.id, raw_type=c.type)
    assert decision.kind == CheckKind.semantic
    assert decision.degraded is True
    assert decision.note == STRUCTURAL_RULE_UNIMPLEMENTED
    assert not decision.unroutable


def test_structural_criterion_matching_a_registered_rule_routes_structural():
    register_rule(
        RuleSpec(
            rule_id="required_section_present",
            description="test rule",
            matches=lambda crit: "abstract" in crit.text.lower(),
            run=_noop_run,
        )
    )
    c = FakeCriterion(id=3, type="structural", text="Manuscript must include an Abstract")
    decision = route_criterion(c, criterion_id=c.id, raw_type=c.type)
    assert decision.kind == CheckKind.structural
    assert decision.rule_id == "required_section_present"
    assert not decision.degraded
    assert not decision.unroutable


def test_structural_criterion_not_matching_any_registered_rule_still_degrades():
    register_rule(
        RuleSpec(
            rule_id="required_section_present",
            description="test rule",
            matches=lambda crit: "abstract" in crit.text.lower(),
            run=_noop_run,
        )
    )
    c = FakeCriterion(id=4, type="structural", text="References must use APA style")
    decision = route_criterion(c, criterion_id=c.id, raw_type=c.type)
    assert decision.kind == CheckKind.semantic
    assert decision.degraded is True


def test_unrecognized_type_is_unroutable_not_dropped():
    c = FakeCriterion(id=5, type="garbled-value", text="???")
    decision = route_criterion(c, criterion_id=c.id, raw_type=c.type)
    assert decision.unroutable is True
    assert decision.note == CRITERION_TYPE_UNRECOGNIZED


def test_not_assessable_criterion_is_never_ai_graded_or_escalated():
    """BUG-092's own regression test: a defense-day/physical requirement
    (the ticket's own real examples -- bringing bound copies, answering
    questions live) has nowhere to go under the old structural/semantic
    split but the AI-grading pipeline, which then fails to reach a
    verdict and escalates it as if it were a document question. Routed
    terminally -- never touches semantic grading, never reaches the
    escalation queue -- and honestly, not as a defect."""
    c = FakeCriterion(
        id=7,
        type="not_assessable",
        text="The group brings three bound copies of the paper to the defense.",
    )
    decision = route_criterion(c, criterion_id=c.id, raw_type=c.type)
    assert decision.unroutable is True  # terminal: apply_routing persists not_applicable directly
    assert decision.degraded is False  # a correct decision, not a fallback from a gap
    assert decision.note == CRITERION_NOT_ASSESSABLE_FROM_DOCUMENT
    # The escalation queue is populated from CheckResult rows with a
    # criterion_id AND an outcome in NEEDS_REVIEW_OUTCOMES -- `unroutable`
    # criteria never get a CheckResult with any of those outcomes at all
    # (apply_routing writes `not_applicable` directly), so this can never
    # reach `list_escalated` regardless of what else changes downstream.
    assert ResultOutcome.not_applicable not in NEEDS_REVIEW_OUTCOMES


def test_zero_weight_criterion_still_routes_normally():
    # Router doesn't special-case weight at all (scoring exclusion is
    # V-019's job) — a zero-weight criterion routes exactly like any other.
    c = FakeCriterion(id=6, type="semantic", text="Optional acknowledgments section")
    decision = route_criterion(c, criterion_id=c.id, raw_type=c.type)
    assert decision.kind == CheckKind.semantic
    assert not decision.unroutable


def test_full_coverage_invariant_holds_for_arbitrary_criteria_sets():
    """Property test (manual, no hypothesis dep in this project): for any
    mix of criterion types/text, routing produces exactly one decision per
    input criterion, and every decision is either routable (kind set) or
    explicitly unroutable — never silently absent."""
    register_rule(
        RuleSpec(
            rule_id="required_section_present",
            description="test rule",
            matches=lambda crit: "abstract" in crit.text.lower(),
            run=_noop_run,
        )
    )
    rng = random.Random(1234)
    type_pool = ["structural", "semantic", "structural", "semantic", "not-a-real-type"]
    text_pool = [
        "Has an Abstract",
        "Margins 1 inch",
        "Well-developed argument",
        "???",
        "APA citations",
    ]
    for _trial in range(200):
        n = rng.randint(0, 12)
        criteria = [
            FakeCriterion(id=i, type=rng.choice(type_pool), text=rng.choice(text_pool))
            for i in range(n)
        ]
        decisions = route_criteria(criteria)
        assert len(decisions) == n
        assert {d.criterion_id for d in decisions} == {c.id for c in criteria}
        for d in decisions:
            assert d.unroutable or d.kind in (CheckKind.structural, CheckKind.semantic)


def test_fixture_rubric_routes_exactly_as_expected_snapshot():
    """Fixture rubric snapshot test (ticket QA step): with NO rules
    registered (this file's `clean_registry` fixture isolates routing
    tests from the real V-016 registry on purpose), a small realistic
    rubric routes every structural criterion to a degraded semantic
    decision (honest fallback, proven independent of which real rules
    exist), and semantic criteria route directly."""
    criteria = [
        FakeCriterion(id=1, type="structural", text="Has an abstract"),
        FakeCriterion(id=2, type="structural", text="Bibliography uses APA format"),
        FakeCriterion(id=3, type="semantic", text="Argument in Chapter 4 is well developed"),
    ]
    decisions = route_criteria(criteria)
    assert [(d.kind, d.degraded, d.rule_id) for d in decisions] == [
        (CheckKind.semantic, True, None),
        (CheckKind.semantic, True, None),
        (CheckKind.semantic, False, None),
    ]
