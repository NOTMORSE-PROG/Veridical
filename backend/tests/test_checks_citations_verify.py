"""V-029: existence + retraction checks — the seeded-test-set AC from the
ticket (real DOI ✓ / typo'd DOI → unverifiable / known-retracted → high
flag / real book → existence-only, no false flag), wording-discipline
(charter rule 3), and api_down-vs-not-found distinction. Live Postgres
(own scratch DB, citation_cache needs a real row to check/write).
"""

import os

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.citations.verify import (
    CORRECTED_WORDING,
    RETRACTED_WORDING,
    UNVERIFIABLE_API_DOWN_WORDING,
    UNVERIFIABLE_NOT_FOUND_WORDING,
    CitationVerdict,
    VerdictKind,
    _verdict_flag_draft,
    run_citation_integrity_check,
    verify_citation,
)
from app.config import get_settings
from app.db import sqlalchemy_url
from app.external.schemas import VerificationResult
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.citation import Citation
from app.models.enums import CitationParseStatus, FlagSeverity
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_verifytest"


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
                "TRUNCATE citation_cache, flag, check_result, check_run, rubric, "
                "manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_check_run(session_factory) -> int:
    async with session_factory() as session:
        instructor = Instructor(email="verify-test@test.local", display_name="Verify Test")
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


def _citation(
    order_index=0, *, doi=None, isbn=None, title=None, authors=None, year=None
) -> Citation:
    return Citation(
        manuscript_id=1,
        order_index=order_index,
        raw_text=f"Some Author. ({year or 2024}). {title or 'A Title'}.",
        authors=authors or ["Some Author"],
        year=year or 2024,
        title=title,
        doi=doi,
        isbn=isbn,
        parse_status=CitationParseStatus.parsed,
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def test_real_doi_confirmed_produces_no_flag(session_factory):
    def handler(request):
        assert "crossref.org" in str(request.url)
        return httpx.Response(200, json={"message": {"DOI": "10.1/x", "title": ["Real Paper"]}})

    citation = _citation(doi="10.1/x")
    async with session_factory() as session, _client(handler) as client:
        verdict = await verify_citation(session, client, citation, settings=get_settings())
    assert verdict.kind == VerdictKind.existence_confirmed
    assert _verdict_flag_draft(citation, verdict) is None


async def test_typo_doi_is_unverifiable_not_retried_via_title(session_factory):
    """Ticket AC: typo'd DOI → unverifiable. Must NOT fall back to a title
    search that could match an unrelated real paper (module docstring)."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404)

    citation = _citation(doi="10.9999/typo-doi", title="Some Real Paper Title")
    async with session_factory() as session, _client(handler) as client:
        verdict = await verify_citation(session, client, citation, settings=get_settings())
    assert verdict.kind == VerdictKind.not_found
    assert len(calls) == 1  # only the DOI lookup — no title-search fallback
    draft = _verdict_flag_draft(citation, verdict)
    assert draft.severity == FlagSeverity.low
    assert draft.detail["reason"] == UNVERIFIABLE_NOT_FOUND_WORDING


async def test_known_retracted_doi_is_high_severity(session_factory):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.1/retracted",
                    "title": ["RETRACTED: Bad Paper"],
                    "update-to": [
                        {"type": "retraction", "label": "Retraction", "source": "publisher"}
                    ],
                }
            },
        )

    citation = _citation(doi="10.1/retracted")
    async with session_factory() as session, _client(handler) as client:
        verdict = await verify_citation(session, client, citation, settings=get_settings())
    assert verdict.kind == VerdictKind.retracted
    draft = _verdict_flag_draft(citation, verdict)
    assert draft.severity == FlagSeverity.high
    expected = RETRACTED_WORDING.format(detail="Retraction (source: publisher)")
    assert draft.detail["reason"] == expected


async def test_correction_is_low_severity_not_high():
    """Ticket edge case: corrections/errata ≠ retractions."""
    citation = _citation(doi="10.1/corrected")
    verdict = CitationVerdict(
        VerdictKind.corrected,
        VerificationResult(found=True, provider="crossref", title="Fine Paper", is_correction=True),
    )
    draft = _verdict_flag_draft(citation, verdict)
    assert draft.severity == FlagSeverity.low
    assert draft.detail["reason"] == CORRECTED_WORDING.format(detail="correction/erratum on record")


async def test_real_book_existence_confirmed_no_false_flag(session_factory):
    """Ticket AC: real book → existence-only note (passes, no false flag)."""

    def handler(request):
        if "openlibrary.org" in str(request.url):
            return httpx.Response(200, json={"key": "/books/OL1M", "title": "A Real Book"})
        return httpx.Response(404)

    citation = _citation(isbn="9780134685991", title="A Real Book")
    async with session_factory() as session, _client(handler) as client:
        verdict = await verify_citation(session, client, citation, settings=get_settings())
    assert verdict.kind == VerdictKind.existence_confirmed
    assert verdict.result.content_checkable is False
    assert _verdict_flag_draft(citation, verdict) is None


async def test_api_down_distinct_from_not_found(session_factory):
    def handler(request):
        return httpx.Response(500)

    settings = get_settings().model_copy(update={"external_max_retries": 0})
    citation = _citation(doi="10.1/flaky")
    async with session_factory() as session, _client(handler) as client:
        verdict = await verify_citation(session, client, citation, settings=settings)
    assert verdict.kind == VerdictKind.api_down
    draft = _verdict_flag_draft(citation, verdict)
    assert draft.detail["reason"] == UNVERIFIABLE_API_DOWN_WORDING
    assert draft.detail["reason"] != UNVERIFIABLE_NOT_FOUND_WORDING  # never conflated


async def test_ph_local_source_no_doi_unverifiable_low_severity(session_factory):
    """Ticket AC: a source without a DOI that no provider indexes (typical
    of PH local grey literature) → unverifiable + manual-review, low
    severity — never treated as suspicious just for being unindexed."""

    def handler(request):
        return httpx.Response(404)

    citation = _citation(title="CMO No. 15 Series 2019, Commission on Higher Education")
    async with session_factory() as session, _client(handler) as client:
        verdict = await verify_citation(session, client, citation, settings=get_settings())
    assert verdict.kind == VerdictKind.not_found
    draft = _verdict_flag_draft(citation, verdict)
    assert draft.severity == FlagSeverity.low


def test_wording_never_uses_accusatory_language():
    """Charter rule 3, string-level: 'unverifiable', never 'fake'."""
    for wording in (
        RETRACTED_WORDING,
        CORRECTED_WORDING,
        UNVERIFIABLE_NOT_FOUND_WORDING,
        UNVERIFIABLE_API_DOWN_WORDING,
    ):
        lowered = wording.lower()
        assert "fake" not in lowered
        assert "fabricat" not in lowered
        assert "dishonest" not in lowered
        assert "cheat" not in lowered


async def test_run_citation_integrity_check_persists_result_and_flags(session_factory):
    """End-to-end: orphan (V-027) + retraction (V-029) both land as real
    Flag rows on one shared check_result."""
    extraction = ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=100,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[
            TextBlock(
                page=1,
                text="This claim cites a ghost source (Nakamura, 2021) directly.",
                max_font_size=11.0,
                bold_ratio=0.0,
            )
        ],
        images=[],
    )
    from app.ingest.patterns import load_patterns

    patterns = load_patterns(get_settings().ingest_patterns_file)
    citations = [_citation(0, doi="10.1/retracted-real")]
    check_run_id = await _seed_check_run(session_factory)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.1/retracted-real",
                    "title": ["RETRACTED: X"],
                    "update-to": [
                        {"type": "retraction", "label": "Retraction", "source": "publisher"}
                    ],
                }
            },
        )

    async with session_factory() as session, _client(handler) as client:
        result = await run_citation_integrity_check(
            session,
            client,
            check_run_id=check_run_id,
            citations=citations,
            extraction=extraction,
            patterns=patterns,
            settings=get_settings(),
        )
        assert result.detail["n_orphans"] == 1  # "Nakamura, 2021" matches no reference
        assert result.detail["n_uncited"] == 1  # the one reference is never cited in-text
        assert result.detail["n_flags"] == 3  # orphan + uncited + retraction

        flag_query = text("SELECT severity FROM flag WHERE check_result_id = :id")
        flags = (await session.execute(flag_query, {"id": result.id})).scalars().all()
        assert sorted(flags) == ["high", "low", "low"]
