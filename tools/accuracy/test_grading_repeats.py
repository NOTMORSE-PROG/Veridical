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
    _env_value,
    activation_refusal,
    check_guards,
    degraded_reason,
)

from app.llm.queue import (
    TransportDailyQuotaExhausted,
    TransportModelUnavailable,
    TransportRateLimited,
    TransportServerError,
)

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


PARITY = {
    "production_key_variable": "GEMINI_API_KEY_PROD",
    "production_settings": {
        "_source": "test",
        "grading_decision_policy": "decide",
        "gemini_request_timeout_seconds": 120,
    },
}


def settings(**overrides):
    values = dict(
        veridical_fake_llm=False,
        llm_cache_bypass=True,
        gemini_api_key="test-key",
        database_url="postgresql://u:p@127.0.0.1:5433/scratch",
        storage_backend="local",
        pipeline_worker_autostart=False,
        grading_decision_policy="decide",
        gemini_request_timeout_seconds=120,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("overrides", "config", "message"),
    [
        ({"grading_decision_policy": "escalate"}, PARITY, "GRADING_DECISION_POLICY"),
        ({"gemini_request_timeout_seconds": 60}, PARITY, "GEMINI_REQUEST_TIMEOUT_SECONDS"),
        ({}, {"production_key_variable": "P"}, "production_settings"),
        ({}, {**PARITY, "production_settings": {"no_such_setting": 1}}, "unknown setting"),
    ],
)
def test_a_setting_that_differs_from_production_refuses_to_spend(
    monkeypatch, overrides, config, message
):
    monkeypatch.setattr(
        "tools.accuracy.grading_repeats._env_value", lambda name, env_file: "prod-key"
    )
    with pytest.raises(SystemExit, match=message):
        check_guards(settings(**overrides), config)


def test_the_production_key_check_fails_closed_without_its_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY_PROD", raising=False)
    with pytest.raises(SystemExit, match="missing"):
        _env_value("GEMINI_API_KEY_PROD", str(tmp_path / "absent.env"))
    monkeypatch.setenv("GEMINI_API_KEY_PROD", "from-env")
    assert _env_value("GEMINI_API_KEY_PROD", None) == "from-env"


def test_an_unexpected_provider_error_stops_the_run(tmp_path):
    inner = ScriptedInner(TransportModelUnavailable("404"), {"ok": 1})
    transport = BudgetedTransport(inner, budget(tmp_path), MODEL)
    with pytest.raises(HarnessStop):
        call(transport)
    with pytest.raises(HarnessStop):
        call(transport)
    assert inner.calls == 1


def test_an_unlockable_ledger_stops_the_run_without_sending(tmp_path, monkeypatch):
    shared = budget(tmp_path)
    monkeypatch.setattr(shared, "reserve", lambda lane: (_ for _ in ()).throw(TimeoutError("held")))
    inner = ScriptedInner({"ok": 1})
    transport = BudgetedTransport(inner, shared, MODEL)
    with pytest.raises(HarnessStop):
        call(transport)
    assert inner.calls == 0


def record(stage, *outcomes):
    return {
        "check_run": {"stage_status": {"stages": {"semantic": stage}}},
        "results": [{"kind": "semantic", "outcome": o} for o in outcomes],
    }


@pytest.mark.parametrize(
    "candidate",
    [
        record({"status": "done", "degraded_count": 2, "degraded_code": "api_down"}, "passed"),
        record({"status": "done"}, "passed", "api_down"),
        record({"status": "done"}, "quota_exhausted"),
        record({"status": "running"}, "passed"),
        record({"status": "done"}),
    ],
)
def test_a_degraded_run_is_never_scored(candidate):
    assert degraded_reason(candidate)


def test_a_fully_graded_run_is_scored():
    assert degraded_reason(record({"status": "done"}, "passed", "escalated", "failed")) is None


def test_safe_settings_pass_the_guards(monkeypatch):
    monkeypatch.setattr(
        "tools.accuracy.grading_repeats._env_value", lambda name, env_file: "prod-key"
    )
    check_guards(settings(), PARITY)


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
    monkeypatch.setattr(
        "tools.accuracy.grading_repeats._env_value", lambda name, env_file: "prod-key"
    )
    with pytest.raises(SystemExit):
        check_guards(settings(**overrides), PARITY)


def rubric(*statuses, active=False):
    details = [{"id": i, "status": s} for i, s in enumerate(statuses, start=1)]
    return SimpleNamespace(is_active=active, parse_issue_details=details or None)


def test_an_open_review_item_blocks_activation_and_names_the_item():
    refusal = activation_refusal(rubric("acknowledged", "open"))
    assert refusal is not None
    assert "1 open review item" in refusal
    assert "[2]" in refusal


@pytest.mark.parametrize(
    "candidate",
    [rubric(), rubric("acknowledged", "resolved"), rubric("open", active=True)],
)
def test_a_clean_or_already_active_format_is_not_refused(candidate):
    assert activation_refusal(candidate) is None
