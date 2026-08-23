"""V-035 unit tests: intent<->outcome pairing — candidate generation
(local embeddings), judgment (scripted Gemini), severity mapping, and
honest degradation on quota exhaustion. Same convention as V-030's
`test_checks_citations_support.py` (this module's closest architectural
sibling)."""

from typing import Any

from app.checks.agreement.extract import Statement
from app.checks.agreement.pair import (
    CONTRADICTORY_WORDING,
    UNMATCHED_INTENT_WORDING,
    generate_candidates,
    run_agreement_pairing,
)
from app.config import get_settings
from app.errors import ApiDownError, QuotaExhaustedError
from app.models.enums import FlagSeverity


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


def _intent(text: str, *, cue: str = "intent_list") -> Statement:
    return Statement(kind="intent", text=text, anchor="p. 1", page=1, paragraph=None, cue=cue)


def _outcome(text: str, *, cue: str = "outcome_list") -> Statement:
    return Statement(kind="outcome", text=text, anchor="p. 5", page=5, paragraph=None, cue=cue)


# --- candidate generation (no LLM involved) ------------------------------------


def test_similar_pair_becomes_a_candidate():
    intents = [_intent("To build a login module using school credentials.")]
    outcomes = [
        _outcome("The login module correctly authenticated 98% of test users."),
        _outcome("The cafeteria menu was updated every Monday."),
    ]
    candidates, unmatched, matched_idx = generate_candidates(
        intents, outcomes, similarity_floor=0.35, max_candidates_per_intent=3
    )
    assert len(candidates) == 1
    assert candidates[0].outcome.text.startswith("The login module")
    assert unmatched == []
    assert matched_idx == {0}


def test_intent_with_no_candidate_above_floor_is_unmatched():
    intents = [_intent("To build a login module using school credentials.")]
    outcomes = [_outcome("The cafeteria menu was updated every Monday.")]
    candidates, unmatched, matched_idx = generate_candidates(
        intents, outcomes, similarity_floor=0.99, max_candidates_per_intent=3
    )
    assert candidates == []
    assert unmatched == intents
    assert matched_idx == set()


def test_empty_intents_or_outcomes_short_circuits():
    assert generate_candidates(
        [], [_outcome("x")], similarity_floor=0.35, max_candidates_per_intent=3
    ) == (
        [],
        [],
        set(),
    )
    intents = [_intent("x")]
    candidates, unmatched, matched_idx = generate_candidates(
        intents, [], similarity_floor=0.35, max_candidates_per_intent=3
    )
    assert candidates == []
    assert unmatched == intents
    assert matched_idx == set()


# --- judgment + severity mapping (scripted LLM) --------------------------------


async def test_consistent_verdict_produces_no_flag():
    llm = ScriptedLLM(
        [{"verdicts": [{"index": 0, "verdict": "consistent", "reasoning": "Matches."}]}]
    )
    intents = [_intent("To build a login module.")]
    outcomes = [_outcome("The login module correctly authenticated 98% of test users.")]
    result = await run_agreement_pairing(
        llm, intents, outcomes, check_run_id=1, settings=get_settings()
    )
    assert result.flags == []


async def test_contradictory_verdict_is_high_severity():
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "contradictory",
                        "reasoning": "The finding says it was NOT implemented.",
                    }
                ]
            }
        ]
    )
    intents = [_intent("To build a login module using school credentials.")]
    outcomes = [_outcome("The login module was not implemented due to time constraints.")]
    result = await run_agreement_pairing(
        llm, intents, outcomes, check_run_id=1, settings=get_settings()
    )
    assert len(result.flags) == 1
    flag = result.flags[0]
    assert flag.severity == FlagSeverity.high
    assert flag.detail["kind"] == "agreement_contradictory"
    assert flag.detail["reason"] == CONTRADICTORY_WORDING.format(
        intent=intents[0].text, outcome=outcomes[0].text
    )


async def test_partial_verdict_is_low_severity_not_binary():
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {"index": 0, "verdict": "partial", "reasoning": "Only 3 of 4 modules built."}
                ]
            }
        ]
    )
    intents = [_intent("To build four integrity checks.")]
    outcomes = [_outcome("Three of the four integrity checks were completed.")]
    result = await run_agreement_pairing(
        llm, intents, outcomes, check_run_id=1, settings=get_settings()
    )
    assert len(result.flags) == 1
    assert result.flags[0].severity == FlagSeverity.low
    assert result.flags[0].detail["kind"] == "agreement_partial"


async def test_cannot_determine_is_low_severity_honest_outcome():
    llm = ScriptedLLM(
        [{"verdicts": [{"index": 0, "verdict": "cannot_determine", "reasoning": "Ambiguous."}]}]
    )
    intents = [_intent("To build a login module.")]
    outcomes = [_outcome("The login module correctly authenticated 98% of test users.")]
    result = await run_agreement_pairing(
        llm, intents, outcomes, check_run_id=1, settings=get_settings()
    )
    assert len(result.flags) == 1
    assert result.flags[0].severity == FlagSeverity.low
    assert result.flags[0].detail["kind"] == "agreement_cannot_determine"


async def test_unmatched_intent_is_low_severity_early_warning_no_llm_call():
    """Ticket AC: a claimed feature absent later -> low flag. No candidate
    clears the floor, so no Gemini call is spent at all (quota discipline)."""
    llm = ScriptedLLM([])
    intents = [_intent("To build a biometric login module.")]
    outcomes = [_outcome("The cafeteria menu was updated every Monday.")]
    result = await run_agreement_pairing(
        llm,
        intents,
        outcomes,
        check_run_id=1,
        settings=get_settings().model_copy(update={"agreement_pairing_similarity_floor": 0.99}),
    )
    assert len(result.flags) == 1
    assert result.flags[0].severity == FlagSeverity.low
    assert result.flags[0].detail["kind"] == "agreement_unmatched_intent"
    assert result.flags[0].detail["reason"] == UNMATCHED_INTENT_WORDING.format(
        intent=intents[0].text
    )
    assert llm.calls == []


async def test_unmatched_outcome_is_a_count_not_a_flag():
    """Ticket AC: unmatched OUTCOMES are an info-level note, never a flag —
    papers legitimately report extras beyond their stated objectives."""
    llm = ScriptedLLM(
        [{"verdicts": [{"index": 0, "verdict": "consistent", "reasoning": "Matches."}]}]
    )
    intents = [_intent("To build a login module.")]
    outcomes = [
        _outcome("The login module correctly authenticated 98% of test users."),
        _outcome("An unrelated extra finding about server uptime."),
    ]
    result = await run_agreement_pairing(
        llm,
        intents,
        outcomes,
        check_run_id=1,
        settings=get_settings().model_copy(
            update={"agreement_pairing_max_candidates_per_intent": 1}
        ),
    )
    assert result.flags == []
    assert result.n_unmatched_outcomes == 1


async def test_quota_exhausted_degrades_honestly_not_crash():
    llm = ScriptedLLM([QuotaExhaustedError("daily budget spent")])
    intents = [_intent("To build a login module.")]
    outcomes = [_outcome("The login module correctly authenticated 98% of test users.")]
    result = await run_agreement_pairing(
        llm, intents, outcomes, check_run_id=1, settings=get_settings()
    )
    assert result.flags == []
    assert result.n_skipped_quota == 1
    assert result.n_skipped_api_down == 0
    assert result.n_skipped_parse_failure == 0


async def test_api_down_is_a_distinct_counter_from_quota():
    llm = ScriptedLLM([ApiDownError("provider unreachable")])
    intents = [_intent("To build a login module.")]
    outcomes = [_outcome("The login module correctly authenticated 98% of test users.")]
    result = await run_agreement_pairing(
        llm, intents, outcomes, check_run_id=1, settings=get_settings()
    )
    assert result.flags == []
    assert result.n_skipped_api_down == 1
    assert result.n_skipped_quota == 0
    assert result.n_skipped_parse_failure == 0


async def test_malformed_llm_output_counts_as_parse_failure_not_quota():
    """BUG-072's own regression test, F4's side: a batch-level structured-
    output validation failure (D-017's defect class, raised as
    `PairingError` inside `_judge_batch`) must increment the
    parse-failure counter, never the quota counter."""
    llm = ScriptedLLM([{"verdicts": "not a list, fails validation"}])
    intents = [_intent("To build a login module.")]
    outcomes = [_outcome("The login module correctly authenticated 98% of test users.")]
    result = await run_agreement_pairing(
        llm, intents, outcomes, check_run_id=1, settings=get_settings()
    )
    assert result.flags == []
    assert result.n_skipped_parse_failure == 1
    assert result.n_skipped_quota == 0
    assert result.n_skipped_api_down == 0


async def test_empty_intents_and_outcomes_makes_no_llm_call():
    llm = ScriptedLLM([])
    result = await run_agreement_pairing(llm, [], [], check_run_id=1, settings=get_settings())
    assert result.flags == []
    assert result.n_unmatched_outcomes == 0
    assert llm.calls == []


def test_wording_never_uses_accusatory_language():
    for wording in (UNMATCHED_INTENT_WORDING, CONTRADICTORY_WORDING):
        lowered = wording.lower()
        assert "fake" not in lowered
        assert "fabricat" not in lowered
        assert "lied" not in lowered
        assert "dishonest" not in lowered
