"""V-030 unit tests: claim-support check — batching, wording discipline,
the hallucinated-quote guard (charter rule 1: never show unverifiable
evidence), and honest degradation on quota exhaustion. No DB — this
module never touches the database (pure function over `LLMClient`)."""

from dataclasses import dataclass
from typing import Any

from app.checks.citations.support import (
    CANNOT_DETERMINE_WORDING,
    POSSIBLY_UNSUPPORTED_WORDING,
    ClaimSupportInput,
    run_claim_support_check,
)
from app.config import get_settings
from app.errors import ApiDownError, QuotaExhaustedError
from app.models.enums import FlagSeverity


@dataclass
class FakeCitation:
    order_index: int


class ScriptedLLM:
    def __init__(self, responses: list[dict[str, Any] | Exception]):
        self._responses = list(responses)
        self.calls: list[str] = []

    async def complete(self, prompt_type, prompt, *, prompt_version="unversioned", **context):
        self.calls.append(prompt)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _pair(
    index: int, *, claim="A finding was reported.", abstract="We report a finding."
) -> ClaimSupportInput:
    return ClaimSupportInput(
        citation=FakeCitation(order_index=index), claim_sentence=claim, abstract=abstract
    )


async def test_supported_verdict_produces_no_flag():
    llm = ScriptedLLM(
        [{"verdicts": [{"index": 0, "verdict": "supported", "reasoning": "Matches."}]}]
    )
    result = await run_claim_support_check(llm, [_pair(0)], check_run_id=1, settings=get_settings())
    assert result.flags == []
    assert result.n_skipped_total == 0


async def test_possibly_unsupported_with_verified_excerpt_is_medium_severity():
    pair = _pair(
        0, claim="The study found X causes Y.", abstract="We found no relationship between X and Y."
    )
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "possibly_unsupported",
                        "reasoning": "The abstract reports no relationship, contradicting it.",
                        "abstract_excerpt": "no relationship between X and Y",
                    }
                ]
            }
        ]
    )
    result = await run_claim_support_check(llm, [pair], check_run_id=1, settings=get_settings())
    assert len(result.flags) == 1
    assert result.flags[0].severity == FlagSeverity.med
    assert result.flags[0].detail["kind"] == "claim_possibly_unsupported"
    expected = POSSIBLY_UNSUPPORTED_WORDING.format(
        claim=pair.claim_sentence, excerpt="no relationship between X and Y"
    )
    assert result.flags[0].detail["reason"] == expected


async def test_hallucinated_excerpt_downgrades_to_cannot_determine():
    """Charter rule 1: never show unverifiable evidence — a quote the
    model claims came from the abstract but doesn't actually appear there
    must not be presented as grounding for a medium-severity flag."""
    pair = _pair(0, abstract="We report a completely different finding.")
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "possibly_unsupported",
                        "reasoning": "Mismatch.",
                        "abstract_excerpt": "this text does not appear in the abstract",
                    }
                ]
            }
        ]
    )
    result = await run_claim_support_check(llm, [pair], check_run_id=1, settings=get_settings())
    assert len(result.flags) == 1
    assert result.flags[0].severity == FlagSeverity.low
    assert result.flags[0].detail["kind"] == "claim_support_cannot_determine"
    assert result.flags[0].detail["reason"] == CANNOT_DETERMINE_WORDING


async def test_cannot_determine_is_low_severity_honest_outcome():
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "cannot_determine",
                        "reasoning": "The claim cites a method the abstract doesn't describe.",
                    }
                ]
            }
        ]
    )
    result = await run_claim_support_check(llm, [_pair(0)], check_run_id=1, settings=get_settings())
    assert len(result.flags) == 1
    assert result.flags[0].severity == FlagSeverity.low
    assert result.n_skipped_total == 0


async def test_quota_exhausted_degrades_honestly_not_crash():
    llm = ScriptedLLM([QuotaExhaustedError("daily budget spent")])
    result = await run_claim_support_check(
        llm, [_pair(0), _pair(1)], check_run_id=1, settings=get_settings()
    )
    assert result.flags == []
    # Both pairs in the one exhausted batch are honestly counted, and
    # counted as QUOTA specifically -- not lumped with the other two causes.
    assert result.n_skipped_quota == 2
    assert result.n_skipped_api_down == 0
    assert result.n_skipped_parse_failure == 0


async def test_api_down_is_a_distinct_counter_from_quota():
    llm = ScriptedLLM([ApiDownError("provider unreachable")])
    result = await run_claim_support_check(llm, [_pair(0)], check_run_id=1, settings=get_settings())
    assert result.flags == []
    assert result.n_skipped_api_down == 1
    assert result.n_skipped_quota == 0
    assert result.n_skipped_parse_failure == 0


async def test_malformed_llm_output_counts_as_parse_failure_not_quota():
    """BUG-072's own regression test: a ClaimSupportError (D-017's exact
    defect class -- structured output that failed validation) must
    increment the parse-failure counter, never the quota counter. Before
    this fix, `except (QuotaExhaustedError, ApiDownError,
    ClaimSupportError): skipped += len(chunk)` made a real model-output
    regression permanently indistinguishable from ordinary budget
    exhaustion."""
    llm = ScriptedLLM([{"verdicts": "not a list, fails validation"}])
    result = await run_claim_support_check(llm, [_pair(0)], check_run_id=1, settings=get_settings())
    assert result.flags == []
    assert result.n_skipped_parse_failure == 1
    assert result.n_skipped_quota == 0
    assert result.n_skipped_api_down == 0


async def test_a_verdict_missing_from_an_otherwise_valid_batch_is_also_parse_failure():
    """The per-index `verdicts.get(i) is None` path (batch validated, but
    the model silently omitted one pair's verdict) is the SAME D-017
    defect class as a batch-level ClaimSupportError, not a different
    cause -- must count identically."""
    llm = ScriptedLLM(
        [{"verdicts": [{"index": 1, "verdict": "supported", "reasoning": "ok"}]}]  # omits index 0
    )
    result = await run_claim_support_check(
        llm, [_pair(0), _pair(1)], check_run_id=1, settings=get_settings()
    )
    assert result.n_skipped_parse_failure == 1
    assert result.n_skipped_quota == 0
    assert result.n_skipped_api_down == 0


async def test_batching_respects_max_pairs_per_call():
    settings = get_settings().model_copy(update={"claim_support_max_pairs_per_call": 2})
    pairs = [_pair(i) for i in range(5)]
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {"index": 0, "verdict": "supported", "reasoning": "ok"},
                    {"index": 1, "verdict": "supported", "reasoning": "ok"},
                ]
            },
            {
                "verdicts": [
                    {"index": 0, "verdict": "supported", "reasoning": "ok"},
                    {"index": 1, "verdict": "supported", "reasoning": "ok"},
                ]
            },
            {"verdicts": [{"index": 0, "verdict": "supported", "reasoning": "ok"}]},
        ]
    )
    await run_claim_support_check(llm, pairs, check_run_id=1, settings=settings)
    assert len(llm.calls) == 3  # 5 pairs / chunk size 2 -> 3 batched calls


async def test_empty_pairs_makes_no_llm_call():
    llm = ScriptedLLM([])
    result = await run_claim_support_check(llm, [], check_run_id=1, settings=get_settings())
    assert result.flags == []
    assert result.n_skipped_total == 0
    assert llm.calls == []


def test_wording_never_uses_accusatory_language():
    for wording in (POSSIBLY_UNSUPPORTED_WORDING, CANNOT_DETERMINE_WORDING):
        lowered = wording.lower()
        assert "fake" not in lowered
        assert "fabricat" not in lowered
        assert "dishonest" not in lowered
        assert "cheat" not in lowered
