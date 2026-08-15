"""V-052 (BYOK): `get_llm_client_for` / `get_quota_status_for` -- resolving
an instructor's own Gemini key (falling back to the shared pool) instead
of always spending the shared key. Needs a live Postgres (real Instructor
rows with a real FK-backed `gemini_api_key_encrypted` column, same
convention as test_llm_queue.py) -- construction only, no real network call
(same reasoning as test_llm_client_factory.py: building a `GeminiTransport`
makes no request by itself).
"""

import os
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.llm as llm_module
from app.auth.security import hash_password
from app.config import Settings
from app.db import sqlalchemy_url
from app.errors import ApiDownError, InvalidApiKeyError, QuotaExhaustedError
from app.llm import FallbackLLMClient, GeminiLLMClient, get_llm_client_for, get_quota_status_for
from app.llm.base import LLMClient
from app.llm.fake import FakeLLMClient
from app.llm.keystore import encrypt_api_key
from app.models.instructor import Instructor

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_byokfactorytest"
_MASTER_KEY = Fernet.generate_key().decode("utf-8")


@pytest.fixture(scope="module")
def byok_scratch_url():
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
def session_factory(byok_scratch_url, monkeypatch):
    # `get_llm_client_for`/`get_quota_status_for` build their SHARED client
    # via `db.get_session_factory()` internally (the app-wide factory, not
    # this fixture's own) -- pointed at the SAME scratch DB here so the
    # test doesn't run two independent engines/connection pools against
    # two different databases in the same process, which caused real,
    # intermittent Windows ProactorEventLoop write-after-close errors
    # during this test file's own development (a harness artifact, not a
    # product bug -- every failure reproduced was 100% reliable in
    # isolation and only appeared several tests into a shared run).
    import app.db as db

    monkeypatch.setenv("DATABASE_URL", byok_scratch_url)
    db._engine = None
    engine = create_async_engine(sqlalchemy_url(byok_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)
    db._engine = None


@pytest.fixture(autouse=True)
def _reset_llm_module_caches():
    llm_module._real_client = None
    llm_module._instructor_clients.clear()
    yield
    llm_module._real_client = None
    llm_module._instructor_clients.clear()


def _settings(**overrides) -> Settings:
    kwargs: dict[str, Any] = dict(
        _env_file=None,
        veridical_fake_llm=False,
        gemini_api_key="shared-key",
        byok_encryption_key=_MASTER_KEY,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _make_instructor(session_factory, *, own_key: str | None = None) -> int:
    import uuid

    async with session_factory() as session:
        instructor = Instructor(
            email=f"byok-{uuid.uuid4().hex[:8]}@tip.edu.ph",
            display_name="I",
            password_hash=hash_password("x"),
            gemini_api_key_encrypted=encrypt_api_key(own_key, _settings()) if own_key else None,
        )
        session.add(instructor)
        await session.commit()
        return instructor.id


async def test_no_instructor_id_returns_the_shared_client_directly(session_factory):
    settings = _settings()
    async with session_factory() as session:
        client = await get_llm_client_for(session, settings, None)
    assert isinstance(client, GeminiLLMClient)


async def test_instructor_with_no_own_key_gets_the_shared_client_directly(session_factory):
    settings = _settings()
    instructor_id = await _make_instructor(session_factory)
    async with session_factory() as session:
        client = await get_llm_client_for(session, settings, instructor_id)
    assert isinstance(client, GeminiLLMClient)  # not wrapped in a fallback


async def test_instructor_with_own_key_gets_a_fallback_wrapped_client(session_factory):
    settings = _settings()
    instructor_id = await _make_instructor(session_factory, own_key="instructor-own-key")
    async with session_factory() as session:
        client = await get_llm_client_for(session, settings, instructor_id)
    assert isinstance(client, FallbackLLMClient)


async def test_fake_mode_ignores_instructor_id_entirely(session_factory):
    settings = _settings(veridical_fake_llm=True)
    instructor_id = await _make_instructor(session_factory, own_key="instructor-own-key")
    async with session_factory() as session:
        client = await get_llm_client_for(session, settings, instructor_id)
    assert isinstance(client, FakeLLMClient)


class _ScriptedClient(LLMClient):
    def __init__(self, outcome: Exception | dict[str, Any]) -> None:
        self._outcome = outcome
        self.calls = 0

    async def complete(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


async def test_fallback_uses_primary_when_it_succeeds():
    primary = _ScriptedClient({"from": "own"})
    fallback = _ScriptedClient({"from": "shared"})
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    result = await client.complete("t", "p")
    assert result == {"from": "own"}
    assert fallback.calls == 0


async def test_fallback_reroutes_to_shared_on_quota_exhaustion():
    primary = _ScriptedClient(QuotaExhaustedError("spent"))
    fallback = _ScriptedClient({"from": "shared"})
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    result = await client.complete("t", "p")
    assert result == {"from": "shared"}
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_fallback_reroutes_to_shared_on_an_invalid_or_revoked_key():
    """backend-critic finding (P1): a revoked/bad BYOK key must degrade to
    the shared key too, not just quota exhaustion -- `queue.py`/
    `transport.py` classify a 401/403 into `InvalidApiKeyError`
    specifically so `FallbackLLMClient` has something typed to catch here
    instead of an unmapped SDK exception."""
    primary = _ScriptedClient(InvalidApiKeyError("Gemini rejected this API key: bad"))
    fallback = _ScriptedClient({"from": "shared"})
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    result = await client.complete("t", "p")
    assert result == {"from": "shared"}
    assert primary.calls == 1
    assert fallback.calls == 1


async def test_fallback_does_not_reroute_on_a_transient_outage():
    """A transient outage on the instructor's own key is a real signal
    (Google-side blip) -- silently rerouting to the shared key would hide
    it as if nothing were wrong. Distinct from `InvalidApiKeyError` above:
    an outage might resolve on its own retry (already exhausted by
    `LLMQueue`'s own backoff before this class ever sees it), a bad key
    never will."""
    primary = _ScriptedClient(ApiDownError("gemini down"))
    fallback = _ScriptedClient({"from": "shared"})
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    with pytest.raises(ApiDownError):
        await client.complete("t", "p")
    assert fallback.calls == 0


async def test_deleting_the_key_makes_the_next_resolve_fall_back_to_shared(session_factory):
    """No explicit cache-invalidation call needed -- the per-instructor
    client cache compares the stored encrypted blob on every lookup."""
    settings = _settings()
    instructor_id = await _make_instructor(session_factory, own_key="instructor-own-key")

    async with session_factory() as session:
        first = await get_llm_client_for(session, settings, instructor_id)
    assert isinstance(first, FallbackLLMClient)

    async with session_factory() as session:
        instructor = await session.get(Instructor, instructor_id)
        instructor.gemini_api_key_encrypted = None
        await session.commit()

    async with session_factory() as session:
        second = await get_llm_client_for(session, settings, instructor_id)
    assert isinstance(second, GeminiLLMClient)
    assert not isinstance(second, FallbackLLMClient)


async def test_evict_instructor_client_actually_drops_the_cache_entry(session_factory):
    """backend-critic finding (P2): the encrypted-blob comparison already
    makes routing correct on the NEXT lookup after a key change, but the
    OLD decrypted key + its live GeminiLLMClient stayed reachable in
    `_instructor_clients` indefinitely until then -- a real secret-hygiene
    gap, not just a routing one. `evict_instructor_client` (called by
    `app/settings/service.py` on delete/rotate) must actually remove the
    entry, not just leave it to self-invalidate later."""
    settings = _settings()
    instructor_id = await _make_instructor(session_factory, own_key="instructor-own-key")

    async with session_factory() as session:
        await get_llm_client_for(session, settings, instructor_id)
    assert instructor_id in llm_module._instructor_clients

    llm_module.evict_instructor_client(instructor_id)
    assert instructor_id not in llm_module._instructor_clients


async def test_undecryptable_key_falls_back_to_shared_instead_of_raising(session_factory):
    """security-auditor finding (High, live-reproduced): a stored key that
    can no longer be decrypted (BYOK_ENCRYPTION_KEY rotated since it was
    saved -- the ticket's own documented, unsolved rotation limitation)
    used to raise `BYOKNotConfiguredError` straight out of
    `get_llm_client_for`, called BEFORE `run_check_run`'s own try/except
    even starts -- wedging the single-worker FIFO queue for every
    instructor, not just this one. Must degrade to the shared key instead,
    the same way "no key configured" already does."""
    settings = _settings()
    instructor_id = await _make_instructor(session_factory, own_key="instructor-own-key")

    rotated_settings = _settings(byok_encryption_key=Fernet.generate_key().decode("utf-8"))
    async with session_factory() as session:
        client = await get_llm_client_for(session, rotated_settings, instructor_id)
    assert isinstance(client, GeminiLLMClient)
    assert not isinstance(client, FallbackLLMClient)

    async with session_factory() as session:
        _status, key_source = await get_quota_status_for(session, rotated_settings, instructor_id)
    assert key_source == "shared"

    # The original (correct) master key still decrypts it fine -- this is
    # a graceful per-call degradation, not silent data loss.
    async with session_factory() as session:
        client = await get_llm_client_for(session, settings, instructor_id)
    assert isinstance(client, FallbackLLMClient)


async def test_quota_status_reports_shared_when_no_own_key(session_factory):
    settings = _settings()
    instructor_id = await _make_instructor(session_factory)
    async with session_factory() as session:
        _status, key_source = await get_quota_status_for(session, settings, instructor_id)
    assert key_source == "shared"


async def test_quota_status_reports_own_when_a_key_is_configured(session_factory):
    settings = _settings()
    instructor_id = await _make_instructor(session_factory, own_key="instructor-own-key")
    async with session_factory() as session:
        _status, key_source = await get_quota_status_for(session, settings, instructor_id)
    assert key_source == "own"
