"""V-010: POST /rubrics — the HTTP surface, fake-LLM mode end-to-end
(DoD item 3). Needs a live Postgres (same convention as test_ingest_api.py).
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from tests.test_ingest_pdf import PdfBuilder

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_rubricapitest"


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
    get_settings.cache_clear()
    db._engine = None
    from app.main import app

    with TestClient(app) as c:
        yield c
    db._engine = None


@pytest.fixture(autouse=True)
def _clean_tables(api_scratch_url):
    """Every test shares the same demo instructor (fixed email) in this
    module-scoped scratch DB — family-list assertions need a clean slate.
    Uses its OWN disposable engine, never `app.db.get_engine()` — that
    one is bound to whichever event loop first touches it, and mixing it
    with a separate `asyncio.run()` call corrupted it across loops on
    Windows before (same bug fixed in test_auth_router.py's `seeded`)."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    async def _truncate():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine)() as session:
                await session.execute(
                    text(
                        "TRUNCATE check_run, criterion, rubric, manuscript, instructor "
                        "RESTART IDENTITY CASCADE"
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_truncate())
    yield


def _rubric_pdf(tmp_path):
    b = PdfBuilder()
    b.new_page().line("REQUIRED FORMAT CHECKLIST", bold=True)
    b.line("The manuscript must include an abstract.")
    return b.save(tmp_path / "rubric.pdf")


def test_post_rubrics_returns_persisted_criteria(client, tmp_path):
    path = _rubric_pdf(tmp_path)
    with path.open("rb") as fh:
        resp = client.post(
            "/rubrics",
            params={"title": "Demo Rubric"},
            files={"file": ("rubric.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Demo Rubric"
    assert body["version"] == 1
    assert body["is_active"] is False
    assert len(body["criteria"]) >= 1
    assert {c["type"] for c in body["criteria"]} <= {"structural", "semantic"}
    assert sum(c["weight"] for c in body["criteria"]) == pytest.approx(100.0)


def _upload(client, tmp_path, *, title="Demo Rubric"):
    path = _rubric_pdf(tmp_path)
    with path.open("rb") as fh:
        resp = client.post(
            "/rubrics",
            params={"title": title},
            files={"file": ("rubric.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_get_rubric_returns_the_uploaded_rubric(client, tmp_path):
    uploaded = _upload(client, tmp_path)
    resp = client.get(f"/rubrics/{uploaded['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == uploaded["id"]


def test_get_rubric_404s_on_an_unknown_id(client):
    resp = client.get("/rubrics/999999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_put_criteria_edit_and_confirm_round_trips_through_http(client, tmp_path):
    uploaded = _upload(client, tmp_path)
    edited = [
        {**c, "weight": 60.0, "type": "semantic" if c["type"] == "structural" else "structural"}
        for c in uploaded["criteria"]
    ]
    resp = client.put(
        f"/rubrics/{uploaded['id']}/criteria", json={"criteria": edited, "confirm": True}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_active"] is True

    reloaded = client.get(f"/rubrics/{uploaded['id']}").json()
    assert reloaded["is_active"] is True
    assert all(c["weight"] == 60.0 for c in reloaded["criteria"])


def test_put_criteria_rejects_an_empty_list(client, tmp_path):
    """Server-side backstop for 'deleting all criteria -> confirm disabled'
    (the frontend disables the button; the API also refuses to persist
    zero criteria as defense in depth)."""
    uploaded = _upload(client, tmp_path)
    resp = client.put(f"/rubrics/{uploaded['id']}/criteria", json={"criteria": [], "confirm": True})
    assert resp.status_code == 422


def _confirm(client, rubric_id, criteria):
    resp = client.put(
        f"/rubrics/{rubric_id}/criteria", json={"criteria": criteria, "confirm": True}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_reupload_with_family_id_creates_v2_over_http(client, tmp_path):
    v1 = _upload(client, tmp_path, title="V1")
    _confirm(client, v1["id"], v1["criteria"])

    path = _rubric_pdf(tmp_path)
    with path.open("rb") as fh:
        resp = client.post(
            "/rubrics",
            params={"title": "V2", "family_id": v1["rubric_family_id"]},
            files={"file": ("rubric.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    v2 = resp.json()
    assert v2["rubric_family_id"] == v1["rubric_family_id"]
    assert v2["version"] == 2
    assert v2["is_active"] is False

    v1_reloaded = client.get(f"/rubrics/{v1['id']}").json()
    assert v1_reloaded["version"] == 1
    assert v1_reloaded["is_latest_version"] is False  # superseded by v2


def test_activate_switches_which_version_is_active(client, tmp_path):
    v1 = _upload(client, tmp_path, title="V1")
    _confirm(client, v1["id"], v1["criteria"])
    path = _rubric_pdf(tmp_path)
    with path.open("rb") as fh:
        v2 = client.post(
            "/rubrics",
            params={"title": "V2", "family_id": v1["rubric_family_id"]},
            files={"file": ("rubric.pdf", fh, "application/pdf")},
        ).json()

    resp = client.post(f"/rubrics/{v2['id']}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is True

    v1_after = client.get(f"/rubrics/{v1['id']}").json()
    assert v1_after["is_active"] is False


def test_delete_rubric_succeeds_when_no_reports_exist(client, tmp_path):
    uploaded = _upload(client, tmp_path)
    resp = client.delete(f"/rubrics/{uploaded['id']}")
    assert resp.status_code == 204
    assert client.get(f"/rubrics/{uploaded['id']}").status_code == 404


def test_list_rubric_families_and_versions(client, tmp_path):
    v1 = _upload(client, tmp_path, title="V1")
    _confirm(client, v1["id"], v1["criteria"])
    path = _rubric_pdf(tmp_path)
    with path.open("rb") as fh:
        client.post(
            "/rubrics",
            params={"title": "V2", "family_id": v1["rubric_family_id"]},
            files={"file": ("rubric.pdf", fh, "application/pdf")},
        )

    families = client.get("/rubric-families").json()
    assert len(families) == 1
    assert families[0]["rubric_family_id"] == v1["rubric_family_id"]
    assert families[0]["is_active"] is True  # v1, still active — v2 not confirmed yet

    versions = client.get(f"/rubric-families/{v1['rubric_family_id']}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
