"""BUG-515: a criterion left ungraded says why with a stable code, so a quote
already used for another criterion is not shown as missing from the source."""

from app.checks.consistency import run_semantic_checks_with_consistency
from app.models.enums import ResultOutcome
from tests.test_checks_consistency import FakeCriterion, FakeSession, ScriptedLLM, _extraction

SENTENCE = "The methodology is described in detail."


def _verdicts(*items):
    return {
        "verdicts": [
            {"index": index, "verdict": "pass", "reasoning": "r", "evidence_quotes": [quote]}
            for index, quote in items
        ]
    }


def _single(quote):
    return _verdicts((0, quote))


async def test_a_quote_already_used_by_another_criterion_is_coded_as_such():
    criteria = [FakeCriterion(id=1, text="Methods"), FakeCriterion(id=2, text="Design")]
    llm = ScriptedLLM(
        [
            _verdicts((0, SENTENCE), (1, SENTENCE)),
            _single(SENTENCE),
            _verdicts((0, SENTENCE), (1, SENTENCE)),
            _single(SENTENCE),
        ]
    )
    results = await run_semantic_checks_with_consistency(
        FakeSession(), 1, criteria, _extraction(), llm
    )
    second = next(result for result in results if result.criterion_id == 2)
    assert second.outcome == ResultOutcome.escalated
    assert second.detail["reason_codes"] == ["evidence_already_used"]
    assert ".;" not in second.detail["reason"]


async def test_a_quote_missing_from_the_source_is_coded_as_not_located():
    missing = "A sentence that never appears in this manuscript at all."
    llm = ScriptedLLM([_single(missing), _single(missing), _single(missing), _single(missing)])
    [result] = await run_semantic_checks_with_consistency(
        FakeSession(), 1, [FakeCriterion(id=1, text="Methods")], _extraction(), llm
    )
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["reason_codes"] == ["evidence_not_located"]
