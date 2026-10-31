"""V-009: LLMQueue — quota persistence, response cache, retry/backoff,
audit logging. Needs a live Postgres (same convention as test_schema.py);
runs against its own scratch database.
"""

import asyncio
import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.security import hash_password
from app.db import sqlalchemy_url
from app.errors import ApiDownError, InvalidApiKeyError, QuotaExhaustedError
from app.llm.pool import ModelSpec
from app.llm.queue import (
    LLMQueue,
    TransportDailyQuotaExhausted,
    TransportInvalidKey,
    TransportModelUnavailable,
    TransportRateLimited,
    TransportServerError,
)
from app.llm.result import GenerationResult
from app.llm.transport import GeminiTransport
from app.models.audit import AuditLog
from app.models.instructor import Instructor
from app.models.llm import LLMQuotaCounter, LLMResponseCache

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live


async def test_actual_sdk_transport_version_survives_queue_cache_and_new_queue(session_factory):
    from google.genai import types

    payload = {"synthetic": "original payload"}
    import json

    reply = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload))])
            )
        ],
        model_version="reported-provider-control",
    )
    calls = []

    async def generated(**kwargs):
        calls.append(kwargs)
        return reply

    transport = object.__new__(GeminiTransport)
    transport._timeout_ms = 1000
    transport._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generated))
    )
    request = dict(prompt_type="t", prompt="controlled SDK chain", prompt_version="v1")
    assert await _make_queue(session_factory, transport).submit(**request) == payload
    assert (
        await _make_queue(session_factory, ScriptedTransport(fail_times=1)).submit(**request)
        == payload
    )
    assert len(calls) == 1
    async with session_factory() as session:
        cached = (await session.scalars(select(LLMResponseCache))).one()
        events = (
            await session.scalars(
                select(AuditLog).where(AuditLog.event_type.in_(("llm_call", "llm_cache_hit")))
            )
        ).all()
    assert cached.model_version == "reported-provider-control" and cached.response == payload
    assert len(events) == 2
    assert all(event.payload["model_version"] == "reported-provider-control" for event in events)
    assert all(
        event.payload["model"] == event.payload["requested_model"] == "test-model"
        for event in events
    )


@pytest.fixture(scope="module")
def llm_scratch_url():
    import asyncio

    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    scratch_db = "veridical_llmtest_" + uuid.uuid4().hex
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{scratch_db}"'))
    url = _swap_db(base, scratch_db)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE "{scratch_db}"'))


@pytest.fixture()
async def session_factory(llm_scratch_url):
    engine = create_async_engine(sqlalchemy_url(llm_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_llm_tables(session_factory):
    """The scratch DB is module-scoped (real migrations are slow to
    re-run); each test starts from an empty quota/cache/audit slate so
    tests can't see each other's call counts."""
    async with session_factory() as session:
        await session.execute(text("TRUNCATE llm_quota_counter, llm_response_cache, audit_log"))
        await session.commit()
    yield


class ScriptedTransport:
    """Deterministic Transport double: scripted failures then a response."""

    def __init__(
        self,
        *,
        responses: list[dict[str, Any]] | None = None,
        fail_times: int = 0,
        fail_with: type[Exception] = TransportServerError,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])
        self._fail_times = fail_times
        self._fail_with = fail_with

    async def generate(
        self, *, model: str, prompt: str, temperature: float, **context: Any
    ) -> dict[str, Any]:
        self.calls.append(
            {"model": model, "prompt": prompt, "temperature": temperature, "context": context}
        )
        if self._fail_times > 0:
            self._fail_times -= 1
            raise self._fail_with("scripted failure")
        return self._responses.pop(0) if self._responses else {"ok": True}


async def _instant_sleep(seconds: float) -> None:
    return None


def _make_queue(session_factory, transport, **overrides: Any) -> LLMQueue:
    kwargs: dict[str, Any] = dict(
        transport=transport,
        session_factory=session_factory,
        model="test-model",
        temperature=0.0,
        rpm=100,
        daily_quota=100,
        max_retries=3,
        retry_base_seconds=0.0,
        reset_timezone="America/Los_Angeles",
        sleep=_instant_sleep,
    )
    kwargs.update(overrides)
    if kwargs.get("pool"):
        # Pool form and single-model form are alternatives, not additions.
        for single_model_only in ("model", "rpm", "daily_quota"):
            kwargs.pop(single_model_only, None)
    return LLMQueue(**kwargs)


async def test_successful_call_writes_audit_log_with_prompt_version_and_hash(session_factory):
    transport = ScriptedTransport(responses=[{"criteria": []}])
    queue = _make_queue(session_factory, transport)

    response = await queue.submit(
        prompt_type="rubric_decomposition", prompt="decompose X", prompt_version="v1"
    )
    assert response == {"criteria": []}

    async with session_factory() as session:
        row = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "llm_call"))
        ).scalar_one()
    assert row.prompt_version == "v1"
    assert len(row.input_hash) == 64
    assert row.payload["response"] == {"criteria": []}


async def test_reported_version_survives_persistence_and_a_new_queue_cache_hit(session_factory):
    payload = {"criteria": [{"text": "synthetic source requirement"}]}
    transport = ScriptedTransport(
        responses=[GenerationResult(payload=payload, model_version="reported-version-control")]
    )
    first = _make_queue(session_factory, transport)
    request = dict(
        prompt_type="rubric_decomposition", prompt="version control", prompt_version="v1"
    )
    assert await first.submit(**request) == payload
    second_transport = ScriptedTransport(fail_times=1)
    second = _make_queue(session_factory, second_transport)
    assert await second.submit(**request) == payload
    assert len(transport.calls) == 1 and second_transport.calls == []
    async with session_factory() as session:
        cached = (await session.scalars(select(LLMResponseCache))).one()
        events = (await session.scalars(select(AuditLog).order_by(AuditLog.id))).all()
    assert cached.response == payload and cached.model_version == "reported-version-control"
    completed = [event for event in events if event.event_type in ("llm_call", "llm_cache_hit")]
    assert [event.event_type for event in completed] == ["llm_call", "llm_cache_hit"]
    for event, source in zip(completed, ("generation", "cache"), strict=True):
        assert event.payload["model"] == event.payload["requested_model"] == "test-model"
        assert event.payload["model_version"] == "reported-version-control"
        assert event.payload["response_source"] == source
        assert event.payload["response"] == payload


async def test_legacy_cache_json_cannot_invent_provider_version(session_factory):
    payload = {"model_version": "untrusted-model-json", "criteria": []}
    transport = ScriptedTransport(responses=[payload])
    queue = _make_queue(session_factory, transport)
    request = dict(
        prompt_type="rubric_decomposition", prompt="unknown version", prompt_version="v1"
    )
    assert await queue.submit(**request) == payload
    assert (
        await _make_queue(session_factory, ScriptedTransport(fail_times=1)).submit(**request)
        == payload
    )
    async with session_factory() as session:
        cached = (await session.scalars(select(LLMResponseCache))).one()
        events = (
            await session.scalars(
                select(AuditLog).where(AuditLog.event_type.in_(("llm_call", "llm_cache_hit")))
            )
        ).all()
    assert cached.model_version is None
    assert all(event.payload["model_version"] is None for event in events)
    assert all(event.payload["model_version_source"] == "unrecorded" for event in events)


async def test_cache_first_writer_preserves_payload_and_version_as_one_record(session_factory):
    queue = _make_queue(session_factory, ScriptedTransport())
    async with session_factory() as session:
        for payload, version in [
            ({"writer": "first"}, "version-first"),
            ({"writer": "second"}, "version-second"),
        ]:
            await queue._write_cache(
                session,
                input_hash="a" * 64,
                prompt_version="v1",
                response=payload,
                model="test-model",
                model_version=version,
            )
            await session.commit()
        cached = await queue._get_cached(session, "a" * 64)
    assert cached.payload == {"writer": "first"}
    assert cached.model_version == "version-first"


async def test_daily_counter_survives_simulated_restart(session_factory):
    transport = ScriptedTransport(responses=[{"a": 1}, {"b": 2}])
    queue_a = _make_queue(session_factory, transport)
    await queue_a.submit(prompt_type="t", prompt="prompt one", prompt_version="v1")
    await queue_a.submit(prompt_type="t", prompt="prompt two", prompt_version="v1")

    # A fresh LLMQueue instance == a fresh process (no shared in-memory state);
    # the counter must come from the DB, not from queue_a's memory.
    queue_b = _make_queue(session_factory, ScriptedTransport())
    status = await queue_b.get_quota_status()
    assert status["calls_used"] == 2


async def test_quota_exhausted_blocks_before_calling_transport(session_factory):
    transport = ScriptedTransport(responses=[{"a": 1}])
    queue = _make_queue(session_factory, transport, daily_quota=1)

    await queue.submit(prompt_type="t", prompt="first", prompt_version="v1")
    with pytest.raises(QuotaExhaustedError):
        await queue.submit(prompt_type="t", prompt="second, different prompt", prompt_version="v1")

    assert len(transport.calls) == 1  # the second submit never reached the transport


async def test_invalid_key_raises_immediately_without_trying_other_models(session_factory):
    """V-052/backend review finding (P1, live-reproduced): a revoked/bad
    key used to propagate as a raw, unmapped SDK exception all the way out
    of `submit()` -- `FallbackLLMClient` had no typed exception to catch,
    so a bad BYOK key was a hard crash, not a graceful fall-back to the
    shared key. Fixed: `transport.py` classifies a 401/403 as
    `TransportInvalidKey`, and `submit()` turns that into
    `InvalidApiKeyError` immediately -- not added to `exhausted` and
    retried against the pool's other models, since an invalid key fails
    identically against every model (unlike quota exhaustion, which is
    genuinely per-model)."""
    transport = ScriptedTransport(fail_times=1, fail_with=TransportInvalidKey)
    queue = _make_queue(session_factory, transport, pool=_pool(("first", 50, 1), ("second", 50, 1)))

    with pytest.raises(InvalidApiKeyError):
        await queue.submit(prompt_type="t", prompt="p", prompt_version="v1")

    assert len(transport.calls) == 1  # never tried "second"


async def test_invalid_key_is_never_retried(session_factory):
    """Unlike a transient TransportServerError/TransportRateLimited, a bad
    key won't start working between attempts a second apart -- retrying
    it would just burn the retry budget for a guaranteed-identical
    failure."""
    transport = ScriptedTransport(fail_times=1, fail_with=TransportInvalidKey)
    queue = _make_queue(session_factory, transport, max_retries=5)

    with pytest.raises(InvalidApiKeyError):
        await queue.submit(prompt_type="t", prompt="p", prompt_version="v1")

    assert len(transport.calls) == 1  # not retried up to max_retries=5


async def test_daily_quota_zero_blocks_the_very_first_call(session_factory):
    """BUG-035: `_try_reserve_quota`'s atomic UPSERT only gates the
    ON CONFLICT UPDATE branch — the plain INSERT (the day's first call
    for a model, i.e. no counter row exists yet) had no quota check at
    all and always succeeded, regardless of `daily_quota`. A
    `daily_quota=0` model (a way to operationally disable a model
    without removing it from the pool) let exactly one real call
    through per day instead of zero."""
    transport = ScriptedTransport(responses=[{"a": 1}])
    queue = _make_queue(session_factory, transport, daily_quota=0)

    with pytest.raises(QuotaExhaustedError):
        await queue.submit(
            prompt_type="t", prompt="should never reach transport", prompt_version="v1"
        )

    assert len(transport.calls) == 0  # the cold-start call never reached the transport


async def test_daily_quota_zero_error_message_never_claims_a_spent_allowance(session_factory):
    """BUG-035 follow-up (backend review): a fully zero-quota pool skips
    every candidate entirely (none is ever tried), so the generic
    "has spent its allowance (models...)" message would render with an
    empty, grammatically-broken model list and falsely imply calls were
    made and quota consumed — honest-wording rule (ground rule 3/9)
    applied to an internal error string, not just product-facing flags."""
    transport = ScriptedTransport(responses=[{"a": 1}])
    queue = _make_queue(session_factory, transport, daily_quota=0)

    with pytest.raises(QuotaExhaustedError) as exc_info:
        await queue.submit(prompt_type="t", prompt="never tried", prompt_version="v1")

    message = str(exc_info.value)
    assert "has spent" not in message
    assert "no model in the pool has any" in message.lower()


async def test_retries_up_to_max_then_raises_api_down_and_logs_failure(session_factory):
    transport = ScriptedTransport(fail_times=99, fail_with=TransportServerError)
    queue = _make_queue(session_factory, transport, max_retries=3)

    with pytest.raises(ApiDownError):
        await queue.submit(prompt_type="t", prompt="doomed", prompt_version="v1")

    assert len(transport.calls) == 3  # exactly max_retries attempts, never silent

    async with session_factory() as session:
        row = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "llm_call_failed"))
        ).scalar_one()
    assert row.prompt_version == "v1"


async def test_rate_limited_response_is_retried_like_a_server_error(session_factory):
    transport = ScriptedTransport(
        fail_times=2, fail_with=TransportRateLimited, responses=[{"ok": True}]
    )
    queue = _make_queue(session_factory, transport, max_retries=3)

    response = await queue.submit(prompt_type="t", prompt="flaky", prompt_version="v1")
    assert response == {"ok": True}
    assert len(transport.calls) == 3
    assert (await queue.get_quota_status())["calls_used"] == 3


async def test_retries_cannot_exceed_actual_attempt_allowance(session_factory):
    transport = ScriptedTransport(fail_times=3)
    queue = _make_queue(session_factory, transport, daily_quota=2, max_retries=3)
    with pytest.raises(QuotaExhaustedError):
        await queue.submit(prompt_type="t", prompt="flaky", prompt_version="v1")
    assert len(transport.calls) == 2
    assert (await queue.get_quota_status())["calls_used"] == 2
    async with session_factory() as session:
        attempts = (
            await session.scalars(
                select(AuditLog).where(AuditLog.event_type == "llm_transport_attempt")
            )
        ).all()
        assert len(attempts) == 2


async def test_identical_concurrent_misses_coalesce_and_release_lock(session_factory):
    import asyncio

    transport = ScriptedTransport()
    queue = _make_queue(session_factory, transport)
    results = await asyncio.gather(
        *[queue.submit(prompt_type="t", prompt="same", prompt_version="v1") for _ in range(3)]
    )
    assert results == [{"ok": True}] * 3
    assert len(transport.calls) == 1
    assert not queue._inflight
    status = await queue.get_quota_status()
    assert status["calls_used"] == 1 and status["cache_hits_today"] == 2


async def test_cancellation_while_waiting_for_rpm_does_not_reserve_daily_capacity(session_factory):
    import asyncio

    entered = asyncio.Event()

    class WaitingGovernor:
        async def acquire(self):
            entered.set()
            await asyncio.Event().wait()

    transport = ScriptedTransport()
    queue = _make_queue(session_factory, transport, governor=WaitingGovernor())
    task = asyncio.create_task(queue.submit(prompt_type="t", prompt="wait", prompt_version="v1"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not transport.calls and not queue._inflight
    assert (await queue.get_quota_status())["calls_used"] == 0


async def test_cache_identity_includes_temperature_type_and_contract(session_factory):
    transport = ScriptedTransport()
    for temperature, prompt_type, contract in [
        (0, "a", "v2"),
        (0.7, "a", "v2"),
        (0, "b", "v2"),
        (0, "a", "v3"),
    ]:
        queue = _make_queue(
            session_factory, transport, temperature=temperature, cache_contract_version=contract
        )
        await queue.submit(prompt_type=prompt_type, prompt="same", prompt_version="v1")
    assert len(transport.calls) == 4


async def test_cache_hit_skips_transport_and_quota_but_is_still_audited(session_factory):
    transport = ScriptedTransport(responses=[{"criteria": ["x"]}])
    queue = _make_queue(session_factory, transport)

    first = await queue.submit(prompt_type="t", prompt="same prompt", prompt_version="v1")
    second = await queue.submit(prompt_type="t", prompt="same prompt", prompt_version="v1")
    assert first == second == {"criteria": ["x"]}
    assert len(transport.calls) == 1  # cache hit never reaches the transport

    status = await queue.get_quota_status()
    assert status["calls_used"] == 1
    assert status["cache_hits_today"] == 1

    async with session_factory() as session:
        hit = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "llm_cache_hit"))
        ).scalar_one()
    assert hit.prompt_version == "v1"


async def test_cache_bypass_forces_a_real_call_every_time(session_factory):
    """BUG-162: `cache_bypass=True` (`Settings.llm_cache_bypass`, wired up
    only for `tools/stability_probe.py`) exists so a run-to-run stability
    measurement is a real, supported operation instead of the DB-edit
    D-011's own caching made it -- an IDENTICAL prompt must reach the
    transport every single time, never replay a stored answer. The write
    side is deliberately UNCHANGED (`test_cache_hit_skips_transport_and_
    quota_but_is_still_audited` above proves the normal, non-bypassed path
    still works exactly as before -- this test only proves the opt-in
    bypass, never the default)."""
    transport = ScriptedTransport(responses=[{"verdict": "pass"}, {"verdict": "fail"}])
    queue = _make_queue(session_factory, transport, cache_bypass=True)

    first = await queue.submit(prompt_type="t", prompt="same prompt", prompt_version="v1")
    second = await queue.submit(prompt_type="t", prompt="same prompt", prompt_version="v1")
    # The real point: two genuinely independent answers, not one cached
    # answer served twice -- if this ever silently regresses back to a
    # cache hit, the ScriptedTransport's second scripted response would
    # never be consumed and both calls would return the FIRST response.
    assert first == {"verdict": "pass"}
    assert second == {"verdict": "fail"}
    assert len(transport.calls) == 2  # neither call was served from cache

    status = await queue.get_quota_status()
    assert status["calls_used"] == 2
    assert status["cache_hits_today"] == 0

    # Writes are unaffected -- a THIRD queue, cache_bypass=False (the
    # default), sees a real cached row from the bypassed run above and
    # hits it normally. Proves bypass only ever skips READS.
    normal_queue = _make_queue(session_factory, ScriptedTransport(), cache_bypass=False)
    replayed = await normal_queue.submit(prompt_type="t", prompt="same prompt", prompt_version="v1")
    # `_write_cache` is `on_conflict_do_nothing` on `input_hash` -- the
    # FIRST bypassed write wins the cache row, later ones for the same
    # hash are silent no-ops, same as any other identical-key write race.
    assert replayed == first
    normal_status = await normal_queue.get_quota_status()
    assert normal_status["cache_hits_today"] == 1


def _pool(*specs: tuple[str, int, int]) -> tuple[ModelSpec, ...]:
    return tuple(
        ModelSpec(model=model, rpm=100, daily_quota=quota, vision=vision == 1)
        for model, quota, vision in specs
    )


class PerModelTransport:
    """Transport double that can declare specific models "done for the day",
    the way the real API does with a per-DAY 429."""

    def __init__(self, *, daily_exhausted: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self._exhausted = daily_exhausted or set()

    async def generate(
        self, *, model: str, prompt: str, temperature: float, **context: Any
    ) -> dict[str, Any]:
        self.calls.append(model)
        if model in self._exhausted:
            raise TransportDailyQuotaExhausted(
                "429 RESOURCE_EXHAUSTED quotaId: "
                "GenerateRequestsPerDayPerProjectPerModel-FreeTier quotaValue: '20'"
            )
        return {"served_by": model}


class AvailabilityTransport(PerModelTransport):
    def __init__(self, *, unavailable=(), daily_exhausted=()):
        super().__init__(daily_exhausted=set(daily_exhausted))
        self.unavailable = set(unavailable)

    async def generate(self, *, model, prompt, temperature, **context):
        if model in self.unavailable:
            self.calls.append(model)
            raise TransportModelUnavailable("raw provider details must not enter the audit")
        return await super().generate(
            model=model, prompt=prompt, temperature=temperature, **context
        )


async def test_unavailable_model_uses_reserve_and_preserves_real_accounting_and_cache(
    session_factory,
):
    from app.models.llm import LLMQuotaCounter, LLMResponseCache

    transport = AvailabilityTransport(unavailable={"head"})
    queue = _make_queue(session_factory, transport, pool=_pool(("head", 50, 1), ("reserve", 50, 1)))
    request = dict(prompt_type="t", prompt="same input", prompt_version="v1")
    assert await queue.submit(**request) == {"served_by": "reserve"}
    assert await queue.submit(**request) == {"served_by": "reserve"}
    assert transport.calls == ["head", "reserve"]
    async with session_factory() as session:
        counters = (await session.execute(select(LLMQuotaCounter))).scalars().all()
        assert {row.model: row.call_count for row in counters} == {"head": 1, "reserve": 1}
        cache = (await session.execute(select(LLMResponseCache))).scalar_one()
        assert cache.model == "reserve"
        audits = (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
        assert [row.event_type for row in audits] == [
            "llm_transport_attempt",
            "llm_call_failed",
            "llm_transport_attempt",
            "llm_call",
            "llm_cache_hit",
        ]
        failed = audits[1]
        assert failed.payload["response"] == {
            "error_code": "model_unavailable",
            "error": "The requested model cannot serve this operation.",
        }
        assert failed.payload["model"] == "head"
        assert audits[3].payload["model"] == "reserve"
        assert cache.input_hash == audits[3].input_hash == audits[4].input_hash
        assert failed.input_hash != cache.input_hash
    status = await queue.get_quota_status()
    assert all(not model["exhausted"] for model in status["models"])


@pytest.mark.parametrize("daily_exhausted", [(), ("head",), ("reserve",)])
async def test_unavailable_and_mixed_pool_failures_do_not_claim_all_quota_spent(
    session_factory, daily_exhausted
):
    unavailable = {"head", "reserve"} - set(daily_exhausted)
    transport = AvailabilityTransport(unavailable=unavailable, daily_exhausted=daily_exhausted)
    queue = _make_queue(session_factory, transport, pool=_pool(("head", 50, 1), ("reserve", 50, 1)))
    with pytest.raises(ApiDownError, match="model was unavailable"):
        await queue.submit(prompt_type="t", prompt="p", prompt_version="v1")
    assert transport.calls == ["head", "reserve"]
    status = await queue.get_quota_status()
    for model in status["models"]:
        assert model["calls_used"] == (50 if model["model"] in daily_exhausted else 1)
        assert model["exhausted"] is (model["model"] in daily_exhausted)
    async with session_factory() as session:
        exhausted = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.event_type == "llm_model_exhausted")
                )
            )
            .scalars()
            .all()
        )
        assert {row.payload["model"] for row in exhausted} == set(daily_exhausted)


async def test_vision_unavailability_uses_only_enabled_vision_reserve(session_factory):
    transport = AvailabilityTransport(unavailable={"head"})
    queue = _make_queue(
        session_factory,
        transport,
        pool=_pool(("head", 50, 1), ("text", 50, 0), ("disabled", 0, 1), ("reserve", 50, 1)),
    )
    result = await queue.submit(
        prompt_type="vision", prompt="p", prompt_version="v1", images=[b"png"]
    )
    assert result == {"served_by": "reserve"}
    assert transport.calls == ["head", "reserve"]


async def test_pool_fails_over_to_the_next_model_when_an_island_is_spent(session_factory):
    """The blocker this pool exists for: the head model's day is 20 calls
    long, but the key still has other islands to spend."""
    transport = PerModelTransport()
    queue = _make_queue(
        session_factory,
        transport,
        pool=_pool(("small", 1, 1), ("big", 50, 1)),
    )

    first = await queue.submit(prompt_type="t", prompt="one", prompt_version="v1")
    second = await queue.submit(prompt_type="t", prompt="two", prompt_version="v1")

    assert first == {"served_by": "small"}
    assert second == {"served_by": "big"}, "the second call must not die on the spent island"
    assert transport.calls == ["small", "big"]


async def test_cache_bypass_skips_every_candidate_in_a_multi_model_pool(session_factory):
    """backend review finding (BUG-162 review, P3): the single-model bypass
    test above doesn't demonstrate the skip across a pool with more than
    one candidate -- the cache-read loop is `for spec in candidates: if
    self._cache_bypass: continue`, so an identical prompt submitted twice
    against a 2-model pool must reach the transport BOTH times, on the
    SAME head model both times (neither model is exhausted here, so
    normal fail-over never triggers -- this isolates the bypass claim from
    the fail-over mechanism `test_pool_fails_over_to_the_next_model_when_
    an_island_is_spent` above already covers separately)."""
    transport = PerModelTransport()
    queue = _make_queue(
        session_factory,
        transport,
        pool=_pool(("head", 50, 1), ("reserve", 50, 1)),
        cache_bypass=True,
    )

    first = await queue.submit(prompt_type="t", prompt="same prompt", prompt_version="v1")
    second = await queue.submit(prompt_type="t", prompt="same prompt", prompt_version="v1")

    assert first == {"served_by": "head"}
    assert second == {"served_by": "head"}
    assert transport.calls == ["head", "head"]  # never short-circuited to a cache hit

    status = await queue.get_quota_status()
    assert status["calls_used"] == 2
    assert status["cache_hits_today"] == 0


async def test_a_per_day_429_closes_that_island_instead_of_being_retried(session_factory):
    """A per-day 429 is not a transient failure: retrying it burns the retry
    budget and then reports `api_down`, which is a lie about what happened
    (charter rule 9). It must fail over, and stop re-asking that model."""
    transport = PerModelTransport(daily_exhausted={"stale"})
    queue = _make_queue(
        session_factory,
        transport,
        pool=_pool(("stale", 50, 1), ("healthy", 50, 1)),
        max_retries=3,
    )

    response = await queue.submit(prompt_type="t", prompt="one", prompt_version="v1")
    assert response == {"served_by": "healthy"}
    assert transport.calls == ["stale", "healthy"], "a daily cap must never be retried"

    # The queue believed "stale" had 50 calls left; the API said otherwise.
    # Reality wins, and the island stays closed for the rest of the day.
    await queue.submit(prompt_type="t", prompt="two", prompt_version="v1")
    assert transport.calls == ["stale", "healthy", "healthy"]

    status = await queue.get_quota_status()
    stale = next(entry for entry in status["models"] if entry["model"] == "stale")
    assert stale["exhausted"] is True
    assert stale["calls_remaining"] == 0


async def test_daily_quota_zero_model_is_skipped_in_a_mixed_pool(session_factory):
    """BUG-035 follow-up (backend review): the single-model all-zero test
    doesn't prove a `daily_quota=0` island is skipped rather than merely
    ordered last — a realistic pool has it alongside normal-quota
    models. Zero cold-start leakage on the disabled model specifically,
    every call goes to the real one, and the disabled model still
    reports honestly in status (never silently dropped from it)."""
    transport = PerModelTransport()
    queue = _make_queue(
        session_factory,
        transport,
        pool=_pool(("disabled", 0, 1), ("normal", 5, 1)),
    )

    response = await queue.submit(prompt_type="t", prompt="one", prompt_version="v1")
    assert response == {"served_by": "normal"}
    assert transport.calls == ["normal"], "the disabled island must never be tried at all"

    status = await queue.get_quota_status()
    disabled = next(entry for entry in status["models"] if entry["model"] == "disabled")
    assert disabled["daily_limit"] == 0
    assert disabled["calls_remaining"] == 0
    assert disabled["exhausted"] is True


async def test_quota_exhausted_only_when_every_island_is_spent(session_factory):
    transport = PerModelTransport()
    queue = _make_queue(
        session_factory,
        transport,
        pool=_pool(("a", 1, 1), ("b", 1, 1)),
    )

    await queue.submit(prompt_type="t", prompt="one", prompt_version="v1")
    await queue.submit(prompt_type="t", prompt="two", prompt_version="v1")
    with pytest.raises(QuotaExhaustedError, match="Every model in the pool"):
        await queue.submit(prompt_type="t", prompt="three", prompt_version="v1")

    assert transport.calls == ["a", "b"]  # the third submit never reached the transport


async def test_quota_status_reports_the_pool_total_not_one_model(session_factory):
    """Every capacity claim (D-001/D-014, the paper) is priced against this
    number, so it must be the sum of the islands."""
    queue = _make_queue(
        session_factory,
        PerModelTransport(),
        pool=_pool(("a", 20, 1), ("b", 180, 1)),
    )

    await queue.submit(prompt_type="t", prompt="one", prompt_version="v1")
    status = await queue.get_quota_status()

    assert status["daily_limit"] == 200
    assert status["calls_used"] == 1
    assert status["calls_remaining"] == 199
    assert [entry["model"] for entry in status["models"]] == ["a", "b"]


async def test_image_calls_never_fail_over_onto_a_text_only_model(session_factory):
    """The V-007 vision pass on a text-only model would silently drop the
    images and answer confidently about nothing."""
    transport = PerModelTransport()
    queue = _make_queue(
        session_factory,
        transport,
        pool=_pool(("text-only", 50, 0), ("multimodal", 50, 1)),
    )

    response = await queue.submit(
        prompt_type="image_table_extraction",
        prompt="read this table",
        prompt_version="v1",
        images=[b"fake-png-bytes"],
    )
    assert response == {"served_by": "multimodal"}
    assert transport.calls == ["multimodal"]

    # Raw PNG bytes must not reach the JSONB payload (they are not JSON
    # serializable — this crashed the audit write before V-049 and had never
    # fired only because the vision pass had only ever run in fake mode).
    async with session_factory() as session:
        row = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "llm_call"))
        ).scalar_one()
    (image,) = row.payload["context"]["images"]
    assert image["__bytes__"] == len(b"fake-png-bytes")
    assert len(image["sha256"]) == 64


async def test_audit_row_records_requested_model_selected_after_failover(session_factory):
    """Audit retains the requested routing ID selected after failover;
    provider-reported version is a separate optional field."""
    transport = PerModelTransport(daily_exhausted={"head"})
    queue = _make_queue(
        session_factory,
        transport,
        pool=_pool(("head", 50, 1), ("reserve", 50, 1)),
    )

    await queue.submit(prompt_type="t", prompt="one", prompt_version="v1")

    async with session_factory() as session:
        row = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "llm_call"))
        ).scalar_one()
    assert row.payload["model"] == "reserve"


async def test_audit_row_marks_fake_llm_false_for_a_real_call(session_factory):
    """BUG-219: parity with FakeLLMClient._write_audit's own explicit
    `"fake_llm": True` — the real queue must be equally explicit, so the
    audit summary's execution-mode derivation never has to fall back to
    guessing "no fake_llm key present" means real."""
    transport = ScriptedTransport(responses=[{"ok": True}])
    queue = _make_queue(session_factory, transport)

    await queue.submit(prompt_type="t", prompt="one", prompt_version="v1")

    async with session_factory() as session:
        row = (
            await session.execute(select(AuditLog).where(AuditLog.event_type == "llm_call"))
        ).scalar_one()
    assert row.payload["fake_llm"] is False


async def test_check_run_id_is_excluded_from_the_cache_key(session_factory):
    """D-011: a Flow E re-run under a new check_run must still hit cache for
    the same rubric/prompt content — check_run_id is a run identifier, not
    prompt content, and must never fragment the cache."""
    transport = ScriptedTransport(responses=[{"criteria": []}])
    queue = _make_queue(session_factory, transport)

    await queue.submit(prompt_type="t", prompt="same content", prompt_version="v1", check_run_id=1)
    await queue.submit(prompt_type="t", prompt="same content", prompt_version="v1", check_run_id=2)

    assert len(transport.calls) == 1


@pytest.fixture()
async def instructor_ids(session_factory) -> tuple[int, int]:
    """A real FK now backs llm_quota_counter.instructor_id (migration
    0022) -- these tests need real Instructor rows, not arbitrary ints.
    Unique emails per call (not truncated between tests, unlike the quota
    tables) so repeated fixture use across tests never collides."""
    import uuid

    suffix = uuid.uuid4().hex[:8]
    async with session_factory() as session:
        a = Instructor(
            email=f"byok-a-{suffix}@tip.edu.ph", display_name="A", password_hash=hash_password("x")
        )
        b = Instructor(
            email=f"byok-b-{suffix}@tip.edu.ph", display_name="B", password_hash=hash_password("x")
        )
        session.add_all([a, b])
        await session.commit()
        return a.id, b.id


async def test_instructor_quota_is_a_separate_island_from_the_shared_pool(
    session_factory, instructor_ids
):
    """V-052 (BYOK): an instructor's own key spends a genuinely separate
    quota counter from the shared pool key, even for the identical model
    name -- the two partial unique indexes (migration 0022) exist
    specifically so these never collide into one row."""
    _, instructor_id = instructor_ids
    shared = _make_queue(session_factory, ScriptedTransport(responses=[{"a": 1}]))
    own = _make_queue(
        session_factory, ScriptedTransport(responses=[{"b": 2}]), instructor_id=instructor_id
    )

    await shared.submit(prompt_type="t", prompt="shared call", prompt_version="v1")
    await own.submit(prompt_type="t", prompt="own call", prompt_version="v1")

    shared_status = await shared.get_quota_status()
    own_status = await own.get_quota_status()
    assert shared_status["calls_used"] == 1
    assert own_status["calls_used"] == 1


async def test_two_different_instructors_have_independent_quota_islands(
    session_factory, instructor_ids
):
    """Not just instructor-vs-shared -- instructor A's own key spending
    quota must never appear on instructor B's own counter either."""
    id_a, id_b = instructor_ids
    queue_a = _make_queue(
        session_factory, ScriptedTransport(responses=[{"a": 1}, {"a": 2}]), instructor_id=id_a
    )
    queue_b = _make_queue(
        session_factory, ScriptedTransport(responses=[{"b": 1}]), instructor_id=id_b
    )

    await queue_a.submit(prompt_type="t", prompt="a1", prompt_version="v1")
    await queue_a.submit(prompt_type="t", prompt="a2", prompt_version="v1")
    await queue_b.submit(prompt_type="t", prompt="b1", prompt_version="v1")

    assert (await queue_a.get_quota_status())["calls_used"] == 2
    assert (await queue_b.get_quota_status())["calls_used"] == 1


async def test_instructor_own_key_exhaustion_leaves_the_shared_islands_untouched(
    session_factory, instructor_ids
):
    """An instructor's own key hitting its (tiny, test-scoped) daily cap for
    a model name must never spend the shared pool's counter for that SAME
    model name -- each queue's ON CONFLICT target (the conflict-target
    helper) must resolve to its own partial index, not accidentally share
    one across `instructor_id` values."""
    _, instructor_id = instructor_ids
    own = _make_queue(
        session_factory,
        ScriptedTransport(responses=[{"a": 1}]),
        instructor_id=instructor_id,
        pool=_pool(("shared-model-name", 1, 0)),
    )
    await own.submit(prompt_type="t", prompt="p1", prompt_version="v1")
    with pytest.raises(QuotaExhaustedError):
        # Own queue's single unit of daily_quota is already spent.
        await own.submit(prompt_type="t", prompt="p2", prompt_version="v2")

    shared = _make_queue(
        session_factory,
        ScriptedTransport(responses=[{"ok": True}]),
        pool=_pool(("shared-model-name", 1, 0)),
    )
    response = await shared.submit(prompt_type="t", prompt="p3", prompt_version="v1")
    assert response == {"ok": True}


@pytest.mark.parametrize("owner_index", [None, 0, 1])
async def test_alias_final_slot_is_atomic_across_independent_queues(
    session_factory, instructor_ids, owner_index
):
    owner = None if owner_index is None else instructor_ids[owner_index]
    spec = ModelSpec("current", 100, 20, False, budget_aliases=("old",))
    transports = [ScriptedTransport(responses=[{"winner": index}]) for index in range(2)]
    queues = [
        _make_queue(session_factory, t, pool=(spec,), instructor_id=owner) for t in transports
    ]
    async with session_factory() as session:
        session.add(
            LLMQuotaCounter(
                quota_day=queues[0]._quota_day(),
                model="old",
                instructor_id=owner,
                call_count=19,
                cache_hit_count=3,
            )
        )
        await session.commit()
    results = await asyncio.gather(
        *[
            queue.submit(prompt_type="t", prompt=f"distinct request {index}", prompt_version="v1")
            for index, queue in enumerate(queues)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, QuotaExhaustedError) for result in results) == 1
    assert sum(len(t.calls) for t in transports) == 1
    async with session_factory() as session:
        rows = {row.model: row for row in (await session.scalars(select(LLMQuotaCounter))).all()}
    assert rows["old"].call_count == 19 and rows["old"].cache_hit_count == 3
    # Local exhaustion must not pin a fresh canonical counter to 20.
    assert rows["current"].call_count == 1
    status = await queues[0].get_quota_status()
    assert status["calls_used"] == 20 and status["calls_remaining"] == 0
    assert status["recorded_budget_debits"] == 20


async def test_alias_reservation_serializes_on_canonical_database_lock(session_factory):
    spec = ModelSpec("current", 100, 20, False, budget_aliases=("old",))
    first = _make_queue(session_factory, ScriptedTransport(), pool=(spec,))
    second = _make_queue(session_factory, ScriptedTransport(), pool=(spec,))
    async with session_factory() as holder:
        await first._lock_budget(holder, spec, first._quota_day())

        async def reserve():
            async with session_factory() as contender:
                result = await second._try_reserve_quota(contender, spec)
                await contender.commit()
                return result

        task = asyncio.create_task(reserve())
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
            await holder.commit()
            assert await asyncio.wait_for(task, timeout=5) is True
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_alias_history_is_visible_and_unassigned_records_do_not_spend_current_budget(
    session_factory,
):
    spec = ModelSpec("current", 100, 20, False, budget_aliases=("old",))
    queue = _make_queue(session_factory, ScriptedTransport(), pool=(spec,))
    async with session_factory() as session:
        session.add_all(
            [
                LLMQuotaCounter(
                    quota_day=queue._quota_day(), model=name, call_count=calls, cache_hit_count=hits
                )
                for name, calls, hits in [
                    ("current", 3, 2),
                    ("old", 20, 3),
                    ("retired", 8, 1),
                    ("unknown", 2, 0),
                ]
            ]
        )
        await session.commit()
    status = await queue.get_quota_status()
    assert status["calls_used"] == 20 and status["calls_remaining"] == 0
    assert status["models"][0]["recorded_budget_debits"] == 23
    assert status["models"][0]["historical_budget_debits"] == 20
    assert status["recorded_budget_debits"] == 33
    assert status["cache_hits_today"] == 6 and status["cache_hit_rate"] is None
    assert status["legacy_models"] == [
        {
            "model": "old",
            "recorded_budget_debits": 20,
            "cache_hits_today": 3,
            "budget_model": "current",
        },
        {
            "model": "retired",
            "recorded_budget_debits": 8,
            "cache_hits_today": 1,
            "budget_model": None,
        },
        {
            "model": "unknown",
            "recorded_budget_debits": 2,
            "cache_hits_today": 0,
            "budget_model": None,
        },
    ]


async def test_provider_closure_never_lowers_prior_debits(session_factory):
    spec = ModelSpec("current", 100, 20, False, budget_aliases=("old",))
    queue = _make_queue(session_factory, ScriptedTransport(), pool=(spec,))
    async with session_factory() as session:
        session.add_all(
            [
                LLMQuotaCounter(
                    quota_day=queue._quota_day(), model=name, call_count=calls, cache_hit_count=4
                )
                for name, calls in [("current", 30), ("old", 7)]
            ]
        )
        await session.commit()
    async with session_factory() as session:
        await queue._close_island(session, spec)
        await session.commit()
    async with session_factory() as session:
        rows = {
            row.model: (row.call_count, row.cache_hit_count)
            for row in (await session.scalars(select(LLMQuotaCounter))).all()
        }
    assert rows == {"current": (30, 4), "old": (7, 4)}


async def test_alias_records_are_scoped_by_owner_and_budget_day(session_factory, instructor_ids):
    own_a, own_b = instructor_ids
    spec = ModelSpec("current", 100, 1, False, budget_aliases=("old",))
    queue = _make_queue(
        session_factory,
        ScriptedTransport(responses=[{"ok": True}]),
        pool=(spec,),
        instructor_id=own_a,
    )
    async with session_factory() as session:
        session.add_all(
            [
                LLMQuotaCounter(
                    quota_day=day,
                    model="old",
                    instructor_id=owner,
                    call_count=20,
                    cache_hit_count=3,
                )
                for day, owner in [
                    (queue._quota_day(), None),
                    (queue._quota_day(), own_b),
                    ("2000-01-01", own_a),
                ]
            ]
        )
        await session.commit()
    assert await queue.submit(prompt_type="t", prompt="own current day", prompt_version="v1") == {
        "ok": True
    }
    status = await queue.get_quota_status()
    assert status["recorded_budget_debits"] == 1 and status["cache_hits_today"] == 0
    assert status["legacy_models"] == []


async def test_nul_in_a_prompt_from_an_older_extraction_is_dropped_before_the_call(
    session_factory,
):
    """BUG-550: extractions stored before NUL was dropped still carry it. The
    model call must not be paid for and then lost to a failed audit write."""
    from app.ingest.normalize import NUL

    transport = ScriptedTransport(responses=[{"ok": True}])
    queue = _make_queue(session_factory, transport)
    request = dict(
        prompt_type="semantic_grading",
        prompt=f"Chapter{NUL}One",
        prompt_version="v1",
        excerpts=[f"a{NUL}b", ("c", f"d{NUL}")],
    )
    assert await queue.submit(**request) == {"ok": True}
    assert await queue.submit(**request) == {"ok": True}

    assert len(transport.calls) == 1  # the second request was a cache hit
    assert transport.calls[0]["prompt"] == "ChapterOne"
    assert transport.calls[0]["context"]["excerpts"] == ["ab", ("c", "d")]
    async with session_factory() as session:
        events = (await session.scalars(select(AuditLog).order_by(AuditLog.id))).all()
    answered = {"llm_call", "llm_cache_hit"}
    assert [e.event_type for e in events if e.event_type in answered] == [
        "llm_call",
        "llm_cache_hit",
    ]
    assert all(NUL not in str(event.payload) for event in events)


def test_without_nul_returns_clean_values_unchanged():
    from app.ingest.normalize import NUL

    clean = {"prompt": "text", "items": ["a", ("b", 1)], "image": b"\x00raw"}
    assert LLMQueue.without_nul(clean) is clean
    assert LLMQueue.without_nul({"image": b"\x00raw"})["image"] == b"\x00raw"
    assert LLMQueue.without_nul(NUL * 2) == ""
