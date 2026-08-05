"""V-008: the ingestion HTTP surface — taxonomy end-to-end.

Needs a live Postgres (same DATABASE_URL convention as the other
integration suites); runs against its own scratch database.
"""

import os

import pytest
from fastapi.testclient import TestClient

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.config import get_settings
from tests.test_ingest_fixtures import FIXTURE_DIR

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)

SCRATCH_DB = "veridical_apitest"


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
    """App wired to the scratch DB, a temp data dir, and fake-LLM mode —
    logged in as a seeded instructor (BUG-002/D-020: /manuscripts/ingest
    now requires auth, same as every other manuscript-scoped route)."""
    import asyncio

    import app.db as db
    import app.ratelimit as ratelimit
    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None  # drop any engine bound to another database
    auth_service._rate_limiter = None
    ratelimit._limiters.clear()  # don't leak ingest-rate-limit counts across tests
    from app.main import app

    async def seed_instructor():
        # api_scratch_url is module-scoped (not truncated between tests,
        # matching this file's prior convention) — find-or-create so a
        # repeat email doesn't violate the unique constraint on a 2nd test.
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                existing = await session.scalar(
                    select(Instructor).where(Instructor.email == "prof@tip.edu.ph")
                )
                if existing is None:
                    session.add(
                        Instructor(
                            email="prof@tip.edu.ph",
                            display_name="Prof",
                            password_hash=hash_password("s3cret!"),
                        )
                    )
                    await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed_instructor())

    with TestClient(app) as tc:
        tc.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
        yield tc
    db._engine = None
    get_settings.cache_clear()


def _upload(client, fixture_name: str, as_name: str | None = None, data: bytes | None = None):
    content = data if data is not None else (FIXTURE_DIR / fixture_name).read_bytes()
    return client.post(
        "/manuscripts/ingest",
        files={"file": (as_name or fixture_name, content, "application/octet-stream")},
    )


@live
def test_upload_pdf_returns_summary(client):
    r = _upload(client, "native.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingest_status"] == "done"
    assert body["page_count"] == 6 and body["anchor_kind"] == "page"
    assert body["citations"] == 2
    assert body["section_tree"]["source"] == "heuristics"
    assert body["notes"] == []


@live
def test_malformed_upload_is_a_422_not_a_500(client):
    r = _upload(client, "malformed.pdf")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "file_malformed"
    assert "re-export" in err["message"]  # user-fixable wording, no stack trace


@live
def test_oversized_upload_rejected_early_with_413(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    r = _upload(client, "native.pdf", data=b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413
    err = r.json()["error"]
    assert err["code"] == "too_large" and "1 MB" in err["message"]


@live
def test_docx_renamed_to_pdf_is_sniffed_and_parsed(client):
    r = _upload(client, "docx_renamed.pdf")
    assert r.status_code == 200
    assert r.json()["anchor_kind"] == "paragraph"  # parsed as the DOCX it is


@live
def test_image_only_scan_continues_with_limited_checks_note(client):
    r = _upload(client, "scan_imageonly.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingest_status"] == "done"  # pipeline CONTINUED (F1.7)
    assert body["image_only"] is True
    assert any("limited" in note for note in body["notes"])


@live
def test_encrypted_pdf_is_a_422_with_unlock_message(client):
    r = _upload(client, "encrypted.pdf")
    assert r.status_code == 422
    assert "password-protected" in r.json()["error"]["message"]


@live
def test_legacy_doc_names_the_unsupported_type(client):
    r = _upload(client, "legacy.doc")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "file_malformed" and "'.doc'" in err["message"]
