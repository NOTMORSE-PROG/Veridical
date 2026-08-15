"""V-042 (screen 4u): GET /settings -- read-only thresholds + live
prompt/model version transparency, sourced from real audit-log rows.
Needs a live Postgres (same convention as test_auth_router.py).
"""

import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.config import get_settings

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_settingsapitest"


@pytest.fixture(scope="module")
def api_scratch_url():
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
def client(api_scratch_url, tmp_path, monkeypatch):
    import app.db as db

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    # An explicit blank OS env var, not `delenv` (backend-critic finding,
    # P1, live-reproduced against this repo's own .env): pydantic-settings'
    # precedence is OS env var > `.env` file, but only when the OS env var
    # actually EXISTS -- `delenv` just removes it, falling straight through
    # to whatever a developer's local `.env` has set (e.g. for a live UI
    # review), silently changing `byok_available` underneath these tests.
    # An explicit `""` always wins over the file, matching the same
    # `_blank_path_is_none`-shaped gotcha `config.py` already knows about
    # for its file-path settings, applied here at the test-isolation layer
    # instead. Tests that need BYOK configured set a real value themselves.
    monkeypatch.setenv("BYOK_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    from app.main import app

    with TestClient(app) as c:
        yield c
    db._engine = None
    auth_service._rate_limiter = None


@pytest.fixture()
def seeded(client, api_scratch_url):
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.audit import AuditLog
    from app.models.instructor import Instructor

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine)
            async with factory() as session:
                await session.execute(
                    text("TRUNCATE audit_log, session, instructor RESTART IDENTITY CASCADE")
                )
                session.add(
                    Instructor(
                        email="prof@tip.edu.ph",
                        display_name="Prof",
                        password_hash=hash_password("s3cret!"),
                    )
                )
                await session.commit()

                # An OLDER call for "semantic_grading" at v1, then a NEWER
                # one at v2 -- the settings view must surface v2 (most
                # recent), never the stale one, matching the AC's "versions
                # match audit rows" bar.
                session.add(
                    AuditLog(
                        event_type="llm_call",
                        check_run_id=None,
                        prompt_version="v1",
                        payload={"prompt_type": "semantic_grading", "model": "gemini-old"},
                    )
                )
                await session.commit()
                session.add(
                    AuditLog(
                        event_type="llm_cache_hit",
                        check_run_id=None,
                        prompt_version="v2",
                        payload={"prompt_type": "semantic_grading", "model": "gemini-new"},
                    )
                )
                session.add(
                    AuditLog(
                        event_type="llm_call",
                        check_run_id=None,
                        prompt_version="v1",
                        payload={"prompt_type": "rubric_decomposition", "model": "gemini-new"},
                    )
                )
                # A non-LLM event must never be mistaken for a version row.
                session.add(
                    AuditLog(
                        event_type="report_decided",
                        check_run_id=None,
                        payload={"decision": "approved"},
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    client.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    return client


def test_settings_requires_auth(client):
    assert client.get("/settings").status_code == 401


def test_thresholds_are_config_driven_and_not_yet_editable(seeded):
    resp = seeded.get("/settings")
    assert resp.status_code == 200
    thresholds = resp.json()["thresholds"]
    settings = get_settings()
    assert thresholds["ready_min_score"] == settings.scoring_ready_min_score
    assert thresholds["not_ready_max_score"] == settings.scoring_not_ready_max_score
    assert thresholds["escalation_agreement_threshold"] == settings.escalation_agreement_threshold
    assert thresholds["editable"] is False


def test_prompt_versions_reflect_the_most_recent_real_audit_row_per_type(seeded):
    resp = seeded.get("/settings")
    versions = {p["prompt_type"]: p for p in resp.json()["prompt_versions"]}
    assert versions["semantic_grading"]["prompt_version"] == "v2"
    assert versions["semantic_grading"]["model"] == "gemini-new"
    assert versions["rubric_decomposition"]["prompt_version"] == "v1"
    # The non-llm_call/llm_cache_hit event never becomes a phantom "version".
    assert "report_decided" not in versions
    assert len(versions) == 2


def test_a_low_frequency_prompt_type_survives_a_high_volume_burst_of_another(
    client, api_scratch_url
):
    """backend-critic finding (P2, live-reproduced): the old query took a
    global `LIMIT 500` newest-first, so a real, currently-running LOW-
    frequency prompt type (e.g. rubric_decomposition, once per rubric
    upload) could be silently pushed out of the window by a HIGH-
    frequency one (semantic_grading, one row per criterion per
    manuscript) during a real defense-day burst -- an honest absence, but
    one that defeats this screen's whole "in use" claim. `DISTINCT ON`
    (one query per prompt_type, not one shared window) fixes this
    regardless of volume."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.audit import AuditLog
    from app.models.instructor import Instructor

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine)
            async with factory() as session:
                await session.execute(
                    text("TRUNCATE audit_log, session, instructor RESTART IDENTITY CASCADE")
                )
                session.add(
                    Instructor(
                        email="burst@tip.edu.ph",
                        display_name="Burst Test",
                        password_hash=hash_password("s3cret!"),
                    )
                )
                await session.commit()

                # One old, low-frequency row.
                session.add(
                    AuditLog(
                        event_type="llm_call",
                        check_run_id=None,
                        prompt_version="v1",
                        payload={"prompt_type": "rubric_decomposition", "model": "gemini-old"},
                    )
                )
                await session.commit()
                # A burst of 600 newer, high-frequency rows -- more than
                # the old shared window (500) ever allowed.
                for _ in range(600):
                    session.add(
                        AuditLog(
                            event_type="llm_call",
                            check_run_id=None,
                            prompt_version="v9",
                            payload={"prompt_type": "semantic_grading", "model": "gemini-new"},
                        )
                    )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    client.post("/auth/login", json={"email": "burst@tip.edu.ph", "password": "s3cret!"})

    resp = client.get("/settings")
    versions = {p["prompt_type"]: p for p in resp.json()["prompt_versions"]}
    assert versions["rubric_decomposition"]["prompt_version"] == "v1"
    assert versions["semantic_grading"]["prompt_version"] == "v9"


def test_settings_reports_no_own_key_by_default(seeded):
    resp = seeded.get("/settings")
    assert resp.json()["api_key"] == {"byok_available": False, "has_own_api_key": False}


def test_blank_byok_encryption_key_reports_unavailable_not_available(seeded, monkeypatch):
    """backend-critic finding (P1, live-reproduced): `.env.example` ships
    `BYOK_ENCRYPTION_KEY=` blank as its documented "not configured yet"
    convention -- pydantic-settings reads that as `""`, not `None`. An
    `is not None` check (the original bug) would report `byok_available:
    true` on exactly this state, inviting a save that then hard-fails
    deep inside `encrypt_api_key`. Must read as unavailable, same as
    genuinely unset."""
    monkeypatch.setenv("BYOK_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    resp = seeded.get("/settings")
    assert resp.json()["api_key"]["byok_available"] is False

    resp = seeded.post("/settings/api-key", json={"api_key": "some-real-looking-key"})
    assert resp.status_code == 409


def test_setting_an_api_key_requires_byok_encryption_key_configured(seeded):
    """BYOK_ENCRYPTION_KEY unset on this deployment -- a 409, not a 500,
    and never silently stored unencrypted."""
    resp = seeded.post("/settings/api-key", json={"api_key": "some-real-looking-key"})
    assert resp.status_code == 409


def test_setting_a_valid_api_key_updates_status_and_never_echoes_it(seeded, monkeypatch):
    async def _fake_validate(api_key, settings):
        assert api_key == "AIzaSy-real-looking-key"
        return None

    monkeypatch.setattr("app.settings.service.validate_gemini_api_key", _fake_validate)
    monkeypatch.setenv("BYOK_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    get_settings.cache_clear()

    resp = seeded.post("/settings/api-key", json={"api_key": "AIzaSy-real-looking-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key"] == {"byok_available": True, "has_own_api_key": True}
    assert "AIzaSy-real-looking-key" not in resp.text  # never echoed back

    # And it survives a fresh GET, not just the POST's own response.
    assert seeded.get("/settings").json()["api_key"]["has_own_api_key"] is True


def test_setting_an_invalid_api_key_is_rejected_and_not_stored(seeded, monkeypatch):
    from app.errors import InvalidApiKeyError

    async def _fake_validate(api_key, settings):
        raise InvalidApiKeyError("Gemini rejected this key.")

    monkeypatch.setattr("app.settings.service.validate_gemini_api_key", _fake_validate)
    monkeypatch.setenv("BYOK_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    get_settings.cache_clear()

    resp = seeded.post("/settings/api-key", json={"api_key": "bad-key"})
    assert resp.status_code == 422
    assert seeded.get("/settings").json()["api_key"]["has_own_api_key"] is False


def test_deleting_an_api_key_reverts_to_the_shared_pool(seeded, monkeypatch):
    async def _fake_validate(api_key, settings):
        return None

    monkeypatch.setattr("app.settings.service.validate_gemini_api_key", _fake_validate)
    monkeypatch.setenv("BYOK_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    get_settings.cache_clear()

    seeded.post("/settings/api-key", json={"api_key": "AIzaSy-real-looking-key"})
    assert seeded.get("/settings").json()["api_key"]["has_own_api_key"] is True

    resp = seeded.delete("/settings/api-key")
    assert resp.status_code == 200
    assert resp.json()["api_key"]["has_own_api_key"] is False
    assert seeded.get("/settings").json()["api_key"]["has_own_api_key"] is False


def test_api_key_endpoints_require_auth(client):
    assert client.post("/settings/api-key", json={"api_key": "x"}).status_code == 401
    assert client.delete("/settings/api-key").status_code == 401
