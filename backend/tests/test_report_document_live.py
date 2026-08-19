"""V-065 AC1/2/7 live-DB tests: `get_manuscript_viewer` / `get_manuscript_file_path`
(the `GET /check-runs/{id}/document` + `.../document/file` data sources).
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.errors import ConflictError, GoneError, NotFoundError
from app.ingest.pdf import extract_document
from app.models.enums import CheckKind, CheckRunStatus, FlagSeverity, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckResult, CheckRun, Flag
from app.report.service import get_manuscript_file_path, get_manuscript_viewer
from tests.test_ingest_pdf import PdfBuilder

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_reportdocumenttest"


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
                "TRUNCATE flag, readiness_report, check_result, check_run, criterion, "
                "rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


def _one_page_pdf(tmp_path: Path) -> Path:
    b = PdfBuilder()
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True)
    b.line("Before a student can defend their capstone the format must be checked.")
    return b.save(tmp_path / "doc.pdf")


async def _seed_manuscript(
    session_factory, tmp_path: Path, *, file_ref: str | None = None, purged: bool = False
) -> tuple[int, int, int]:
    """Returns (instructor_id, check_run_id, manuscript_id)."""
    pdf_path = file_ref or str(_one_page_pdf(tmp_path))
    extraction = extract_document(pdf_path, get_settings())

    async with session_factory() as session:
        instructor = Instructor(email="doc-viewer@demo.local", display_name="Doc Viewer Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id,
            group_label="G-1",
            file_ref=pdf_path,
            original_filename="doc.pdf",
            section_tree=extraction.section_tree.model_dump(),
            purged_at=datetime.now(UTC) if purged else None,
        )
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()

        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        check_run.status = CheckRunStatus.done
        await session.commit()
        await session.refresh(check_run)
        return instructor.id, check_run.id, manuscript.id


async def _add_flag(session_factory, check_run_id: int, *, page_anchor: str, evidence: str) -> int:
    async with session_factory() as session:
        result = CheckResult(
            check_run_id=check_run_id,
            criterion_id=None,
            kind=CheckKind.citation_integrity,
            outcome=ResultOutcome.escalated,
            score=None,
            detail={},
        )
        session.add(result)
        await session.commit()
        await session.refresh(result)

        flag = Flag(
            check_result_id=result.id,
            severity=FlagSeverity.low,
            evidence_excerpt=evidence,
            page_anchor=page_anchor,
            overridden=False,
        )
        session.add(flag)
        await session.commit()
        await session.refresh(flag)
        return flag.id


async def test_available_pdf_returns_page_bbox_region(session_factory, tmp_path):
    instructor_id, check_run_id, _ = await _seed_manuscript(session_factory, tmp_path)
    flag_id = await _add_flag(
        session_factory,
        check_run_id,
        page_anchor="p. 1",
        evidence="Before a student can defend their capstone the format must be checked.",
    )
    async with session_factory() as session:
        viewer = await get_manuscript_viewer(session, check_run_id, instructor_id)

    assert viewer.available is True
    assert viewer.source_format == "pdf"
    assert viewer.page_count == 1
    assert len(viewer.regions) == 1
    assert viewer.regions[0].flag_id == flag_id
    assert viewer.regions[0].kind == "page_bbox"
    assert viewer.regions[0].bbox is not None


async def test_purged_manuscript_every_flag_is_unavailable(session_factory, tmp_path):
    instructor_id, check_run_id, _ = await _seed_manuscript(session_factory, tmp_path, purged=True)
    flag_id = await _add_flag(
        session_factory, check_run_id, page_anchor="p. 1", evidence="anything"
    )
    async with session_factory() as session:
        viewer = await get_manuscript_viewer(session, check_run_id, instructor_id)

    assert viewer.available is False
    assert viewer.purged_at is not None
    assert viewer.unavailable_reason is not None
    assert len(viewer.regions) == 1
    assert viewer.regions[0].flag_id == flag_id
    assert viewer.regions[0].kind == "unavailable"


async def test_file_path_raises_gone_for_purged_manuscript(session_factory, tmp_path):
    instructor_id, check_run_id, _ = await _seed_manuscript(session_factory, tmp_path, purged=True)
    async with session_factory() as session:
        with pytest.raises(GoneError):
            await get_manuscript_file_path(session, check_run_id, instructor_id)


async def test_file_path_returns_path_for_available_pdf(session_factory, tmp_path):
    instructor_id, check_run_id, _ = await _seed_manuscript(session_factory, tmp_path)
    async with session_factory() as session:
        path = await get_manuscript_file_path(session, check_run_id, instructor_id)
    assert path.suffix == ".pdf"
    assert path.exists()


async def test_non_pdf_file_ref_rejected_by_file_endpoint(session_factory, tmp_path):
    docx_path = tmp_path / "doc.docx"
    docx_path.write_bytes(b"not a real docx, just needs a name")
    # A real DOCX extraction isn't needed here -- the file-path guard only
    # inspects the extension, and viewer metadata isn't under test in this
    # case.
    async with session_factory() as session:
        instructor = Instructor(email="docx-viewer@demo.local", display_name="Docx Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id,
            group_label="G-2",
            file_ref=str(docx_path),
            original_filename="doc.docx",
        )
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        check_run.status = CheckRunStatus.done
        await session.commit()
        await session.refresh(check_run)
        instructor_id, check_run_id = instructor.id, check_run.id

    async with session_factory() as session:
        with pytest.raises(ConflictError):
            await get_manuscript_file_path(session, check_run_id, instructor_id)


async def test_ownership_check_matches_other_report_endpoints(session_factory, tmp_path):
    instructor_id, check_run_id, _ = await _seed_manuscript(session_factory, tmp_path)
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_manuscript_viewer(session, check_run_id, instructor_id + 999)
