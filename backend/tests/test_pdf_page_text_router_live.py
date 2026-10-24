"""F8/4w authenticated source-name, exact-page and immutable-history controls."""

import asyncio
import hashlib

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import sqlalchemy_url
from app.ingest.pdf import extract_document
from app.ingest.service import _write_raw_store, raw_store_path
from app.models.enums import IngestStatus
from app.models.manuscript import Manuscript
from tests import test_report_router_live as report_controls
from tests.test_ingest_fixtures import FIXTURE_DIR
from tests.test_report_router_live import (
    _saved_assessment_rows,
    _set_run_original,
    live,
)

pytestmark = live
api_scratch_url = report_controls.api_scratch_url
client = report_controls.client
logged_in_with_a_done_run = report_controls.logged_in_with_a_done_run


@pytest.mark.parametrize("suffix", [".pdf", ".txt", ".docx"])
def test_exact_saved_page_ignores_name_and_keeps_history(
    logged_in_with_a_done_run, api_scratch_url, tmp_path, suffix
):
    client, run_id = logged_in_with_a_done_run
    path = tmp_path / f"original{suffix}"
    payload = (FIXTURE_DIR / "native.pdf").read_bytes()
    path.write_bytes(payload)
    settings = get_settings()
    extraction = extract_document(str(path), settings)
    mid = _set_run_original(api_scratch_url, run_id, path)

    async def mark_ingested():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine)() as session:
                manuscript = await session.get(Manuscript, mid)
                manuscript.content_hash = hashlib.sha256(payload).hexdigest()
                manuscript.ingest_status = IngestStatus.done
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(mark_ingested())
    raw = raw_store_path(settings, mid)
    _write_raw_store(extraction, raw)
    raw_before = hashlib.sha256(raw.read_bytes()).hexdigest()
    rows_before = _saved_assessment_rows(api_scratch_url)
    for page in [1, 2]:
        response = client.get(f"/check-runs/{run_id}/document/page-text?page={page}")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["state"] == "available"
        assert result["page"] == page and result["page_count"] == extraction.page_count
        assert [block["text"] for block in result["blocks"]] == [
            block.text for block in extraction.blocks if block.page == page
        ]
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/check-runs/{run_id}/document/page-text?page=0").status_code == 422
    upper = client.get(f"/check-runs/{run_id}/document/page-text?page=9999")
    assert upper.status_code == 200 and upper.json()["reason"] == "page_out_of_range"
    assert not upper.json()["blocks"]
    assert _saved_assessment_rows(api_scratch_url) == rows_before
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == raw_before
    assert path.read_bytes() == payload
    client.post("/auth/logout")
    assert client.get(f"/check-runs/{run_id}/document/page-text?page=2").status_code == 401
