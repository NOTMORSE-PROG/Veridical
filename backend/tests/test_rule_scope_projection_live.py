"""F3/F8: legacy scope disclosure is a read-only private/public projection."""

import asyncio
import copy
import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import sqlalchemy_url
from app.models.rubric import Criterion
from app.models.run import CheckResult
from tests.test_share_router_live import api_scratch_url as api_scratch_url
from tests.test_share_router_live import client as client
from tests.test_share_router_live import logged_in_with_a_done_run as logged_in_with_a_done_run

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="owned PostgreSQL required"
)


@pytest.mark.parametrize("instructor_recorded", [False, True])
def test_legacy_private_and_anonymous_reads_preserve_saved_academic_records(
    logged_in_with_a_done_run, api_scratch_url, instructor_recorded
):
    client, run_id = logged_in_with_a_done_run

    async def seed_or_snapshot(seed=False):
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                if seed:
                    row = await session.scalar(
                        select(CheckResult).where(CheckResult.check_run_id == run_id)
                    )
                    criterion = await session.get(Criterion, row.criterion_id)
                    criterion.text = "Manuscript contains an abstract of at most 250 words"
                    detail = copy.deepcopy(row.detail)
                    detail.update(
                        target="abstract", matched_title="Abstract", private_path="PRIVATE_PATH"
                    )
                    if instructor_recorded:
                        detail["resolution"] = {
                            "type": "mark_pass",
                            "reason": "PRIVATE_INSTRUCTOR_REASON",
                            "ai_majority_verdict": None,
                        }
                        detail["original_result"] = {"outcome": "passed", "score": 100}
                    row.detail = detail
                    await session.commit()
                snapshots = {}
                # Fixed table names, never supplied by a request or criterion.
                for table in ("criterion", "check_result", "readiness_report", "audit_log"):
                    snapshots[table] = list(
                        await session.scalars(
                            text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY id")
                        )
                    )
                return snapshots
        finally:
            await engine.dispose()

    asyncio.run(seed_or_snapshot(seed=True))
    shared = client.post(f"/check-runs/{run_id}/share", json={})
    assert shared.status_code == 200
    token = shared.json()["token"]
    before = asyncio.run(seed_or_snapshot())
    private = client.get(f"/check-runs/{run_id}/report")
    assert private.status_code == 200
    row = private.json()["results"][0]
    assert row["rule_scope_unconfirmed"] is True
    assert row["outcome"] == "passed" and row["score"] == 100
    public = client.get(f"/shared/{token}/report", headers={"cookie": ""})
    assert public.status_code == 200
    projected = public.json()["report"]["results"][0]
    assert projected["rule_scope_unconfirmed"] is True
    assert projected["resolved"] is instructor_recorded
    assert projected["outcome"] == "passed" and projected["score"] == 100
    assert "resolution" not in projected
    for sentinel in (
        "PRIVATE_PATH",
        "PRIVATE_INSTRUCTOR_REASON",
        "input_digest",
        "section_presence_scope",
    ):
        assert sentinel not in public.text
    assert client.get(f"/check-runs/{run_id}/report").json() == private.json()
    assert client.get(f"/shared/{token}/report", headers={"cookie": ""}).json() == public.json()
    assert asyncio.run(seed_or_snapshot()) == before
