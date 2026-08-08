"""V-035 tests: the F4 assembly (`run_internal_agreement_check`) — the
applicability gate and real Flag persistence end-to-end. Live Postgres
(own scratch DB, same convention as `test_checks_forensics_service.py`)."""

import os
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.agreement.service import (
    existing_internal_agreement_result,
    run_internal_agreement_check,
)
from app.config import get_settings
from app.db import sqlalchemy_url
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.enums import ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_agreementtest"


class ScriptedLLM:
    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)
        self.calls: list[str] = []

    async def complete(self, prompt_type, prompt, *, prompt_version="unversioned", **context):
        self.calls.append(prompt)
        return self._responses.pop(0)


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
async def _clean(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE flag, check_result, check_run, rubric, "
                "manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_check_run(session_factory) -> int:
    async with session_factory() as session:
        instructor = Instructor(email="agreement-test@test.local", display_name="Agreement Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref="test.pdf"
        )
        session.add(manuscript)
        await session.commit()
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        return check_run.id


def _block(text_: str, *, page: int = 1) -> TextBlock:
    return TextBlock(text=text_, page=page, max_font_size=11.0, bold_ratio=0.0)


def _extraction(blocks: list[TextBlock]) -> ExtractionResult:
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=blocks,
        images=[],
    )


async def test_manuscript_with_no_statements_is_not_applicable(session_factory):
    """Ticket-family AC (F4/F5/F6 share this convention): nothing to check
    -> N/A, never a silently-clean pass (charter rule 9)."""
    check_run_id = await _seed_check_run(session_factory)
    extraction = _extraction([_block("This chapter is purely descriptive prose.")])
    llm = ScriptedLLM([])
    async with session_factory() as session:
        result = await run_internal_agreement_check(
            session, llm, check_run_id, extraction, get_settings()
        )
    assert result.outcome == ResultOutcome.not_applicable
    assert result.detail["n_intents"] == 0
    assert result.detail["n_outcomes"] == 0
    assert llm.calls == []


async def test_seeded_contradiction_produces_a_real_flag_row(session_factory):
    """End-to-end: a real seeded intent/outcome contradiction produces a
    real persisted Flag with severity HIGH, not just an in-memory draft."""
    check_run_id = await _seed_check_run(session_factory)
    extraction = _extraction(
        [
            _block("Objectives"),
            _block("1. To build a biometric login module for the mobile app."),
            _block("Summary of Findings"),
            _block(
                "1. The biometric login module was not implemented due to hardware constraints."
            ),
        ]
    )
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "contradictory",
                        "reasoning": "The finding says it was not implemented.",
                    }
                ]
            }
        ]
    )
    async with session_factory() as session:
        result = await run_internal_agreement_check(
            session, llm, check_run_id, extraction, get_settings()
        )
        assert result.outcome == ResultOutcome.passed
        assert result.detail["n_intents"] == 1
        assert result.detail["n_outcomes"] == 1
        assert result.detail["n_flags"] == 1

        flag_query = text("SELECT severity, page_anchor FROM flag WHERE check_result_id = :id")
        rows = (await session.execute(flag_query, {"id": result.id})).all()
    assert len(rows) == 1
    assert rows[0].severity == "high"


async def test_unmatched_intent_produces_a_low_flag_no_llm_call(session_factory):
    """Ticket AC demo seed: a feature claimed in Ch1, absent later -> low
    flag. High similarity_floor forces zero candidates, so no Gemini call
    is spent (quota discipline, verified end-to-end)."""
    check_run_id = await _seed_check_run(session_factory)
    extraction = _extraction(
        [
            _block("Objectives"),
            _block("1. To build a biometric login module for the mobile app."),
            _block("Summary of Findings"),
            _block("1. The dashboard loads within two seconds on average."),
        ]
    )
    llm = ScriptedLLM([])
    settings = get_settings().model_copy(update={"agreement_pairing_similarity_floor": 0.99})
    async with session_factory() as session:
        result = await run_internal_agreement_check(
            session, llm, check_run_id, extraction, settings
        )
        assert result.detail["n_flags"] == 1
        flag_query = text(
            "SELECT severity, detail->>'kind' AS kind FROM flag WHERE check_result_id = :id"
        )
        rows = (await session.execute(flag_query, {"id": result.id})).all()
    assert len(rows) == 1
    assert rows[0].severity == "low"
    assert rows[0].kind == "agreement_unmatched_intent"
    assert llm.calls == []


async def test_idempotent_rerun_does_not_duplicate_flags(session_factory):
    """ENGINEERING §4 contract every stage keeps: a resumed run must not
    re-run (and re-flag) work it already did."""
    check_run_id = await _seed_check_run(session_factory)
    extraction = _extraction([_block("This chapter is purely descriptive prose.")])
    llm = ScriptedLLM([])
    async with session_factory() as session:
        first = await run_internal_agreement_check(
            session, llm, check_run_id, extraction, get_settings()
        )
        existing = await existing_internal_agreement_result(session, check_run_id)
    assert existing is not None
    assert existing.id == first.id
