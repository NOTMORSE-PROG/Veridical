"""Owner decision 2026-10-29: GRADING_DECISION_POLICY="decide" grades every
criterion the AI can ground in the manuscript; the instructor changes any
grade instead of deciding each one."""

import pytest

from app.checks.consistency import run_semantic_checks_with_consistency
from app.checks.escalation import gate_vote
from app.checks.support import apply_support, locatable_subclaim
from app.config import get_settings
from app.models.enums import ResultOutcome
from tests.test_checks_consistency import (
    TIP_SCALE,
    FakeCriterion,
    FakeSession,
    ScriptedLLM,
    _extraction,
    _verdict_response,
)


@pytest.fixture
def decide(monkeypatch):
    monkeypatch.setenv("GRADING_DECISION_POLICY", "decide")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def escalate(monkeypatch):
    monkeypatch.setenv("GRADING_DECISION_POLICY", "escalate")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _unverifiable(verdict: str) -> dict:
    return {
        "verdicts": [
            {
                "index": 0,
                "verdict": verdict,
                "reasoning": f"Reasoned to {verdict}.",
                "evidence_quotes": ["A sentence that never appears in this manuscript at all."],
            }
        ]
    }


async def _grade(responses, levels=TIP_SCALE):
    llm = ScriptedLLM(responses)
    results = await run_semantic_checks_with_consistency(
        FakeSession(),
        1,
        [FakeCriterion(id=1, text="Introduction", levels=levels)],
        _extraction(),
        llm,
    )
    return results[0], llm


async def test_majority_level_is_decided(decide):
    result, llm = await _grade(
        [
            _verdict_response("Proficient"),
            _verdict_response("Acceptable"),
            _verdict_response("Proficient"),
        ]
    )
    assert llm.passes == ["pass_1", "pass_2", "tie_break"]
    assert result.outcome == ResultOutcome.passed
    assert result.detail["level"]["name"] == "Proficient"
    assert "decision_basis" not in result.detail


async def test_three_way_split_takes_the_middle_level(decide):
    result, _ = await _grade(
        [
            _verdict_response("Beginner"),
            _verdict_response("Exemplary"),
            _verdict_response("Proficient"),
        ]
    )
    assert result.detail["level"]["name"] == "Proficient"
    assert result.detail["decision_basis"] == "middle_of_three"


async def test_uncertain_tie_break_abstains_and_the_lower_level_is_taken(decide):
    result, llm = await _grade(
        [
            _verdict_response("Exemplary"),
            _verdict_response("Proficient"),
            _verdict_response("uncertain"),
        ]
    )
    assert llm.passes == ["pass_1", "pass_2", "tie_break"]
    assert result.outcome == ResultOutcome.passed
    assert result.detail["level"]["name"] == "Proficient"
    assert result.detail["decision_basis"] == "lower_of_two"


async def test_uncertain_tie_break_still_escalates_when_not_deciding(escalate):
    result, _ = await _grade(
        [
            _verdict_response("Exemplary"),
            _verdict_response("Proficient"),
            _verdict_response("uncertain"),
        ]
    )
    assert result.outcome == ResultOutcome.escalated


async def test_unverifiable_tie_break_takes_the_lower_verified_level(decide):
    result, llm = await _grade(
        [
            _verdict_response("Exemplary"),
            _verdict_response("Acceptable"),
            _unverifiable("Proficient"),
        ]
    )
    assert llm.passes == ["pass_1", "pass_2", "tie_break"]
    assert result.detail["level"]["name"] == "Acceptable"
    assert result.detail["decision_basis"] == "lower_of_two"


async def test_one_verified_pass_decides(decide):
    result, llm = await _grade(
        [_unverifiable("Exemplary"), _unverifiable("Exemplary"), _verdict_response("Acceptable")]
    )
    assert llm.passes == ["pass_1", "pass_1", "pass_2"]
    assert result.detail["level"]["name"] == "Acceptable"
    assert result.detail["decision_basis"] == "single_verified_pass"


async def test_no_verified_pass_still_waits_for_the_instructor(decide):
    result, _ = await _grade(
        [
            _unverifiable("Exemplary"),
            _unverifiable("Exemplary"),
            _unverifiable("Proficient"),
            _unverifiable("Proficient"),
        ]
    )
    assert result.outcome == ResultOutcome.escalated
    assert "level" not in result.detail


async def test_escalate_policy_keeps_the_earlier_behavior(escalate):
    result, _ = await _grade(
        [
            _verdict_response("Proficient"),
            _verdict_response("Acceptable"),
            _verdict_response("Proficient"),
        ]
    )
    assert result.outcome == ResultOutcome.escalated


def test_decide_policy_ignores_the_agreement_threshold(decide):
    assert gate_vote("pass", 0.5, get_settings())[0] == ResultOutcome.passed
    assert gate_vote(None, 0.0, get_settings())[0] == ResultOutcome.escalated


@pytest.mark.parametrize("decide_flag", [True, False])
def test_unconfirmed_support_is_a_caution_only_when_deciding(decide_flag):
    detail = {"verdict": "Proficient", "level": {"name": "Proficient"}, "reasoning": "r"}
    support = {"status": "not_supported", "reason": "The quote does not show the preview."}
    outcome, updated = apply_support(ResultOutcome.passed, detail, support, decide=decide_flag)
    if decide_flag:
        assert outcome == ResultOutcome.passed and updated["level"] == {"name": "Proficient"}
        assert updated["support_caution"] == support["reason"]
    else:
        assert outcome == ResultOutcome.escalated and "level" not in updated


STRUCTURE_SCALE = [
    {"level": 1, "name": "Beginner", "descriptor": "No clear introduction.", "points": 1},
    {"level": 2, "name": "Acceptable", "descriptor": "States the main topic.", "points": 2},
    {
        "level": 3,
        "name": "Proficient",
        "descriptor": "States the main topic and previews the structure of the paper.",
        "points": 3,
    },
    {"level": 4, "name": "Exemplary", "descriptor": "Engaging and complete.", "points": 4},
]
UNSUPPORTED = {"status": "unsupported", "reason": "The quote does not show the preview."}


@pytest.mark.parametrize(
    "criterion,verdict,expected",
    [
        (FakeCriterion(id=1, text="Introduction", levels=STRUCTURE_SCALE), "Proficient", True),
        (FakeCriterion(id=1, text="Introduction", levels=STRUCTURE_SCALE), "Acceptable", False),
        (FakeCriterion(id=2, text="Purpose is stated in a single sentence."), "pass", True),
        (
            FakeCriterion(id=3, text="Ideas flow logically", evidence="Preview the structure"),
            "pass",
            True,
        ),
        (FakeCriterion(id=4, text="Organization is logical and easy to follow."), "pass", False),
        (FakeCriterion(id=5, text="The tone is professional throughout."), "pass", False),
    ],
)
def test_locatable_subclaims_come_from_the_criterion_wording(criterion, verdict, expected):
    """Read from the criterion's text, its evidence request and the claimed
    level's descriptor, never from its name."""
    assert locatable_subclaim(criterion, {"verdict": verdict}) is expected


def test_an_unshown_locatable_subclaim_goes_to_the_instructor(decide):
    criterion = FakeCriterion(id=1, text="Introduction", levels=STRUCTURE_SCALE)
    detail = {"verdict": "Proficient", "level": {"name": "Proficient"}, "reasoning": "r"}
    outcome, updated = apply_support(
        ResultOutcome.passed,
        detail,
        UNSUPPORTED,
        decide=True,
        strict=locatable_subclaim(criterion, detail),
    )
    assert outcome == ResultOutcome.escalated
    assert "level" not in updated and updated["proposed_level"] == {"name": "Proficient"}
    assert updated["support_gate"] == "locatable_subclaim"
    assert "support_caution" not in updated


def test_a_whole_document_criterion_keeps_its_grade_with_a_caution(decide):
    criterion = FakeCriterion(id=4, text="Organization is logical and easy to follow.")
    detail = {"verdict": "pass", "reasoning": "r"}
    outcome, updated = apply_support(
        ResultOutcome.passed,
        detail,
        UNSUPPORTED,
        decide=True,
        strict=locatable_subclaim(criterion, detail),
    )
    assert outcome == ResultOutcome.passed
    assert updated["support_caution"] == UNSUPPORTED["reason"]
    assert "support_gate" not in updated


def test_a_supported_locatable_subclaim_is_still_decided(decide):
    criterion = FakeCriterion(id=1, text="Introduction", levels=STRUCTURE_SCALE)
    detail = {"verdict": "Proficient", "level": {"name": "Proficient"}, "reasoning": "r"}
    support = {"status": "supported", "reason": "The quote previews the chapters."}
    outcome, updated = apply_support(
        ResultOutcome.passed,
        detail,
        support,
        decide=True,
        strict=locatable_subclaim(criterion, detail),
    )
    assert outcome == ResultOutcome.passed and updated["level"] == {"name": "Proficient"}


async def test_the_pipeline_escalates_an_unshown_structure_preview(decide):
    """End to end through both grading passes and the support check."""
    import json

    class Unshown(ScriptedLLM):
        async def complete(self, prompt_type, prompt, **kwargs):
            if prompt_type != "semantic_support":
                return await super().complete(prompt_type, prompt, **kwargs)
            task = json.loads(prompt.split("--- TASK JSON ---\n", 1)[1])
            return {
                "items": [
                    {
                        "index": item["index"],
                        "status": "unsupported",
                        "reason": "No preview of the structure is shown.",
                        "requirements": [
                            {
                                "key": obligation["key"],
                                "status": "unsupported",
                                "evidence_quotes": [],
                            }
                            for obligation in item["required_obligations"]
                        ],
                    }
                    for item in task["items"]
                ]
            }

    llm = Unshown([_verdict_response("Proficient"), _verdict_response("Proficient")])
    results = await run_semantic_checks_with_consistency(
        FakeSession(),
        1,
        [FakeCriterion(id=1, text="Introduction", levels=STRUCTURE_SCALE)],
        _extraction(),
        llm,
    )
    assert results[0].outcome == ResultOutcome.escalated
    assert results[0].detail["support_gate"] == "locatable_subclaim"


def test_support_outage_is_never_a_decided_grade():
    support = {
        "status": "unavailable",
        "reason": "The support service was unavailable.",
        "failure_outcome": ResultOutcome.api_down.value,
        "failure_phase": "support",
    }
    outcome, _ = apply_support(ResultOutcome.passed, {"verdict": "pass"}, support, decide=True)
    assert outcome == ResultOutcome.api_down


def test_report_rows_explain_an_automatic_grade_until_an_instructor_judges():
    from types import SimpleNamespace

    from app.report.service import _automatic_grade

    decided = SimpleNamespace(outcome=ResultOutcome.passed)
    detail = {
        "basis": "llm",
        "verdict": "Proficient",
        "votes": ["Proficient", "Acceptable", "Proficient"],
        "support_caution": "The quote shows the topic but not the preview.",
    }
    assert _automatic_grade(decided, detail, None) == {
        "decision_basis": None,
        "support_caution": "The quote shows the topic but not the preview.",
        "agreeing_passes": 2,
        "grading_passes": 3,
        "votes": ["Proficient", "Acceptable", "Proficient"],
    }
    # A pass that gave no usable grade keeps its place as None, and anything
    # stored that is not text reads the same way rather than breaking the row.
    gap = {**detail, "votes": ["Proficient", None, "Proficient"]}
    assert _automatic_grade(decided, gap, None)["votes"] == ["Proficient", None, "Proficient"]
    odd = {**detail, "votes": ["Proficient", 3, "Proficient"]}
    assert _automatic_grade(decided, odd, None)["votes"] == ["Proficient", None, "Proficient"]
    assert _automatic_grade(decided, {**detail, "votes": []}, None)["votes"] is None
    split = {**detail, "decision_basis": "middle_of_three"}
    assert _automatic_grade(decided, split, None)["decision_basis"] == "middle_of_three"
    assert _automatic_grade(decided, detail, {"type": "mark_level"}) == {}
    assert _automatic_grade(SimpleNamespace(outcome=ResultOutcome.escalated), detail, None) == {}


def test_report_rows_drop_an_old_no_grade_clause_beside_a_kept_grade():
    """Older decide-mode cautions could end "no automatic grade was
    assigned". The report row is what the screen and the adviser view read,
    so it must not carry that clause beside the grade it kept."""
    from types import SimpleNamespace

    from app.report.service import _automatic_grade

    detail = {
        "basis": "llm",
        "verdict": "pass",
        "votes": ["pass", "pass", "pass"],
        "support_caution": (
            "Review the criterion and its performance level directly; "
            "no automatic grade was assigned."
        ),
    }
    row = _automatic_grade(SimpleNamespace(outcome=ResultOutcome.passed), detail, None)
    assert row["support_caution"] == "Review the criterion and its performance level directly."
    only_the_clause = {**detail, "support_caution": "No automatic grade was assigned."}
    row = _automatic_grade(SimpleNamespace(outcome=ResultOutcome.passed), only_the_clause, None)
    assert row["support_caution"] is None
