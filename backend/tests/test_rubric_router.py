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
    assert len(body["criteria"]) >= 1
    assert {c["type"] for c in body["criteria"]} <= {"structural", "semantic"}
    assert sum(c["weight"] for c in body["criteria"]) == pytest.approx(100.0)
