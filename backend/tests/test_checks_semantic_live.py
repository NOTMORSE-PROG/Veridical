"""V-017 live tests:
1. `run_semantic_checks` persisting real `check_result` rows against a
   real Postgres (fake-LLM mode — deterministic, zero quota), same
   scratch-DB convention as test_checks_structural_live.py.
2. A REAL Gemini smoke test (gated on `GEMINI_API_KEY`, same convention as
   test_gemini_transport_live.py) grading real criteria against the
   owner's real proposal PDF — the milestone's own quota-risk-retirement
   evidence (V2 focus: "record actual calls + tokens for a full
   manuscript").
"""

import os
import time
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.checks.semantic import build_semantic_batches, run_semantic_checks
from app.config import get_settings
from app.models.enums import CheckKind, ResultOutcome
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Criterion, Rubric
from app.models.run import CheckResult, CheckRun
from tests.test_ingest_pdf import PdfBuilder

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_semantictest"
DEMO_PDF = Path(__file__).resolve().parents[2] / "VERIDICAL-DOCUMENTATION.pdf"


@pytest.fixture(scope="module")
def semantic_scratch_url():
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
def session_factory(semantic_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(semantic_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE check_result, check_run, criterion, rubric, citation, "
                "manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


def _abstract_pdf(tmp_path):
    b = PdfBuilder()
    b.new_page().line("A STUDY OF THINGS", size=16, bold=True)
    b.new_page().line("ABSTRACT", bold=True)
    # Matches app/llm/fixtures/semantic_grading.json's evidence_quotes
    # exactly, so fake-LLM mode's deterministic verdict survives the real
    # containment check end-to-end (not just in the unit test's
    # hand-built context).
    b.line("This is a test sentence used as evidence.")
    return b.save(tmp_path / "abstract.pdf")


async def test_semantic_check_persists_real_results_fake_llm_mode(
    tmp_path, monkeypatch, session_factory
):
    from app.ingest.service import ingest_manuscript, load_raw_store
    from app.llm.fake import FakeLLMClient

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()
    pdf_path = _abstract_pdf(tmp_path)

    async with session_factory() as session:
        instructor = Instructor(
            email=f"semantic-{time.time_ns()}@test.local", display_name="Semantic Test"
        )
        session.add(instructor)
        await session.commit()

        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref=str(pdf_path)
        )
        session.add(manuscript)
        await session.commit()
        await ingest_manuscript(session, manuscript, pdf_path, settings)
        extraction = load_raw_store(settings, manuscript.id)

        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        # BUG-177: the shared `semantic_grading.json` fixture always
        # returns 3 verdicts (indices 0-2) -- a batch of fewer criteria now
        # correctly triggers the new out-of-range rejection (the fixture
        # itself was never validated against a batch it didn't match, and
        # nothing before this ticket checked). Three criteria, none naming
        # a specific section (so all three land in the SAME whole-document
        # batch, not scattered across separate single-criterion batches
        # that would each independently mismatch the fixture) -- same
        # non-section-naming phrasing
        # `test_fake_llm_mode_returns_deterministic_fixture_verdicts`
        # already uses for the identical reason.
        criteria = [
            Criterion(
                rubric_id=rubric.id,
                type="semantic",
                text="The overall purpose of the study is clearly stated",
                evidence=None,
                weight=Decimal("10"),
                position=0,
            ),
            Criterion(
                rubric_id=rubric.id,
                type="semantic",
                text="The overall methodology is sound",
                evidence=None,
                weight=Decimal("10"),
                position=1,
            ),
            Criterion(
                rubric_id=rubric.id,
                type="semantic",
                text="The overall writing is clear throughout",
                evidence=None,
                weight=Decimal("10"),
                position=2,
            ),
        ]
        session.add_all(criteria)
        await session.commit()

        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()

        results = await run_semantic_checks(
            session, check_run.id, criteria, extraction, FakeLLMClient(), settings
        )
        assert len(results) == 3
        assert results[0].outcome == ResultOutcome.passed

    async with session_factory() as verify_session:
        stored = (
            await verify_session.execute(
                select(CheckResult).where(CheckResult.criterion_id == criteria[0].id)
            )
        ).scalar_one()
        assert stored.kind == CheckKind.semantic
        assert stored.detail["basis"] == "llm"
        # Containment spot-check (ticket AC): the quoted text really does
        # exist in the source, at the anchor the pipeline itself derived.
        quote = stored.detail["evidence"][0]["quote"]
        anchor = stored.detail["evidence"][0]["anchor"]
        assert any(quote in b.text for b in extraction.blocks)
        assert anchor == "page 2"  # title page is page 1; ABSTRACT starts page 2


# --- real Gemini smoke test (quota-risk retirement evidence, V2 focus) ----------

gemini_live = pytest.mark.skipif(
    "GEMINI_API_KEY" not in os.environ,
    reason="live smoke: needs a real GEMINI_API_KEY exported in the shell",
)


@gemini_live
async def test_real_gemini_grades_real_proposal_excerpt_and_records_quota_evidence(
    session_factory,
):
    """Not gated on DATABASE_URL alone (module-level `pytestmark`) — also
    needs GEMINI_API_KEY. Real call, real manuscript excerpt (the owner's
    own proposal PDF, D-007 local-only), real containment check. Records
    actual call count (from audit_log) and an approximate token count
    (chars/4 heuristic, noted as approximate — exact usage_metadata isn't
    captured by the transport yet, a follow-up, not this ticket's job)."""
    if not DEMO_PDF.exists():
        pytest.skip("owner's proposal PDF is local-only (D-007)")

    from app.ingest.pdf import extract_document
    from app.llm.client import GeminiLLMClient
    from app.llm.queue import LLMQueue
    from app.llm.transport import GeminiTransport
    from app.models.audit import AuditLog

    settings = get_settings()
    extraction = extract_document(str(DEMO_PDF), settings)

    async with session_factory() as session:
        instructor = Instructor(
            email=f"gemini-semantic-{time.time_ns()}@test.local", display_name="Live Semantic Test"
        )
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref=str(DEMO_PDF)
        )
        session.add(manuscript)
        await session.commit()
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add(rubric)
        await session.commit()
        criteria = [
            Criterion(
                rubric_id=rubric.id,
                type="semantic",
                text=text_,
                evidence=None,
                weight=Decimal("10"),
                position=i,
            )
            for i, text_ in enumerate(
                [
                    "Chapter 1 clearly states the project's objectives",
                    "Chapter 1 clearly explains why the problem matters",
                ]
            )
        ]
        session.add_all(criteria)
        await session.commit()
        check_run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id)
        session.add(check_run)
        await session.commit()

        transport = GeminiTransport(
            api_key=settings.gemini_api_key, timeout_seconds=settings.gemini_request_timeout_seconds
        )
        queue = LLMQueue(
            transport=transport,
            session_factory=session_factory,
            model=settings.gemini_model,
            temperature=settings.gemini_temperature,
            rpm=settings.llm_rpm,
            daily_quota=settings.llm_daily_quota,
            max_retries=settings.llm_max_retries,
            retry_base_seconds=settings.llm_retry_base_seconds,
            reset_timezone=settings.llm_quota_reset_timezone,
        )
        llm = GeminiLLMClient(queue)

        results = await run_semantic_checks(
            session, check_run.id, criteria, extraction, llm, settings
        )

        for result in results:
            assert result.outcome in (ResultOutcome.passed, ResultOutcome.failed)
            if result.detail.get("basis") == "llm":
                for ev in result.detail["evidence"]:
                    assert ev["quote"]  # non-blank: containment already verified before persist

        call_rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.check_run_id == check_run.id, AuditLog.event_type == "llm_call"
                    )
                )
            )
            .scalars()
            .all()
        )
        # audit_log's payload only carries the RESPONSE (queue.py never logs
        # the prompt itself) — rebuild the actual prompt(s) sent via the same
        # deterministic batching to get a real (if approximate) token count.
        from app.checks.semantic import _build_prompt

        batches, _ = build_semantic_batches(criteria, extraction)
        prompt_chars = sum(len(_build_prompt(batch, bc)) for batch, bc in batches)
        response_chars = sum(len(str(row.payload.get("response"))) for row in call_rows)
        approx_tokens = (prompt_chars + response_chars) // 4
        print(
            f"\n[V-017 quota evidence] real Gemini grading of 2 semantic criteria "
            f"(batched into {len(batches)} call(s) via the whole-Chapter-1 context, "
            f"{prompt_chars} prompt chars): {len(call_rows)} real call(s) made, "
            f"~{approx_tokens} tokens (chars/4 approximation; exact usage_metadata "
            f"isn't captured by the transport yet — noted as a follow-up)."
        )
        assert len(call_rows) == len(batches)
