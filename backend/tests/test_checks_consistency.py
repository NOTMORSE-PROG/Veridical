"""V-022 unit tests: N=2 self-consistency voting + conditional tie-break,
and V-023's threshold gate. No DB (same `FakeSession` convention as
test_checks_semantic.py).
"""

from dataclasses import dataclass
from typing import Any

import pytest

from app.checks.consistency import run_semantic_checks_with_consistency
from app.checks.escalation import gate_vote
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.enums import ResultOutcome


@dataclass
class FakeCriterion:
    id: int
    text: str
    evidence: str | None = None


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None


class ScriptedLLM:
    """Returns queued responses in order, one per `complete()` call, and
    records the `consistency_pass` each call was made with — lets these
    tests assert exactly which passes fired (2 vs 3 calls)."""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)
        self.passes: list[str | None] = []

    async def complete(self, prompt_type, prompt, *, prompt_version="unversioned", **context):
        self.passes.append(context.get("consistency_pass"))
        return self._responses.pop(0)


def _extraction() -> ExtractionResult:
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [
        TextBlock(
            page=1,
            text="The methodology is described in detail.",
            max_font_size=11,
            bold_ratio=0.0,
        )
    ]
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=tree,
        blocks=blocks,
        images=[],
    )


def _verdict_response(verdict: str, index: int = 0) -> dict[str, Any]:
    return {
        "verdicts": [
            {
                "index": index,
                "verdict": verdict,
                "reasoning": f"Reasoned to {verdict}.",
                "evidence_quotes": ["The methodology is described in detail."],
            }
        ]
    }


# --- gate_vote (pure) --------------------------------------------------------------


def test_gate_vote_escalates_when_no_majority():
    outcome, score = gate_vote(None, 0.333, get_settings())
    assert outcome == ResultOutcome.escalated
    assert score is None


def test_gate_vote_escalates_below_default_strict_threshold():
    # Default threshold is 1.0: even a clean 2/3 majority escalates.
    outcome, score = gate_vote("pass", 0.667, get_settings())
    assert outcome == ResultOutcome.escalated
    assert score is None


def test_gate_vote_auto_accepts_perfect_agreement():
    outcome, score = gate_vote("pass", 1.0, get_settings())
    assert outcome == ResultOutcome.passed
    assert score == 100.0


def test_gate_vote_loosened_threshold_accepts_majority():
    settings = get_settings().model_copy(update={"escalation_agreement_threshold": 0.6})
    outcome, score = gate_vote("fail", 0.667, settings)
    assert outcome == ResultOutcome.failed
    assert score == 0.0


# --- run_semantic_checks_with_consistency -----------------------------------------


async def test_two_agreeing_passes_never_spend_a_tie_break_call():
    criteria = [FakeCriterion(id=1, text="Some criterion")]
    llm = ScriptedLLM([_verdict_response("pass"), _verdict_response("pass")])
    session = FakeSession()
    results = await run_semantic_checks_with_consistency(session, 1, criteria, _extraction(), llm)
    assert len(llm.passes) == 2
    assert llm.passes == ["pass_1", "pass_2"]
    assert results[0].outcome == ResultOutcome.passed
    assert results[0].score == 100.0
    assert results[0].detail["agreement"] == 1.0
    assert results[0].detail["votes"] == ["pass", "pass"]


async def test_disagreement_spends_exactly_one_tie_break_call():
    criteria = [FakeCriterion(id=1, text="Some criterion")]
    llm = ScriptedLLM(
        [_verdict_response("pass"), _verdict_response("fail"), _verdict_response("pass")]
    )
    session = FakeSession()
    results = await run_semantic_checks_with_consistency(session, 1, criteria, _extraction(), llm)
    assert llm.passes == ["pass_1", "pass_2", "tie_break"]
    # 2/3 majority formed ("pass"), but default threshold (1.0) still
    # escalates ANY disagreement — never a majority-by-technicality auto-accept.
    assert results[0].outcome == ResultOutcome.escalated
    assert results[0].detail["agreement"] == pytest.approx(0.667, abs=0.001)
    assert results[0].detail["votes"] == ["pass", "fail", "pass"]
    # AI's own majority call is preserved for the panel:
    assert results[0].detail["verdict"] == "pass"


async def test_three_way_split_has_no_majority_and_escalates():
    criteria = [FakeCriterion(id=1, text="Some criterion")]
    llm = ScriptedLLM(
        [_verdict_response("pass"), _verdict_response("fail"), _verdict_response("partial")]
    )
    session = FakeSession()
    results = await run_semantic_checks_with_consistency(session, 1, criteria, _extraction(), llm)
    assert llm.passes == ["pass_1", "pass_2", "tie_break"]
    assert results[0].outcome == ResultOutcome.escalated
    assert results[0].detail["agreement"] == pytest.approx(0.333, abs=0.001)
    assert "verdict" not in results[0].detail  # no AI majority to show


async def test_no_semantic_criteria_makes_no_llm_call():
    llm = ScriptedLLM([])
    session = FakeSession()
    results = await run_semantic_checks_with_consistency(session, 1, [], _extraction(), llm)
    assert results == []
    assert llm.passes == []


async def test_pass_failing_to_grade_at_all_escalates_without_a_tie_break():
    # pass_1 comes back malformed (fails the whole-batch AND single-
    # criterion retry ladder) — voting can't proceed, so there's nothing
    # to tie-break against; this must not spend a third call.
    criteria = [FakeCriterion(id=1, text="Some criterion")]
    llm = ScriptedLLM(
        [
            {"not_verdicts": []},  # pass_1: batch attempt (malformed)
            {"not_verdicts": []},  # pass_1: whole-batch retry (still malformed)
            _verdict_response("pass"),  # pass_2
        ]
    )
    session = FakeSession()
    results = await run_semantic_checks_with_consistency(session, 1, criteria, _extraction(), llm)
    assert llm.passes == ["pass_1", "pass_1", "pass_2"]
    assert results[0].outcome == ResultOutcome.escalated
    assert results[0].detail["agreement"] == 0.0


async def test_both_passes_failing_with_the_same_reason_does_not_duplicate_it():
    # The common real case: both grading passes fail identically (same
    # manuscript, same unverifiable quote) — a naive "; ".join produced a
    # literal doubled sentence in real report output (V-055 4h review).
    criteria = [FakeCriterion(id=1, text="Some criterion")]
    llm = ScriptedLLM(
        [
            {"not_verdicts": []},  # pass_1: batch attempt (malformed)
            {"not_verdicts": []},  # pass_1: whole-batch retry (still malformed)
            {"not_verdicts": []},  # pass_2: batch attempt (malformed)
            {"not_verdicts": []},  # pass_2: whole-batch retry (still malformed)
        ]
    )
    session = FakeSession()
    results = await run_semantic_checks_with_consistency(session, 1, criteria, _extraction(), llm)
    assert llm.passes == ["pass_1", "pass_1", "pass_2", "pass_2"]
    assert results[0].outcome == ResultOutcome.escalated
    assert results[0].detail["reason"] == "Grading response could not be validated after a retry."
