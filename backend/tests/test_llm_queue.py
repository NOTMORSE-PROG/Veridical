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
from app.llm.queue import LLMQueue, TransportRateLimited, TransportServerError
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


async def test_check_run_id_is_excluded_from_the_cache_key(session_factory):
    """D-011: a Flow E re-run under a new check_run must still hit cache for
    the same rubric/prompt content — check_run_id is a run identifier, not
    prompt content, and must never fragment the cache."""
    transport = ScriptedTransport(responses=[{"criteria": []}])
    queue = _make_queue(session_factory, transport)

    await queue.submit(prompt_type="t", prompt="same content", prompt_version="v1", check_run_id=1)
    await queue.submit(prompt_type="t", prompt="same content", prompt_version="v1", check_run_id=2)

    assert len(transport.calls) == 1
