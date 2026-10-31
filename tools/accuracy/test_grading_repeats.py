"""The paid repeat runner refuses unsafe settings, pays before every attempt,
and stops on the first rate limit. No network and no database."""

import asyncio
from types import SimpleNamespace

import pytest
from tools.accuracy.budget import Budget
from tools.accuracy.grading_repeats import (
    BudgetedTransport,
    HarnessRefused,
    HarnessStop,
    MeasuredCallsOnly,
    check_guards,
)

from app.llm.queue import TransportDailyQuotaExhausted, TransportRateLimited, TransportServerError

MODEL = "gemini-3.5-flash-lite"


class ScriptedInner:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def budget(tmp_path, share=5):
    return Budget(tmp_path / "budget.json", cap=200, shares={"grading": share})


def call(transport, model=MODEL):
    return asyncio.run(transport.generate(model=model, prompt="p", temperature=0.0))


def test_every_attempt_is_paid_for_before_it_is_sent(tmp_path):
    shared = budget(tmp_path)
    transport = BudgetedTransport(ScriptedInner({"ok": 1}, {"ok": 2}), shared, MODEL)
    assert call(transport) == {"ok": 1}
    assert call(transport) == {"ok": 2}
    assert transport.attempts == 2
    assert shared.spent()["by"]["grading"] == 2


def test_a_server_error_is_still_counted_and_left_to_the_queue_to_retry(tmp_path):
    shared = budget(tmp_path)
    transport = BudgetedTransport(ScriptedInner(TransportServerError("500")), shared, MODEL)
    with pytest.raises(TransportServerError):
        call(transport)
    assert shared.spent()["by"]["grading"] == 1
    assert transport.stop_reason is None


@pytest.mark.parametrize(
    "limit", [TransportRateLimited("429"), TransportDailyQuotaExhausted("429")]
)
def test_any_rate_limit_stops_the_run_and_nothing_more_is_sent(tmp_path, limit):
    inner = ScriptedInner(limit, {"ok": 1})
    transport = BudgetedTransport(inner, budget(tmp_path), MODEL)
    with pytest.raises(HarnessStop):
        call(transport)
    with pytest.raises(HarnessStop):
        call(transport)
    assert inner.calls == 1
    assert "429" in transport.stop_reason


def test_the_budget_stops_the_run_before_the_call(tmp_path):
    inner = ScriptedInner({"ok": 1}, {"ok": 2})
    transport = BudgetedTransport(inner, budget(tmp_path, share=1), MODEL)
    call(transport)
    with pytest.raises(HarnessStop):
        call(transport)
    assert inner.calls == 1


def test_a_different_model_is_refused_without_spending(tmp_path):
    shared = budget(tmp_path)
    inner = ScriptedInner({"ok": 1})
    transport = BudgetedTransport(inner, shared, MODEL)
    with pytest.raises(HarnessStop):
        call(transport, model="gemini-3.6-flash")
    assert inner.calls == 0
    assert shared.spent().get("by", {}).get("grading", 0) == 0


def test_only_measured_prompt_types_reach_the_model():
    class Inner:
        async def complete(self, prompt_type, prompt, **kwargs):
            return {"graded": prompt_type}

    client = MeasuredCallsOnly(Inner(), {"semantic_grading"})
    assert asyncio.run(client.complete("semantic_grading", "p")) == {"graded": "semantic_grading"}
    with pytest.raises(HarnessRefused):
        asyncio.run(client.complete("agreement_pairing", "p"))
    assert client.refused == ["agreement_pairing"]


def settings(**overrides):
    values = dict(
        veridical_fake_llm=False,
        llm_cache_bypass=True,
        gemini_api_key="test-key",
        database_url="postgresql://u:p@127.0.0.1:5433/scratch",
        storage_backend="local",
        pipeline_worker_autostart=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_safe_settings_pass_the_guards(monkeypatch):
    monkeypatch.setattr("tools.accuracy.grading_repeats._env_value", lambda name: "prod-key")
    check_guards(settings(), {})


@pytest.mark.parametrize(
    "overrides",
    [
        {"veridical_fake_llm": True},
        {"llm_cache_bypass": False},
        {"gemini_api_key": ""},
        {"gemini_api_key": "prod-key"},
        {"database_url": "postgresql://u:p@ep-cool.neon.tech/db"},
        {"storage_backend": "r2"},
        {"pipeline_worker_autostart": True},
    ],
)
def test_unsafe_settings_refuse_to_spend(monkeypatch, overrides):
    monkeypatch.setattr("tools.accuracy.grading_repeats._env_value", lambda name: "prod-key")
    with pytest.raises(SystemExit):
        check_guards(settings(**overrides), {})
