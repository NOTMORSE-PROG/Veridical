"""Actual quota route/configuration boundary with RAM ledgers, no provider/DB."""

import base64
import json
import socket
from types import SimpleNamespace

import httpx
import pytest

import app.llm as llm
import app.llm.router as quota_router
from app import db
from app.auth.dependencies import get_current_instructor
from app.config import Settings
from app.llm.client import GeminiLLMClient
from app.llm.keystore import encrypt_api_key
from app.llm.pool import load_model_pool
from app.llm.queue import LLMQueue
from app.llm.transport import GeminiTransport
from app.main import app

ACTOR = 47101
FOREIGN = 47102
ORIGIN = "http://127.0.0.1:59991"
MASTER = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")


@pytest.fixture
async def harness(monkeypatch, tmp_path):
    pool = tmp_path / "pool.json"
    pool.write_text(
        json.dumps(
            {
                "models": [
                    {"model": "current", "rpm": 10, "daily_quota": 20, "vision": False},
                    {"model": "other", "rpm": 10, "daily_quota": 10, "vision": True},
                ],
                "budget_aliases": {"old": "current"},
            }
        )
    )
    state = SimpleNamespace(
        settings=Settings(
            _env_file=None,
            veridical_env="test",
            veridical_fake_llm=False,
            gemini_api_key=None,
            byok_encryption_key=None,
            llm_model_pool_file=pool,
            cors_allowed_origins=ORIGIN,
        ),
        encrypted=None,
        gets=[],
        queries=[],
        forbidden=[],
        ledger_failure=False,
    )

    def forbidden(*args, **kwargs):
        state.forbidden.append("forbidden operation")
        raise AssertionError("This status control forbids SDK, network and ledger mutations")

    # The async fixture runs after Windows has initialized its local event loop.
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(GeminiTransport, "__init__", forbidden)
    for method in (
        "submit",
        "_try_reserve_quota",
        "_close_island",
        "_note_cache_hit",
        "_write_cache",
    ):
        monkeypatch.setattr(LLMQueue, method, forbidden)
    monkeypatch.setattr(llm, "_real_client", None)
    monkeypatch.setattr(llm, "_instructor_clients", {})
    monkeypatch.setattr(quota_router, "get_settings", lambda: state.settings)

    class InstructorSession:
        async def get(self, model, identity):
            state.gets.append(identity)
            assert identity == ACTOR
            return SimpleNamespace(id=ACTOR, gemini_api_key_encrypted=state.encrypted)

    class Ledger:
        def __init__(self, owner):
            self.owner = owner

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, statement):
            assert statement.is_select
            compiled = statement.compile()
            sql, params = str(compiled), compiled.params
            if self.owner is None:
                assert "llm_quota_counter.instructor_id IS NULL" in sql
            else:
                assert self.owner == ACTOR
                assert self.owner in [v for k, v in params.items() if k.startswith("instructor_id")]
            day = params["quota_day_1"]
            state.queries.append((self.owner, day))
            if state.ledger_failure:
                raise RuntimeError("Controlled ledger failure")
            rows = [
                (None, day, "current", 3, 2),
                (None, day, "old", 20, 3),
                (None, day, "retired", 8, 1),
                (None, day, "unknown", 2, 0),
                (ACTOR, day, "current", 2, 4),
                (ACTOR, day, "old", 5, 3),
                (FOREIGN, day, "current", 97, 11),
                (self.owner, "2000-01-01", "old", 99, 13),
            ]
            selected = [
                (model, calls, hits)
                for owner, recorded_day, model, calls, hits in rows
                if owner == self.owner and recorded_day == day
            ]
            return SimpleNamespace(all=lambda: selected)

    def client(owner):
        return GeminiLLMClient(
            LLMQueue(
                transport=SimpleNamespace(generate=forbidden),
                session_factory=lambda: Ledger(owner),
                pool=load_model_pool(pool),
                temperature=state.settings.gemini_temperature,
                max_retries=state.settings.llm_max_retries,
                retry_base_seconds=state.settings.llm_retry_base_seconds,
                reset_timezone=state.settings.llm_quota_reset_timezone,
                instructor_id=owner,
                cache_contract_version=state.settings.llm_cache_contract_version,
            )
        )

    state.client = client
    llm._instructor_clients[FOREIGN] = (b"foreign-only", client(FOREIGN))
    monkeypatch.setattr(db, "get_session_factory", lambda: lambda: Ledger(None))

    async def session_dependency():
        yield InstructorSession()

    async def auth_dependency():
        return SimpleNamespace(id=ACTOR)

    monkeypatch.setitem(app.dependency_overrides, db.get_session, session_dependency)
    monkeypatch.setitem(app.dependency_overrides, get_current_instructor, auth_dependency)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://127.0.0.1",
    ) as api:
        state.api = api
        yield state


@pytest.mark.parametrize("case", ["missing", "empty_shared", "unreadable_own", "empty_decrypted"])
async def test_missing_configuration_keeps_shared_history_readable(harness, case):
    if case == "empty_shared":
        harness.settings.gemini_api_key = ""
    elif case == "unreadable_own":
        harness.encrypted = b"unreadable controlled key"
    elif case == "empty_decrypted":
        harness.settings.byok_encryption_key = MASTER
        harness.encrypted = encrypt_api_key("", harness.settings)
    response = await harness.api.get("/quota", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "live" and body["key_source"] == "shared"
    assert body["key_state"] == "unavailable"
    assert body["calls_used"] == 20 and body["calls_remaining"] == 10
    assert body["recorded_budget_debits"] == 33 and body["cache_hits_today"] == 6
    assert body["cache_hit_rate"] is None
    assert body["models"][0]["recorded_budget_debits"] == 23
    assert body["models"][0]["historical_budget_debits"] == 20
    assert {row["model"] for row in body["legacy_models"]} == {"old", "retired", "unknown"}
    assert harness.gets == [ACTOR] and len(harness.queries) == 1
    assert harness.queries[0][0] is None and not harness.forbidden


@pytest.mark.parametrize("owner", ["own", "warm_shared", "unreadable_own_shared"])
async def test_existing_selected_client_and_ledger_scope_are_preserved(harness, owner):
    if owner == "own":
        harness.settings.byok_encryption_key = MASTER
        harness.encrypted = encrypt_api_key("controlled-personal-never-sent", harness.settings)
        llm._instructor_clients[ACTOR] = (harness.encrypted, harness.client(ACTOR))
    else:
        llm._real_client = harness.client(None)
        if owner == "unreadable_own_shared":
            harness.encrypted = b"unreadable controlled key"
    response = await harness.api.get("/quota")
    assert response.status_code == 200
    body = response.json()
    assert body["key_state"] == "configured"
    assert body["key_source"] == ("own" if owner == "own" else "shared")
    assert body["recorded_budget_debits"] == (7 if owner == "own" else 33)
    assert body["calls_remaining"] == (23 if owner == "own" else 10)
    assert len(harness.queries) == 1 and not harness.forbidden


async def test_ledger_failure_is_not_reported_as_successful_empty_records(harness):
    harness.ledger_failure = True
    response = await harness.api.get("/quota")
    assert response.status_code == 500
    assert len(harness.queries) == 1 and not harness.forbidden


async def test_unrelated_selection_failure_is_not_relabelled_missing_configuration(
    harness, monkeypatch
):
    async def broken(*args):
        raise ValueError("Controlled unrelated failure")

    monkeypatch.setattr(llm, "_resolve_own_client", broken)
    response = await harness.api.get("/quota")
    assert response.status_code == 500
    assert not harness.queries and not harness.forbidden


async def test_bad_pool_file_does_not_fabricate_successful_unavailable_status(harness):
    harness.settings.llm_model_pool_file.write_text('{"models": []}')
    response = await harness.api.get("/quota")
    assert response.status_code == 500
    assert not harness.queries and not harness.forbidden


async def test_fake_mode_does_not_imply_configuration_or_read_real_ledger(harness):
    harness.settings.veridical_fake_llm = True
    response = await harness.api.get("/quota")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "fake" and body["key_state"] == "not_checked"
    assert body["recorded_budget_debits"] == 0
    assert not harness.gets and not harness.queries and not harness.forbidden


async def test_selected_ledger_configuration_error_is_not_caught_as_selection_failure(
    harness, monkeypatch
):
    selected = harness.client(None)

    async def broken():
        raise llm.LLMNotConfiguredError("Controlled selected-ledger failure")

    monkeypatch.setattr(selected.queue, "get_quota_status", broken)
    llm._real_client = selected
    response = await harness.api.get("/quota")
    assert response.status_code == 500
    assert not harness.queries and not harness.forbidden


async def test_signed_out_request_cannot_read_any_budget_record(harness, monkeypatch):
    monkeypatch.delitem(app.dependency_overrides, get_current_instructor)
    response = await harness.api.get("/quota")
    assert response.status_code == 401
    assert not harness.gets and not harness.queries and not harness.forbidden
