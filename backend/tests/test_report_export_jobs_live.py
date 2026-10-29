"""F8.6: authenticated prepare/status/file with real database ownership."""
# ruff: noqa: F811 -- pytest fixtures imported for the same isolated database.

import asyncio
import os
import time

import pytest

from tests.test_report_export_live import (  # noqa: F401
    api_scratch_url,
    client,
    logged_in_with_a_done_run,
)

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="requires PostgreSQL")


def test_prepared_pdf_uses_owned_revision_and_downloads_real_pdf(
    logged_in_with_a_done_run, api_scratch_url
):
    client, run = logged_in_with_a_done_run
    base = f"/check-runs/{run}/report/export-jobs"
    mismatch = client.post(base, json={"expected_revision": 99})
    assert mismatch.status_code == 409
    response = client.post(base, json={"expected_revision": 0})
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "private, no-store"
    job = response.json()
    for _ in range(100):
        response = client.get(f"{base}/{job['job_id']}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["state"] != "preparing":
            break
        time.sleep(0.02)
    assert job["state"] == "ready", job
    pdf = client.get(f"{base}/{job['job_id']}/file")
    assert pdf.status_code == 200, pdf.text[:200]
    assert pdf.content.startswith(b"%PDF")
    assert pdf.headers["cache-control"] == "private, no-store"
    assert client.get(f"/check-runs/999/report/export-jobs/{job['job_id']}/file").status_code == 404
    assert client.get(f"{base}/interrupted").status_code == 410

    async def change(sql):
        import asyncpg

        connection = await asyncpg.connect(api_scratch_url)
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    asyncio.run(change("UPDATE readiness_report SET assessment_revision=1"))
    assert client.get(f"{base}/{job['job_id']}/file").status_code == 409
    assert client.get(f"{base}/{job['job_id']}").status_code == 409
    asyncio.run(change("UPDATE readiness_report SET assessment_revision=0"))
    asyncio.run(change("UPDATE manuscript SET purged_at=now()"))
    assert client.get(f"{base}/{job['job_id']}/file").status_code == 409


@pytest.mark.parametrize(
    "suffix,method", [("", "post"), ("/unknown", "get"), ("/unknown/file", "get")]
)
def test_job_routes_require_auth_and_are_private(client, suffix, method):
    kwargs = {"json": {"expected_revision": 0}} if method == "post" else {}
    response = getattr(client, method)("/check-runs/1/report/export-jobs" + suffix, **kwargs)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
