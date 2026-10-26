"""Ordinary optional comments survive real saved facts and private audit history."""

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.db import sqlalchemy_url
from app.models.run import CheckResult
from app.report.judgments import get_judgment_context, get_judgment_history
from app.report.service import resolve_escalation_for_run
from app.share.service import _to_public_report
from tests.test_assessment_revision_live import seed, stored
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")


@pytest.fixture(scope="module")
def scratch_url():
    base = os.environ["DATABASE_URL"]
    name = "veridical_optional_comments_" + uuid.uuid4().hex
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{name}"'))
    try:
        url = _swap_db(base, name)
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
async def factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["mark_pass", "mark_fail", "mark_level"])
@pytest.mark.parametrize("comment", ["", " x "])
async def test_optional_comment_survives_commit_audit_context_history_and_private_report(
    factory, action, comment
):
    owner, run, ids, _ = await seed(factory)
    result_id = ids[1 if action == "mark_level" else 0]
    async with factory() as session:
        result = await session.get(CheckResult, result_id)
        criterion_id = result.criterion_id
        ack = await resolve_escalation_for_run(
            session,
            run,
            result_id,
            owner,
            action,
            comment,
            expected_revision=0,
            level=2 if action == "mark_level" else None,
        )
    row = next(row for row in ack.report.results if row.criterion_id == criterion_id)
    assert row.resolution.reason == comment.strip() and row.resolution.type == action
    assert row.judgment_revision == 1 and ack.report.assessment_revision == 1
    async with factory() as session:
        context = await get_judgment_context(session, run, criterion_id, owner)
    assert context.current.reason == comment.strip() and context.current.provenance == "recorded"
    async with factory() as session:
        history = await get_judgment_history(
            session,
            run,
            criterion_id,
            owner,
            expected_revision=1,
            expected_judgment_revision=1,
        )
    assert len(history.entries) == 1 and not history.history_incomplete
    assert history.entries[0].after.reason == comment.strip()
    assert history.entries[0].after == context.current
    parent, saved_rows, audits = await stored(factory, run)
    assert parent.assessment_revision == 1
    saved = next(row for row in saved_rows if row.id == result_id)
    assert saved.detail["resolution"]["reason"] == comment.strip()
    event = next(row for row in audits if row.event_type == "escalation_resolved")
    assert event.payload["judgment"]["after"]["reason"] == comment.strip()
    public_row = next(
        row for row in _to_public_report(ack.report).results if row.criterion_id == criterion_id
    )
    assert "resolution" not in public_row.model_dump()
