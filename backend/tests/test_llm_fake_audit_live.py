"""BUG-038 live-DB test: `FakeLLMClient` must write an `llm_call` audit
row when given a session_factory (the real `get_llm_client()` wiring),
matching real-mode's own audit contract — the audit log's header claims
"every AI call" is tracked, and that must hold in fake-LLM mode too
(the project's own zero-budget default: every dev session, CI run, and
most demos).
"""

import json
import os
from unittest.mock import ANY

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import sqlalchemy_url
from app.llm.fake import FakeLLMClient
from app.models.audit import AuditLog

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_fakeaudittest"


@pytest.fixture(scope="module")
def scratch_url():
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
def session_factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(text("TRUNCATE audit_log"))
        await session.commit()
    yield


async def test_fake_client_with_no_session_factory_writes_nothing(session_factory):
    """Backward-compat guard: every existing bare `FakeLLMClient()`
    construction across the test suite must keep working unchanged."""
    client = FakeLLMClient()
    await client.complete("rubric_decomposition", "prompt text")

    async with session_factory() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    assert rows == []


async def test_fake_client_with_session_factory_writes_an_honest_llm_call_row(session_factory):
    client = FakeLLMClient(session_factory=session_factory)
    await client.complete(
        "rubric_decomposition",
        "prompt text",
        prompt_version="v3",
        check_run_id=42,
    )

    async with session_factory() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "llm_call"
    assert row.check_run_id == 42
    assert row.prompt_version == "v3"
    # Honest about being a fixture, never dressed up as a real Gemini
    # call (charter rule 9) — the audit log UI/an examiner reading the
    # raw row must be able to tell fake-mode calls apart from real ones.
    assert row.payload["model"] == "fake"
    assert row.payload["fake_llm"] is True
    assert row.payload["prompt_type"] == "rubric_decomposition"
    assert row.payload["prompt"] == "prompt text"
    assert "response" in row.payload


async def test_fake_client_context_bytes_are_summarized_not_inlined(session_factory):
    """BUG-038 review (backend-critic): a first version of `_write_audit`
    ran a blanket `str(v)` over context values, which for the vision pass's
    `images=[png_bytes]` inlined the full escaped binary as text — the
    exact bloat `LLMQueue._json_safe` (queue.py) was written to avoid on
    the real path. Fake-mode audit rows must use the same summarization."""
    client = FakeLLMClient(session_factory=session_factory)
    fake_png = b"\x89PNG\r\n" + b"\x00" * 2048
    await client.complete(
        "image_table_extraction",
        "prompt text",
        prompt_version="v1",
        check_run_id=1,
        images=[fake_png],
    )

    async with session_factory() as session:
        row = (await session.execute(select(AuditLog))).scalar_one()
    stored_images = row.payload["context"]["images"]
    assert stored_images == [{"__bytes__": len(fake_png), "sha256": ANY}]
    assert len(json.dumps(row.payload)) < 2000


async def test_fake_client_audit_row_never_leaks_check_run_id_into_context_payload(
    session_factory,
):
    """check_run_id identifies the pipeline run, not the prompt content —
    same reasoning GeminiLLMClient.complete() already documents for the
    real path — it belongs on the row's own column, not duplicated into
    the free-form context blob."""
    client = FakeLLMClient(session_factory=session_factory)
    await client.complete(
        "semantic_grading",
        "prompt text",
        prompt_version="v1",
        check_run_id=7,
        consistency_pass="pass_1",
    )

    async with session_factory() as session:
        row = (await session.execute(select(AuditLog))).scalar_one()
    assert row.check_run_id == 7
    assert "check_run_id" not in row.payload["context"]
    assert row.payload["context"]["consistency_pass"] == "pass_1"
