"""Factual confirmation needs its own source evidence and assessment receipt."""

import copy

import pytest

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import Statement
from app.checks.agreement.pair import Candidate, judge_pairs
from app.config import Settings
from app.errors import QuotaExhaustedError

INTENT = "To implement offline receipts. The receipt module was implemented."
OUTCOME = "The receipt module was not implemented."
ANSWER = dict(
    index=0,
    verdict="contradictory",
    reasoning="Opposite completed-work claims.",
    reason_category="incompatible_descriptive_claims",
    objective_quote="The receipt module was implemented",
    finding_quote="The receipt module was not implemented",
    reported_attainment="unknown",
    context_basis="none",
)


class Replies:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    async def complete(self, *args, **kwargs):
        self.calls += 1
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return {"verdicts": [reply]}


async def compare(*replies):
    client = Replies(replies)
    candidate = Candidate(
        Statement("intent", INTENT, "p. 2", 2, None, "intent_list"),
        Statement("outcome", OUTCOME, "p. 8", 8, None, "outcome_prose"),
        0.9,
    )
    judged, counters = await judge_pairs(
        client, [candidate], check_run_id=None, settings=Settings(_env_file=None), cues=load_cues()
    )
    return judged[0], counters, client


@pytest.mark.parametrize("field", ["objective_quote", "finding_quote"])
async def test_a_confirmation_with_an_unbound_quote_cannot_support_a_conflict(field):
    repeat = dict(ANSWER, **{field: "A different manuscript says this module was deployed."})
    item, _, client = await compare(ANSWER, repeat)
    assert client.calls == 2
    assert item.final == "cannot_determine"
    assert item.downgrade == "confirmation_evidence_unverified"


async def test_confirmation_attainment_disagreement_is_retained_without_overwriting_either_answer():
    original = copy.deepcopy(ANSWER)
    repeat = dict(ANSWER, reported_attainment="unmet")
    item, _, _ = await compare(original, repeat)
    assert item.final == "contradictory"
    assert item.verdict.reported_attainment == "unknown"
    assert item.confirmation_readings[0]["reported_attainment"] == "unmet"
    assert item.confirmation_readings[0]["source_evidence_verified"] is True
    assert original == ANSWER


async def test_failed_confirmation_is_separate_from_a_completed_initial_comparison():
    item, counts, client = await compare(ANSWER, QuotaExhaustedError("quota"))
    assert client.calls == 2 and counts["quota"] == 1
    assert item.verdict is not None and item.final == "cannot_determine"
    assert item.confirmation_readings[0]["unassessed_reason"] == "quota"


async def test_initial_quota_failure_names_the_unassessed_selected_pair():
    item, counts, client = await compare(QuotaExhaustedError("quota"))
    assert client.calls == 1 and counts["quota"] == 1
    assert item.verdict is None and item.unassessed_reason == "quota"


@pytest.mark.parametrize("majority", [False, True])
async def test_quota_rejection_stops_before_any_further_confirmation_request(majority):
    client = Replies([ANSWER, QuotaExhaustedError("quota"), ANSWER])
    candidate = Candidate(
        Statement("intent", INTENT, "p. 2", 2, None, "intent_list"),
        Statement("outcome", OUTCOME, "p. 8", 8, None, "outcome_prose"),
        0.9,
    )
    settings = Settings(_env_file=None).model_copy(
        update={"agreement_contradiction_majority_of_three": majority}
    )
    judged, counts = await judge_pairs(
        client, [candidate], check_run_id=None, settings=settings, cues=load_cues()
    )
    assert client.calls == 2 and counts["quota"] == 1
    assert judged[0].final == "cannot_determine"


async def test_quota_rejection_in_a_later_initial_batch_does_not_reask_an_earlier_pair():
    client = Replies([ANSWER, QuotaExhaustedError("quota"), ANSWER])
    candidate = Candidate(
        Statement("intent", INTENT, "p. 2", 2, None, "intent_list"),
        Statement("outcome", OUTCOME, "p. 8", 8, None, "outcome_prose"),
        0.9,
    )
    settings = Settings(_env_file=None).model_copy(
        update={"agreement_pairing_max_pairs_per_call": 1}
    )
    judged, counts = await judge_pairs(
        client, [candidate, candidate], check_run_id=None, settings=settings, cues=load_cues()
    )
    assert client.calls == 2 and counts["quota"] == 1
    assert judged[0].final == "cannot_determine"
    assert judged[1].unassessed_reason == "quota"
