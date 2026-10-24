"""F4/F5 retain completed work and count capability limits by actual cause."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.checks.agreement import pair as agreement
from app.checks.agreement.extract import Statement
from app.checks.citations.support import ClaimSupportInput, run_claim_support_check
from app.config import Settings
from app.errors import RuntimeApiKeyRejectedError
from app.llm.resolution import AIUnavailableError, RunLLMClient


@pytest.mark.parametrize("cause", ["ai_not_configured", "ai_key_rejected"])
async def test_agreement_keeps_prefix_and_counts_remaining_candidates(monkeypatch, cause):
    intent = Statement("intent", "Build a login module.", "p. 1", 1, None, "intent_list")
    outcome = Statement("outcome", "A login module was built.", "p. 2", 2, None, "outcome_list")
    candidates = [agreement.Candidate(intent, outcome, 1.0)] * 3
    monkeypatch.setattr(agreement, "generate_candidates", lambda *a, **k: (candidates, [], {0}))
    client = AsyncMock()
    client.complete.side_effect = [
        {"verdicts": [{"index": 0, "verdict": "consistent", "reasoning": "Matches."}]},
        AIUnavailableError(cause),
    ]
    result = await agreement.run_agreement_pairing(
        client,
        [intent],
        [outcome],
        check_run_id=1,
        settings=Settings(_env_file=None, agreement_pairing_max_pairs_per_call=1),
    )
    assert client.complete.await_count == 2
    assert result.flags == []
    assert result.n_skipped_total == 2
    assert getattr(result, f"n_skipped_{cause}") == 2
    other = "ai_key_rejected" if cause == "ai_not_configured" else "ai_not_configured"
    assert getattr(result, f"n_skipped_{other}") == 0
    assert (
        result.n_skipped_quota == result.n_skipped_api_down == result.n_skipped_parse_failure == 0
    )


@pytest.mark.parametrize("cause", ["ai_not_configured", "ai_key_rejected"])
async def test_support_keeps_prefix_and_limit_units_distinct(cause):
    pairs = [
        ClaimSupportInput(SimpleNamespace(order_index=i), "A finding was reported.", "A finding.")
        for i in range(4)
    ]
    client = AsyncMock()
    client.complete.side_effect = [
        {"verdicts": [{"index": 0, "verdict": "supported", "reasoning": "Matches."}]},
        AIUnavailableError(cause),
    ]
    result = await run_claim_support_check(
        client,
        pairs,
        check_run_id=1,
        settings=Settings(
            _env_file=None, claim_support_max_pairs_per_call=1, claim_support_max_pairs_per_run=3
        ),
    )
    assert client.complete.await_count == 2
    assert result.n_skipped_total == 3
    assert result.n_skipped_limit == 1
    assert getattr(result, f"n_skipped_{cause}") == 2
    assert (
        result.n_skipped_quota == result.n_skipped_api_down == result.n_skipped_parse_failure == 0
    )
    assert [(p.pair_index, p.status, p.unassessed_reason) for p in result.pair_results] == [
        (0, "assessed", None),
        (1, "unassessed", cause),
        (2, "unassessed", cause),
        (3, "unassessed", "limit"),
    ]
    assert all(p.batch_index is None for p in result.pair_results[1:])


async def test_zero_ai_candidates_never_resolve_capability(monkeypatch):
    resolver = AsyncMock(side_effect=AssertionError("No AI work exists"))
    client = RunLLMClient(resolver)
    monkeypatch.setattr(agreement, "generate_candidates", lambda *a, **k: ([], [], set()))
    settings = Settings(_env_file=None)
    result = await agreement.run_agreement_pairing(
        client, [], [], check_run_id=1, settings=settings
    )
    support = await run_claim_support_check(client, [], check_run_id=1, settings=settings)
    assert result.n_skipped_total == support.n_skipped_total == 0
    resolver.assert_not_awaited()


async def test_fresh_support_rejection_records_only_current_chunk_request():
    underlying = AsyncMock()
    underlying.complete.side_effect = RuntimeApiKeyRejectedError("controlled rejection")
    client = RunLLMClient(AsyncMock(return_value=underlying))
    pairs = [
        ClaimSupportInput(SimpleNamespace(order_index=i), "A finding.", "A finding.")
        for i in range(4)
    ]
    result = await run_claim_support_check(
        client,
        pairs,
        check_run_id=1,
        settings=Settings(
            _env_file=None, claim_support_max_pairs_per_call=2, claim_support_max_pairs_per_run=3
        ),
    )
    assert underlying.complete.await_count == 1
    assert result.n_skipped_ai_key_rejected == 3
    assert result.n_skipped_limit == 1 and result.n_skipped_total == 4
    assert [(p.batch_index, p.batch_local_index) for p in result.pair_results] == [
        (0, 0),
        (0, 1),
        (None, None),
        (None, None),
    ]
    remembered = await run_claim_support_check(
        client, pairs, check_run_id=1, settings=Settings(_env_file=None)
    )
    assert underlying.complete.await_count == 1
    assert all(p.batch_index is None for p in remembered.pair_results)
