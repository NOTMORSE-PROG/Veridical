"""V-020 live-DB tests: `get_report` (the `GET /check-runs/{id}/report`
data source, screen 4h) — ownership, the not-finished-yet guard, and the
full per-criterion shape (evidence/anchor/reason all read back from
`check_result.detail`).
"""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.errors import ConflictError, NotFoundError
from app.models.enums import CheckKind, CheckRunStatus, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun
from app.report.service import aggregate_and_score, get_report

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_reportgettest"


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
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE readiness_report, check_result, check_run, criterion, "
                "rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_done_run(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="report-get@demo.local", display_name="Report Get Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id,
            group_label="G-11",
            file_ref="x.pdf",
            original_filename="G-11-Thesis.pdf",
        )
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        criterion = Criterion(
            rubric_id=rubric.id,
            type="structural",
            text="Has an abstract",
            evidence=None,
            weight=Decimal("100"),
            position=0,
        )
        session.add(criterion)
        await session.commit()
        await session.refresh(criterion)

        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        session.add(
            CheckResult(
                check_run_id=check_run.id,
                criterion_id=criterion.id,
                kind=CheckKind.structural,
                outcome=ResultOutcome.passed,
                score=100.0,
                detail={
                    "rule_id": "required_section_present",
                    "anchor": "page 2",
                    "target": "abstract",
                },
            )
        )
        check_run.status = CheckRunStatus.done
        await session.commit()
        await aggregate_and_score(session, check_run.id)
        return instructor.id, check_run.id, criterion.id


async def test_get_report_returns_the_full_shape_for_a_done_run(session_factory):
    instructor_id, check_run_id, criterion_id = await _seed_done_run(session_factory)
    async with session_factory() as session:
        report = await get_report(session, check_run_id, instructor_id)
        assert report.check_run_id == check_run_id
        assert report.manuscript_group_label == "G-11"
        # BUG-022 review (backend review): assert the ACTUAL filename,
        # not a copy of the label — the two are easy to swap silently in
        # the construction site with no existing test to catch it.
        assert report.manuscript_original_filename == "G-11-Thesis.pdf"
        assert report.composite_score == 100.0
        assert len(report.results) == 1
        row = report.results[0]
        assert row.criterion_id == criterion_id
        assert row.outcome == "passed"
        assert row.anchor == "page 2"
        assert row.evidence == []


async def test_old_semantic_suggestions_are_disclosed_without_rewriting_history(session_factory):
    instructor_id, run_id, _ = await _seed_done_run(session_factory)
    async with session_factory() as session:
        result = await session.scalar(select(CheckResult).where(CheckResult.check_run_id == run_id))
        result.kind = CheckKind.semantic
        result.detail = {**result.detail, "basis": "llm", "prompt_version": "v3"}
        await session.commit()
        assert (
            await get_report(session, run_id, instructor_id)
        ).historical_semantic_support_unverified
        assert result.outcome == ResultOutcome.passed
        result.detail = {**result.detail, "support_check": {"status": "supported"}}
        await session.commit()
        assert (
            await get_report(session, run_id, instructor_id)
        ).historical_semantic_support_unverified
        from app.checks.support import SOURCE_SCOPE_POLICY_VERSION

        result.detail = {
            **result.detail,
            "support_check": {
                "status": "supported",
                "source_scope_policy": SOURCE_SCOPE_POLICY_VERSION,
            },
        }
        await session.commit()
        assert not (
            await get_report(session, run_id, instructor_id)
        ).historical_semantic_support_unverified


async def test_image_coverage_prevents_ready_and_uses_only_saved_safe_snapshot(session_factory):
    from app.checks.forensics.service import run_statistical_forensics_check
    from app.ingest.schemas import (
        ExtractionResult,
        ImageBlock,
        ImageReading,
        SectionTree,
        TextBlock,
    )
    from app.share.service import _to_public_report

    instructor_id, run_id, _ = await _seed_done_run(session_factory)
    extraction = ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=50,
        section_tree=SectionTree(source="none"),
        blocks=[TextBlock(page=1, text="t(28) = 2.45, p = .021.", max_font_size=11, bold_ratio=0)],
        images=[ImageBlock(page=1, bbox=(0, 0, 100, 100))],
        image_reading_version="v1",
        image_readings=[
            ImageReading(image_index=0, status="failed", reason="api_down", attempted=True)
        ],
    )
    async with session_factory() as session:
        assert (await get_report(session, run_id, instructor_id)).status == "ready"
        result = await run_statistical_forensics_check(session, run_id, extraction)
        await aggregate_and_score(session, run_id)
        report = await get_report(session, run_id, instructor_id)
        assert report.status == "needs_review"
        assert report.flag_deduction == 0
        [coverage] = report.integrity_check_status
        assert coverage.n_skipped_api_down == 1
        assert coverage.n_images_unassessed == 1
        assert coverage.assessment_scope == "available_text_and_tables"
        assert coverage.image_coverage_items[0].page == 1
        assert _to_public_report(report).assessment_incomplete is True
        # A stored private field cannot pass through the explicit public projection.
        detail = {
            **result.detail,
            "image_coverage_items": [
                {**result.detail["image_coverage_items"][0], "private_path": "DO-NOT-EXPOSE"},
            ],
        }
        result.detail = detail
        await session.commit()
        extraction.images.clear()
        reopened = await get_report(session, run_id, instructor_id)
        assert reopened.integrity_check_status == report.integrity_check_status
        assert "DO-NOT-EXPOSE" not in reopened.model_dump_json()


async def test_get_report_rejects_a_run_that_has_not_finished(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="unfinished@demo.local", display_name="Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()

        with pytest.raises(ConflictError):
            await get_report(session, check_run.id, instructor.id)


async def test_get_report_rejects_another_instructors_run(session_factory):
    instructor_id, check_run_id, _ = await _seed_done_run(session_factory)
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_report(session, check_run_id, instructor_id + 999)
