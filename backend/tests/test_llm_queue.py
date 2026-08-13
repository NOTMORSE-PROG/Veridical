"""V-009: LLMQueue — quota persistence, response cache, retry/backoff,
audit logging. Needs a live Postgres (same convention as test_schema.py);
runs against its own scratch database.
"""

import os
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import sqlalchemy_url
from app.errors import ApiDownError, QuotaExhaustedError
from app.llm.pool import ModelSpec
from app.llm.queue import (
    LLMQueue,
    TransportDailyQuotaExhausted,
    TransportRateLimited,
    TransportServerError,
)
from app.models.audit import AuditLog

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_llmtest"


@pytest.fixture(scope="module")
def llm_scratch_url():
    import asyncio

    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture()
def session_factory(llm_scratch_url):
    engine = create_async_engine(sqlalchemy_url(llm_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


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
    """BUG-035 follow-up (backend-critic): a fully zero-quota pool skips
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
    """BUG-035 follow-up (backend-critic): the single-model all-zero test
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


async def test_audit_row_records_the_model_that_actually_served_the_call(session_factory):
    """A verdict's provenance is the model that produced it — the pool head
    is not evidence (V-024 replay must reconstruct the real call)."""
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


async def test_check_run_id_is_excluded_from_the_cache_key(session_factory):
    """D-011: a Flow E re-run under a new check_run must still hit cache for
    the same rubric/prompt content — check_run_id is a run identifier, not
    prompt content, and must never fragment the cache."""
    transport = ScriptedTransport(responses=[{"criteria": []}])
    queue = _make_queue(session_factory, transport)

    await queue.submit(prompt_type="t", prompt="same content", prompt_version="v1", check_run_id=1)
    await queue.submit(prompt_type="t", prompt="same content", prompt_version="v1", check_run_id=2)

    assert len(transport.calls) == 1
