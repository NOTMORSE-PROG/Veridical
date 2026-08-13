"""V-041 live-DB tests: re-running a manuscript against a new rubric
version. Cache discipline (AC1, proven against the REAL citation
verification function, not a mock of the cache layer), old-report
immutability (AC3), the version-comparison line, and the quota estimate
(D-001) shown before confirming a bulk re-run.
"""

import os
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.citations.verify import verify_citation
from app.config import get_settings
from app.db import sqlalchemy_url
from app.errors import NotFoundError
from app.models.audit import AuditLog
from app.models.citation import Citation
from app.models.enums import CheckRunStatus, CitationParseStatus
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun, ReadinessReport
from app.pipeline.service import estimate_rerun_calls
from app.report.service import get_report

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_rerun041test"


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
        await session.execute(
            text(
                "TRUNCATE citation_cache, audit_log, readiness_report, check_result, "
                "check_run, criterion, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def test_ac1_a_reruns_citation_check_hits_the_cache_not_the_network(session_factory):
    """The exact real function the pipeline calls for citation checks
    (`verify_citation`) must serve a second call for the SAME citation
    entirely from `citation_cache` -- proven by making the second call's
    HTTP handler fail the test if it's ever invoked, not by inspecting
    the cache table directly (which would only prove the cache CAN hold
    a value, not that the real check path actually consults it first)."""
    citation = Citation(
        manuscript_id=1,
        order_index=0,
        raw_text="Some Author. (2024). A Title.",
        authors=["Some Author"],
        year=2024,
        title="A Title",
        doi="10.1/rerun-x",
        parse_status=CitationParseStatus.parsed,
    )

    def real_network_handler(request):
        if "crossref.org" in str(request.url):
            return httpx.Response(
                200, json={"message": {"DOI": "10.1/rerun-x", "title": ["Real Paper"]}}
            )
        return httpx.Response(404)

    def must_not_be_called(request):
        raise AssertionError(
            f"A re-run's citation check hit the network ({request.url}) instead of the cache."
        )

    async with session_factory() as session, _client(real_network_handler) as client:
        first = await verify_citation(session, client, citation, settings=get_settings())
    assert first.kind.value == "existence_confirmed"

    # The "re-run" -- a fresh verify_citation call for the identical
    # citation, simulating F5 re-executing under a new check_run. Any
    # network call at all fails this test.
    async with session_factory() as session, _client(must_not_be_called) as client:
        second = await verify_citation(session, client, citation, settings=get_settings())
    assert second.kind.value == "existence_confirmed"


async def _seed_done_run(session, manuscript_id, rubric_id, *, status_label, score):
    check_run = CheckRun(
        manuscript_id=manuscript_id, rubric_id=rubric_id, status=CheckRunStatus.done
    )
    session.add(check_run)
    await session.commit()
    session.add(
        ReadinessReport(check_run_id=check_run.id, status=status_label, composite_score=score)
    )
    await session.commit()
    return check_run


async def test_ac3_the_old_reports_report_is_unchanged_after_a_rerun_exists(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="rerun@demo.local", display_name="Rerun Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric_v1 = Rubric(
            instructor_id=instructor.id, title="Format", source_file="r.pdf", version=1
        )
        session.add_all([manuscript, rubric_v1])
        await session.commit()

        old_run = await _seed_done_run(
            session,
            manuscript.id,
            rubric_v1.id,
            status_label="conditionally_ready",
            score=Decimal("70"),
        )
        old_report_before = await get_report(session, old_run.id, instructor.id)

        # The "re-run": a second rubric version, a second check_run for
        # the SAME manuscript.
        rubric_v2 = Rubric(
            instructor_id=instructor.id,
            title="Format",
            source_file="r.pdf",
            version=2,
            is_active=True,
        )
        session.add(rubric_v2)
        await session.commit()
        await _seed_done_run(
            session, manuscript.id, rubric_v2.id, status_label="ready", score=Decimal("95")
        )

        old_report_after = await get_report(session, old_run.id, instructor.id)
        assert old_report_after.status == old_report_before.status == "conditionally_ready"
        assert old_report_after.composite_score == old_report_before.composite_score == 70.0


async def test_version_comparison_line_points_at_the_immediately_prior_run(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="compare@demo.local", display_name="Compare Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric_v1 = Rubric(
            instructor_id=instructor.id, title="Format", source_file="r.pdf", version=1
        )
        session.add_all([manuscript, rubric_v1])
        await session.commit()
        await _seed_done_run(
            session,
            manuscript.id,
            rubric_v1.id,
            status_label="conditionally_ready",
            score=Decimal("70"),
        )

        # SAME family, next version -- explicit, not left to the column's
        # own server_default (which generates a random UUID per row and
        # would silently defeat this test's own point: backend-critic
        # found the original version of this test never actually modeled
        # "next version of the same format" for exactly this reason).
        rubric_v2 = Rubric(
            instructor_id=instructor.id,
            title="Format",
            source_file="r.pdf",
            version=2,
            is_active=True,
            rubric_family_id=rubric_v1.rubric_family_id,
        )
        session.add(rubric_v2)
        await session.commit()
        new_run = await _seed_done_run(
            session, manuscript.id, rubric_v2.id, status_label="ready", score=Decimal("95")
        )

        new_report = await get_report(session, new_run.id, instructor.id)
        assert new_report.previous_status == "conditionally_ready"
        assert new_report.previous_composite_score == 70.0


async def test_comparison_never_crosses_an_unrelated_rubric_family(session_factory):
    """`backend-critic` finding, live-demonstrated: without a family
    filter, a manuscript checked against an UNRELATED format in between
    two real versions of the SAME format would win the comparison,
    manufacturing a "v1 -> v2" story that never happened (two different
    formats' verdicts being compared, not two versions of one)."""
    async with session_factory() as session:
        instructor = Instructor(email="crossfam@demo.local", display_name="Cross Family Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        cs_format_v1 = Rubric(
            instructor_id=instructor.id, title="CS Format", source_file="cs.pdf", version=1
        )
        it_format_v1 = Rubric(
            instructor_id=instructor.id, title="IT Format", source_file="it.pdf", version=1
        )
        session.add_all([manuscript, cs_format_v1, it_format_v1])
        await session.commit()

        # Real prior version of the SAME family the new run belongs to.
        await _seed_done_run(
            session, manuscript.id, cs_format_v1.id, status_label="ready", score=Decimal("95")
        )
        # An unrelated format, checked LATER (so it's more recent by
        # created_at) -- must NOT win the comparison.
        await _seed_done_run(
            session, manuscript.id, it_format_v1.id, status_label="not_ready", score=Decimal("40")
        )

        cs_format_v2 = Rubric(
            instructor_id=instructor.id,
            title="CS Format",
            source_file="cs.pdf",
            version=2,
            is_active=True,
            rubric_family_id=cs_format_v1.rubric_family_id,
        )
        session.add(cs_format_v2)
        await session.commit()
        new_run = await _seed_done_run(
            session,
            manuscript.id,
            cs_format_v2.id,
            status_label="conditionally_ready",
            score=Decimal("75"),
        )

        new_report = await get_report(session, new_run.id, instructor.id)
        # The real prior CS Format version, not the more-recent-but-
        # unrelated IT Format run.
        assert new_report.previous_status == "ready"
        assert new_report.previous_composite_score == 95.0


async def test_a_manuscripts_first_ever_report_has_no_previous_comparison(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="first@demo.local", display_name="First Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        rubric = Rubric(
            instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
        )
        session.add_all([manuscript, rubric])
        await session.commit()
        run = await _seed_done_run(
            session, manuscript.id, rubric.id, status_label="ready", score=Decimal("90")
        )

        report = await get_report(session, run.id, instructor.id)
        assert report.previous_status is None
        assert report.previous_composite_score is None


async def test_rerun_estimate_sums_each_manuscripts_own_latest_run_call_count(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="estimate@demo.local", display_name="Estimate Test")
        session.add(instructor)
        await session.commit()
        rubric = Rubric(
            instructor_id=instructor.id, title="Format", source_file="r.pdf", is_active=True
        )
        session.add(rubric)
        await session.commit()

        m1 = Manuscript(instructor_id=instructor.id, group_label="G1", file_ref="x.pdf")
        m2 = Manuscript(instructor_id=instructor.id, group_label="G2", file_ref="y.pdf")
        m3_never_checked = Manuscript(
            instructor_id=instructor.id, group_label="G3", file_ref="z.pdf"
        )
        session.add_all([m1, m2, m3_never_checked])
        await session.commit()

        run1 = CheckRun(manuscript_id=m1.id, rubric_id=rubric.id, status=CheckRunStatus.done)
        run2 = CheckRun(manuscript_id=m2.id, rubric_id=rubric.id, status=CheckRunStatus.done)
        session.add_all([run1, run2])
        await session.commit()

        # 3 real calls for run1, 1 for run2, plus a non-LLM event that
        # must NOT be counted (proves the event_type filter is real).
        session.add_all(
            [
                AuditLog(event_type="llm_call", check_run_id=run1.id, payload={}),
                AuditLog(event_type="llm_call", check_run_id=run1.id, payload={}),
                AuditLog(event_type="llm_cache_hit", check_run_id=run1.id, payload={}),
                AuditLog(event_type="llm_call", check_run_id=run2.id, payload={}),
                AuditLog(event_type="escalation_resolved", check_run_id=run1.id, payload={}),
            ]
        )
        await session.commit()

        out = await estimate_rerun_calls(
            session, instructor.id, [m1.id, m2.id, m3_never_checked.id], rubric.id
        )
        by_id = {item.manuscript_id: item.estimated_calls for item in out.items}
        assert by_id[m1.id] == 3
        assert by_id[m2.id] == 1
        assert by_id[m3_never_checked.id] is None
        assert out.total_estimated_calls == 4  # excludes the None
        assert out.manuscripts_with_no_estimate == 1


async def test_rerun_estimate_is_scoped_to_the_target_rubrics_family(session_factory):
    """Same fix, same reason as the version-comparison line
    (`backend-critic` finding): a manuscript's most recent run under an
    UNRELATED format must not be used as the estimate for a re-run
    against a different family -- honestly `None` instead."""
    async with session_factory() as session:
        instructor = Instructor(email="estimate2@demo.local", display_name="Estimate Test 2")
        session.add(instructor)
        await session.commit()
        cs_format = Rubric(instructor_id=instructor.id, title="CS Format", source_file="cs.pdf")
        it_format = Rubric(instructor_id=instructor.id, title="IT Format", source_file="it.pdf")
        session.add_all([cs_format, it_format])
        await session.commit()

        manuscript = Manuscript(instructor_id=instructor.id, group_label="G", file_ref="x.pdf")
        session.add(manuscript)
        await session.commit()
        # Checked only under IT Format -- no history under CS Format.
        run = CheckRun(
            manuscript_id=manuscript.id, rubric_id=it_format.id, status=CheckRunStatus.done
        )
        session.add(run)
        await session.commit()
        session.add(AuditLog(event_type="llm_call", check_run_id=run.id, payload={}))
        await session.commit()

        # Estimating a re-run against CS Format: the IT Format history
        # must not be used.
        out = await estimate_rerun_calls(session, instructor.id, [manuscript.id], cs_format.id)
        assert out.items[0].estimated_calls is None
        assert out.manuscripts_with_no_estimate == 1

        # Estimating a re-run against IT Format itself: the real history
        # DOES apply.
        out_same_family = await estimate_rerun_calls(
            session, instructor.id, [manuscript.id], it_format.id
        )
        assert out_same_family.items[0].estimated_calls == 1


async def test_rerun_estimate_never_leaks_a_strangers_manuscript(session_factory):
    async with session_factory() as session:
        owner = Instructor(email="owner041@demo.local", display_name="Owner")
        stranger = Instructor(email="stranger041@demo.local", display_name="Stranger")
        session.add_all([owner, stranger])
        await session.commit()
        owner_rubric = Rubric(instructor_id=owner.id, title="Format", source_file="r.pdf")
        session.add(owner_rubric)
        await session.commit()
        their_manuscript = Manuscript(
            instructor_id=stranger.id, group_label="Not Yours", file_ref="x.pdf"
        )
        session.add(their_manuscript)
        await session.commit()

        out = await estimate_rerun_calls(session, owner.id, [their_manuscript.id], owner_rubric.id)
        assert out.items == []
        assert out.total_estimated_calls == 0
        assert out.manuscripts_with_no_estimate == 0


async def test_rerun_estimate_rejects_a_strangers_rubric(session_factory):
    async with session_factory() as session:
        owner = Instructor(email="owner041b@demo.local", display_name="Owner")
        stranger = Instructor(email="stranger041b@demo.local", display_name="Stranger")
        session.add_all([owner, stranger])
        await session.commit()
        their_rubric = Rubric(instructor_id=stranger.id, title="Not Yours", source_file="r.pdf")
        session.add(their_rubric)
        await session.commit()

        with pytest.raises(NotFoundError):
            await estimate_rerun_calls(session, owner.id, [], their_rubric.id)
