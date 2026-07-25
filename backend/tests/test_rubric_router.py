"""V-010: POST /rubrics — the HTTP surface, fake-LLM mode end-to-end
(DoD item 3). Needs a live Postgres (same convention as test_ingest_api.py).
"""

import os

import pytest
from fastapi.testclient import TestClient

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
