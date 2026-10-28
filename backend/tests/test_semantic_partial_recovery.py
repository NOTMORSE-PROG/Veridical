"""F3: an invalid item's content cannot become a vote or erase a valid sibling."""

import pytest

from app.checks.semantic import (
    SemanticBatch,
    SemanticGradeError,
    _indexed_candidates,
    grade_batch_verdicts,
)
from app.ingest.schemas import TextBlock
from tests.test_checks_semantic import FakeCriterion


def candidate(index, *, quotes=None, verdict="pass"):
    return {
        "index": index,
        "verdict": verdict,
        "reasoning": f"Recorded explanation {index}",
        "evidence_quotes": (
            [f"Unique saved source statement {index}."] if quotes is None else quotes
        ),
    }


def fixture():
    criteria = [FakeCriterion(index + 1, f"Custom requirement {index}") for index in range(8)]
    batch = SemanticBatch(
        "whole document",
        [
            TextBlock(
                page=7,
                text=" ".join(f"Unique saved source statement {index}." for index in range(8)),
                max_font_size=11,
                bold_ratio=0,
            )
        ],
    )
    return batch, criteria


class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, prompt_type, prompt, *, prompt_version, **context):
        self.calls.append((prompt_type, prompt, prompt_version, context))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


async def test_two_invalid_items_repair_only_their_own_pass_and_preserve_six_siblings():
    batch, criteria = fixture()
    initial = [candidate(index, quotes=[] if index in {6, 7} else None) for index in range(8)]
    repaired = candidate(6)
    repaired["index"] = 0
    llm = Scripted(
        [{"verdicts": initial}, {"verdicts": [repaired]}, {"verdicts": [candidate(0, quotes=[])]}]
    )
    out = await grade_batch_verdicts(
        batch,
        criteria,
        llm,
        None,
        "page",
        consistency_pass="pass_2",
        prompt_version="v4",
        annotator_stance="INDEPENDENT_REVIEWER_STANCE",
    )
    assert len(llm.calls) == 3
    for index in range(6):
        assert out[index + 1].verdict == "pass"
        assert out[index + 1].reasoning == f"Recorded explanation {index}"
        assert out[index + 1].quotes == [f"Unique saved source statement {index}."]
        assert out[index + 1].anchors == ["page 7"]
    assert out[7].verdict == "pass" and out[7].quotes == ["Unique saved source statement 6."]
    assert out[8].verdict is None
    assert "could not be validated" in out[8].escalation_reason
    for _kind, prompt, version, context in llm.calls:
        assert version == "v4" and "INDEPENDENT_REVIEWER_STANCE" in prompt
        assert context["consistency_pass"] == "pass_2"
        assert context["grading_response_contract"] == "indexed-item-recovery-v1"
    assert [call[3]["recovery_phase"] for call in llm.calls] == [
        "batch",
        "criterion_repair",
        "criterion_repair",
    ]


@pytest.mark.parametrize("index", [True, False, 0.0, "0", -1, 2, None])
def test_ambiguous_or_out_of_range_indices_reject_the_whole_response(index):
    with pytest.raises(SemanticGradeError):
        _indexed_candidates({"verdicts": [candidate(0), candidate(index, quotes=[])]}, 2)


def test_duplicate_invalid_index_is_still_global_and_permuted_unique_indices_are_stable():
    with pytest.raises(SemanticGradeError):
        _indexed_candidates({"verdicts": [candidate(0), candidate(0, quotes=[])]}, 2)
    out = _indexed_candidates({"verdicts": [candidate(1), candidate(0, quotes=[])]}, 2)
    assert [item.index for item in out.verdicts] == [1]
    assert out.invalid_indices == (0,)


async def test_uncertainty_is_withholding_and_does_not_spend_an_item_repair():
    batch, criteria = fixture()
    llm = Scripted(
        [{"verdicts": [candidate(index, quotes=[], verdict="uncertain") for index in range(8)]}]
    )
    out = await grade_batch_verdicts(batch, criteria, llm, None, "page")
    assert len(llm.calls) == 1
    assert all(item.verdict is None and item.anchors is None for item in out.values())


async def test_item_recovery_limit_withholds_remaining_items_without_more_calls(monkeypatch):
    monkeypatch.setenv("SEMANTIC_ITEM_RECOVERY_MAX_CRITERIA", "1")
    batch, criteria = fixture()
    repaired = candidate(6)
    repaired["index"] = 0
    llm = Scripted(
        [
            {
                "verdicts": [
                    candidate(index, quotes=[] if index >= 6 else None) for index in range(8)
                ]
            },
            {"verdicts": [repaired]},
        ]
    )
    out = await grade_batch_verdicts(batch, criteria, llm, None, "page")
    assert len(llm.calls) == 2
    assert out[7].verdict == "pass"
    assert out[8].verdict is None and "budget was reached" in out[8].escalation_reason


async def test_provider_interruption_during_recovery_propagates_without_a_partial_return():
    from app.errors import QuotaExhaustedError

    batch, criteria = fixture()
    llm = Scripted(
        [
            {
                "verdicts": [
                    candidate(index, quotes=[] if index == 7 else None) for index in range(8)
                ]
            },
            QuotaExhaustedError("Controlled quota"),
        ]
    )
    with pytest.raises(QuotaExhaustedError):
        await grade_batch_verdicts(batch, criteria, llm, None, "page")
    assert len(llm.calls) == 2
