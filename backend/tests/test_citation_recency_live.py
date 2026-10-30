"""V-092: the citation recency note inside the real F5 stage, with the
format's saved setting read from the rubric. Live Postgres (own scratch DB)."""

import os
from dataclasses import asdict

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.citations.recency import CitationRecencyFormatSettings
from app.checks.citations.verify import run_citation_integrity_check
from app.config import get_settings
from app.db import sqlalchemy_url
from app.errors import NotFoundError
from app.findings.policy import exclusion_reason
from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.citation import Citation
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun, Flag
from app.rubric.service import get_rubric, set_rubric_family_citation_recency

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)

SCRATCH_DB = "veridical_recencytest"
OLD = "Reyes, J. (2015). Barangay health records. Journal of Local Studies, 4(2), 1-9."
NEW = "Cruz, M. A. (2024). Mobile clinics in Iloilo. Philippine Health Review, 9(1), 3-8."
UNDATED = "Santos, P. (n.d.). City health office profile. Iloilo City Government."


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
                "TRUNCATE audit_log, citation_cache, flag, check_result, check_run, rubric, "
                "manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed(session_factory, citation_recency=None) -> tuple[int, Rubric]:
    async with session_factory() as session:
        instructor = Instructor(email="recency@test.local", display_name="Recency Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="t.pdf")
        rubric = Rubric(
            instructor_id=instructor.id,
            title="Format",
            source_file="r.pdf",
            citation_recency=citation_recency,
        )
        session.add_all([manuscript, rubric])
        await session.commit()
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()
        return check_run.id, rubric


def _manuscript(title_page_date="May 2024"):
    blocks = [
        TextBlock(page=1, text="A Barangay Clinic Locator", max_font_size=16, bold_ratio=1),
        TextBlock(page=1, text=title_page_date, max_font_size=12, bold_ratio=0),
        TextBlock(
            page=2,
            text="Records were kept on paper (Reyes, 2015; Cruz, 2024; Santos, n.d.).",
            max_font_size=11,
            bold_ratio=0,
        ),
        TextBlock(page=3, text="References", max_font_size=14, bold_ratio=1),
    ]
    blocks.extend(
        TextBlock(page=3, text=raw, max_font_size=11, bold_ratio=0) for raw in (OLD, NEW, UNDATED)
    )
    extraction = ExtractionResult(
        page_count=3,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=blocks,
        images=[],
    )
    citations = [
        Citation(manuscript_id=1, **asdict(draft))
        for draft in extract_references(extraction, load_patterns())
    ]
    return extraction, citations


def _not_found(request):
    return httpx.Response(404, json={})


async def _run(session_factory, check_run_id, extraction, citations):
    async with (
        session_factory() as session,
        httpx.AsyncClient(transport=httpx.MockTransport(_not_found)) as client,
    ):
        result = await run_citation_integrity_check(
            session,
            client,
            check_run_id=check_run_id,
            citations=citations,
            extraction=extraction,
            patterns=load_patterns(get_settings().ingest_patterns_file),
            settings=get_settings(),
        )
        flags = (await session.scalars(select(Flag).where(Flag.check_result_id == result.id))).all()
    recency = [f for f in flags if (f.detail or {}).get("kind") == "outside_recency_window"]
    return result, recency


async def test_old_source_gets_a_low_note_against_the_manuscript_year(session_factory):
    extraction, citations = _manuscript()
    assert [c.year for c in citations] == [2015, 2024, None]
    check_run_id, _ = await _seed(session_factory)
    result, recency = await _run(session_factory, check_run_id, extraction, citations)

    summary = result.detail["citation_recency"]
    assert summary["status"] == "assessed"
    assert summary["reference_year"] == 2024
    assert summary["reference_year_basis"] == "manuscript_year"
    assert summary["reference_year_evidence"] == {"text": "May 2024", "anchor": "p. 1"}
    assert summary["oldest_year_in_window"] == 2019
    assert (summary["n_dated"], summary["n_outside_window"]) == (2, 1)
    assert summary["year_not_readable_reasons"] == {"no_date": 1}

    assert len(recency) == 1
    flag = recency[0]
    assert flag.severity.value == "low"
    assert flag.evidence_excerpt == OLD
    assert flag.page_anchor == "p. 3"
    assert flag.detail["year"] == 2015
    assert flag.detail["affects_readiness"] is False
    assert flag.detail["citation_source_locator"]["finding_kind"] == "outside_recency_window"
    assert exclusion_reason("citation_integrity", flag.detail) == "recency_note_not_counted"
    assert result.detail["n_flags"] >= 1


async def test_a_wider_format_window_removes_the_note(session_factory):
    extraction, citations = _manuscript()
    check_run_id, _ = await _seed(session_factory, {"window_years": 10})
    result, recency = await _run(session_factory, check_run_id, extraction, citations)
    assert recency == []
    assert result.detail["citation_recency"]["window_years"] == 10
    assert result.detail["citation_recency"]["setting_sources"]["window_years"] == "format"


async def test_turning_the_note_off_in_the_format_reads_nothing(session_factory):
    extraction, citations = _manuscript()
    check_run_id, _ = await _seed(session_factory, {"enabled": False})
    result, recency = await _run(session_factory, check_run_id, extraction, citations)
    assert recency == []
    assert result.detail["citation_recency"]["status"] == "off"


async def test_invalid_saved_setting_is_reported_unassessed(session_factory):
    extraction, citations = _manuscript()
    check_run_id, _ = await _seed(session_factory, {"window_years": -3})
    result, recency = await _run(session_factory, check_run_id, extraction, citations)
    assert recency == []
    assert result.detail["citation_recency"] == {
        "schema_version": "citation-recency-v1",
        "status": "unassessed",
        "unassessed_reason": "format_setting_invalid",
    }


async def test_check_date_is_used_when_the_title_page_has_no_year(session_factory):
    extraction, citations = _manuscript(title_page_date="Iloilo City")
    check_run_id, _ = await _seed(session_factory)
    result, _ = await _run(session_factory, check_run_id, extraction, citations)
    summary = result.detail["citation_recency"]
    assert summary["reference_year_basis"] == "check_date"
    assert summary["reference_year_fallback_reason"] == "manuscript_year_not_found"
    assert summary["reference_year"] == summary["check_year"]


async def test_family_setting_is_saved_shown_and_carried(session_factory):
    _, rubric = await _seed(session_factory)
    async with session_factory() as session:
        shown = await set_rubric_family_citation_recency(
            session,
            rubric.rubric_family_id,
            CitationRecencyFormatSettings(window_years=3, counts_toward_readiness=True),
            rubric.instructor_id,
        )
        assert shown.window_years == 3
        assert shown.counts_toward_readiness is True
        assert shown.setting_sources["window_years"] == "format"
        assert shown.setting_sources["enabled"] == "default"
        loaded = await get_rubric(session, rubric.id, rubric.instructor_id)
        assert loaded.citation_recency == {"window_years": 3, "counts_toward_readiness": True}
        assert loaded.citation_recency_settings.window_years == 3

        cleared = await set_rubric_family_citation_recency(
            session,
            rubric.rubric_family_id,
            CitationRecencyFormatSettings(),
            rubric.instructor_id,
        )
        assert cleared.setting_sources["window_years"] == "default"
        loaded = await get_rubric(session, rubric.id, rubric.instructor_id)
        assert loaded.citation_recency is None

        # Another instructor cannot change this family.
        with pytest.raises(NotFoundError):
            await set_rubric_family_citation_recency(
                session,
                rubric.rubric_family_id,
                CitationRecencyFormatSettings(window_years=1),
                rubric.instructor_id + 999,
            )


def test_request_body_refuses_unknown_and_out_of_range_values():
    from pydantic import ValidationError

    for body in ({"window_years": 0}, {"window_years": 101}, {"basis": "x"}, {"enabled": "yes"}):
        with pytest.raises(ValidationError):
            CitationRecencyFormatSettings.model_validate(body)
