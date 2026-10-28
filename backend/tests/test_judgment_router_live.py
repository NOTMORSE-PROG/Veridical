"""V-085 authenticated route authority, enrichment and adverse stored history."""

import asyncio
import os
from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.db import sqlalchemy_url
from app.models.audit import AuditLog
from app.models.enums import ResultOutcome
from app.models.run import CheckResult
from tests import test_share_router_live as sharing
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")
SCRATCH_DB = "veridical_judgment_http_aeac_test"
client = sharing.client
logged_in_with_a_done_run = sharing.logged_in_with_a_done_run


@pytest.fixture(scope="module")
def api_scratch_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    try:
        url = _swap_db(base, SCRATCH_DB)
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{SCRATCH_DB}" WITH (FORCE)'))


def test_private_revision_routes_preserve_figures_and_qualify_malformed_history(
    logged_in_with_a_done_run, api_scratch_url, monkeypatch
):
    import app.report.router as router

    client, run = logged_in_with_a_done_run
    attached = []

    async def figures(session, report):
        attached.append(report.assessment_revision)
        return report.model_copy(update={"figure_readings": []})

    monkeypatch.setattr(router, "attach_report_figures", figures)

    async def prepare():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                row = await session.scalar(
                    select(CheckResult).where(
                        CheckResult.check_run_id == run, CheckResult.criterion_id.isnot(None)
                    )
                )
                row.outcome, row.score = ResultOutcome.escalated, None
                row.detail = {"reason": "Original assessment remains intact"}
                await session.commit()
                return row.id, row.criterion_id
        finally:
            await engine.dispose()

    result_id, criterion = asyncio.run(prepare())
    initial = client.post(
        f"/check-runs/{run}/escalated/{result_id}/resolve",
        json={
            "resolution": "mark_pass",
            "reason": "I inspected the source evidence.",
            "expected_revision": 0,
        },
    )
    assert initial.status_code == 200, initial.text
    prior_pdf = client.get(f"/check-runs/{run}/report/export.pdf")
    assert prior_pdf.status_code == 200
    base = f"/check-runs/{run}/criteria/{criterion}/judgment"
    body = {
        "expected_revision": 1,
        "expected_judgment_revision": 1,
        "resolution": "mark_fail",
        "reason": "I inspected further source evidence and corrected the grade.",
    }
    for changed in (
        {"expected_revision": True},
        {"expected_judgment_revision": "1"},
        {"actor_id": 2},
    ):
        assert client.post(base + "/revisions", json={**body, **changed}).status_code == 422
    saved = client.post(base + "/revisions", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.headers["cache-control"] == "no-store"
    ack = saved.json()
    assert ack["report"]["figure_readings"] == [] and attached == [1, 2]
    assert ack["context"]["assessment_revision"] == ack["report"]["assessment_revision"] == 2
    assert ack["entry"]["after"]["score"] == ack["context"]["current"]["score"] == 0
    current_pdf = client.get(f"/check-runs/{run}/report/export.pdf")
    assert current_pdf.status_code == 200
    import fitz

    def pdf_text(response):
        with fitz.open(stream=response.content, filetype="pdf") as document:
            return " ".join(" ".join(page.get_text() for page in document).split())

    assert body["reason"] in pdf_text(current_pdf)
    assert "I inspected the source evidence." not in pdf_text(current_pdf)
    assert "I inspected the source evidence." in pdf_text(prior_pdf)
    assert body["reason"] not in pdf_text(prior_pdf)
    assert client.post(base + "/revisions", json=body).status_code == 409
    query = {"expected_revision": 2, "expected_judgment_revision": 2}
    good = client.get(base + "/history", params=query)
    assert good.status_code == 200 and not good.json()["history_incomplete"]
    entries = good.json()["entries"]

    async def append_bad_events():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                events = []
                for score in (999, "NaN"):
                    payload = deepcopy(entries[0])
                    payload.pop("entry_id")
                    payload.pop("performed_at")
                    now = await session.scalar(select(func.clock_timestamp()))
                    payload["after"].update(score=score, recorded_at=now.isoformat())
                    event = AuditLog(
                        event_type="judgment_revised",
                        check_run_id=run,
                        created_at=now,
                        payload={"judgment": payload},
                    )
                    session.add(event)
                    events.append(event)
                await session.commit()
                return {event.id: deepcopy(event.payload) for event in events}
        finally:
            await engine.dispose()

    bad = asyncio.run(append_bad_events())
    page = client.get(base + "/history", params=query)
    assert page.status_code == 200, page.text
    assert page.json()["history_incomplete"]
    assert page.json()["entries"] == entries

    async def raw_history():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine)() as session:
                rows = await session.scalars(select(AuditLog).where(AuditLog.id.in_(bad)))
                return {event.id: event.payload for event in rows}
        finally:
            await engine.dispose()

    assert asyncio.run(raw_history()) == bad
    token = client.post(f"/check-runs/{run}/share", json={}).json()["token"]
    client.cookies.clear()
    assert client.get(base).status_code == 401
    assert (
        client.get(
            base + "/history", params=query, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )
    assert client.post(base + "/revisions", json=body).status_code == 401
    public = client.get(f"/shared/{token}/report")
    assert public.status_code == 200
    assert body["reason"] not in public.text
    assert public.json()["report"]["assessment_revision"] == 2
    for key in (
        "judgment_revision",
        "check_result_id",
        "performed_by",
        "actor_id",
    ):
        assert key not in public.text
    assert (
        client.post(
            "/auth/login", json={"email": "stranger@tip.edu.ph", "password": "other!"}
        ).status_code
        == 200
    )
    assert client.get(base).status_code == 404
    assert client.get(base + "/history", params=query).status_code == 404
    assert client.post(base + "/revisions", json={**body, **query}).status_code == 404
