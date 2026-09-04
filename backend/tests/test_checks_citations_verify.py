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
                "TRUNCATE audit_log, citation_cache, flag, check_result, check_run, rubric, "
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
    """CrossRef confirms existence; since it has no abstract for this DOI,
    verify_citation supplements with an S2 lookup (module docstring: most
    real DOI-backed citations have no CrossRef abstract at all)."""

    def handler(request):
        if "crossref.org" in str(request.url):
            return httpx.Response(200, json={"message": {"DOI": "10.1/x", "title": ["Real Paper"]}})
        assert "semanticscholar.org" in str(request.url)
        return httpx.Response(404)  # no S2 record either — abstract stays None, still fine

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
    # BUG-078: a real DOI to key a confirmation on -- the flag is confirmable.
    assert draft.detail["key_kind"] == "doi"
    assert draft.detail["key_value"] == "10.9999/typo-doi"


async def test_not_found_with_no_identifier_has_nothing_confirmable(session_factory):
    """BUG-078: a citation with no DOI/ISBN/title at all (e.g. a
    parse_failed entry) can't be keyed to any citation_cache row, so its
    flag must carry no key_kind/key_value -- nothing for the instructor to
    confirm, and the frontend/`confirm_citation_source` must be able to
    tell this case apart from a genuinely confirmable one."""
    citation = _citation(doi=None, isbn=None, title=None)
    async with session_factory() as session, _client(lambda r: httpx.Response(404)) as client:
        verdict = await verify_citation(session, client, citation, settings=get_settings())
    assert verdict.kind == VerdictKind.not_found
    draft = _verdict_flag_draft(citation, verdict)
    assert "key_kind" not in draft.detail
    assert "key_value" not in draft.detail


async def test_instructor_confirmed_source_produces_no_flag(session_factory):
    """BUG-078/FEATURES.md §9: once a source's citation_cache row is
    marked instructor_confirmed (by `confirm_citation_source`, exercised
    directly here rather than through the flags service to keep this test
    scoped to `verify.py`'s own read side), a LATER verification of the
    identical key (this manuscript's re-run, or a different manuscript
    citing the same DOI) must resolve to `instructor_confirmed` and
    produce no flag -- the whole point of the feature."""
    from app.external.cache import confirm_citation_source

    citation = _citation(doi="10.9999/confirmed-doi")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404)

    async with session_factory() as session, _client(handler) as client:
        first = await verify_citation(session, client, citation, settings=get_settings())
    assert first.kind == VerdictKind.not_found

    async with session_factory() as session:
        confirmed = await confirm_citation_source(
            session, key_kind="doi", key_value="10.9999/confirmed-doi"
        )
    assert confirmed is True

    # Second verification hits the cache (not a fresh provider call) and
    # must now resolve to instructor_confirmed, not not_found again.
    async with session_factory() as session, _client(handler) as client:
        second = await verify_citation(session, client, citation, settings=get_settings())
    assert second.kind == VerdictKind.instructor_confirmed
    assert _verdict_flag_draft(citation, second) is None
    assert len(calls) == 1  # only the first lookup hit the network; the second was a cache hit


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


async def test_crossref_missing_abstract_supplemented_from_s2(session_factory):
    """V-030's own precondition: claim-support needs an abstract, and
    CrossRef often doesn't have one — verify_citation must fill it in from
    S2 without disturbing the CrossRef-sourced existence verdict."""

    def handler(request):
        if "crossref.org" in str(request.url):
            return httpx.Response(200, json={"message": {"DOI": "10.1/y", "title": ["A Paper"]}})
        assert "semanticscholar.org" in str(request.url)
        return httpx.Response(200, json={"title": "A Paper", "abstract": "We report a finding."})

    citation = _citation(doi="10.1/y")
    async with session_factory() as session, _client(handler) as client:
        verdict = await verify_citation(session, client, citation, settings=get_settings())
    assert verdict.kind == VerdictKind.existence_confirmed
    assert verdict.result.abstract == "We report a finding."
    assert verdict.result.provider == "crossref"  # the ORIGINAL provider, not overwritten


async def test_claim_support_flag_created_end_to_end(session_factory):
    """The full V-027+V-029+V-030 assembly: a DOI-confirmed citation with
    a supplemented abstract and a linked in-text claim produces a real
    claim-support Flag when the LLM judges a mismatch."""
    extraction = ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=100,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[
            TextBlock(
                page=1,
                text="Prior work found no significant effect (Reyes, 2023).",
                max_font_size=11.0,
                bold_ratio=0.0,
            )
        ],
        images=[],
    )
    from app.ingest.patterns import load_patterns

    patterns = load_patterns(get_settings().ingest_patterns_file)
    citation = _citation(0, doi="10.1/reyes", title="A Study", authors=["Reyes, J."], year=2023)
    check_run_id = await _seed_check_run(session_factory)

    def handler(request):
        if "crossref.org" in str(request.url):
            return httpx.Response(
                200, json={"message": {"DOI": "10.1/reyes", "title": ["A Study"]}}
            )
        assert "semanticscholar.org" in str(request.url)
        return httpx.Response(
            200, json={"title": "A Study", "abstract": "We found a significant positive effect."}
        )

    class ScriptedLLM:
        async def complete(self, prompt_type, prompt, *, prompt_version="unversioned", **context):
            return {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "possibly_unsupported",
                        "reasoning": "The abstract reports an effect; the claim says none.",
                        "abstract_excerpt": "a significant positive effect",
                    }
                ]
            }

    async with session_factory() as session, _client(handler) as client:
        result = await run_citation_integrity_check(
            session,
            client,
            check_run_id=check_run_id,
            citations=[citation],
            extraction=extraction,
            patterns=patterns,
            settings=get_settings(),
            llm=ScriptedLLM(),
        )
        assert result.detail["n_claim_support_checked"] == 1
        assert result.detail["n_claim_support_skipped_quota"] == 0

        flag_query = text("SELECT severity, evidence_excerpt FROM flag WHERE check_result_id = :id")
        rows = (await session.execute(flag_query, {"id": result.id})).all()
        assert len(rows) == 1
        assert rows[0].severity == "med"
        assert "no significant effect" in rows[0].evidence_excerpt


async def test_a_run_with_skipped_claim_support_pairs_does_not_report_passed(session_factory):
    """BUG-073's own regression test, F5's side: nothing previously read
    `n_claim_support_skipped_quota`, and the check reported `passed`
    unconditionally regardless of how many claim-support pairs it
    actually judged. A quota-exhausted claim-support batch must surface
    as a non-`passed` outcome, honestly distinguishing "didn't fully run"
    from "ran and found nothing.\""""
    from app.errors import QuotaExhaustedError
    from app.models.enums import ResultOutcome as RO

    extraction = ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=100,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[
            TextBlock(
                page=1,
                text="Prior work found no significant effect (Reyes, 2023).",
                max_font_size=11.0,
                bold_ratio=0.0,
            )
        ],
        images=[],
    )
    from app.ingest.patterns import load_patterns

    patterns = load_patterns(get_settings().ingest_patterns_file)
    citation = _citation(0, doi="10.1/reyes", title="A Study", authors=["Reyes, J."], year=2023)
    check_run_id = await _seed_check_run(session_factory)

    def handler(request):
        if "crossref.org" in str(request.url):
            return httpx.Response(
                200, json={"message": {"DOI": "10.1/reyes", "title": ["A Study"]}}
            )
        assert "semanticscholar.org" in str(request.url)
        return httpx.Response(
            200, json={"title": "A Study", "abstract": "We found a significant positive effect."}
        )

    class QuotaExhaustedLLM:
        async def complete(self, prompt_type, prompt, *, prompt_version="unversioned", **context):
            raise QuotaExhaustedError("daily budget spent")

    async with session_factory() as session, _client(handler) as client:
        result = await run_citation_integrity_check(
            session,
            client,
            check_run_id=check_run_id,
            citations=[citation],
            extraction=extraction,
            patterns=patterns,
            settings=get_settings(),
            llm=QuotaExhaustedLLM(),
        )
    assert result.outcome == RO.quota_exhausted
    assert result.outcome != RO.passed
    assert result.detail["n_claim_support_pairs_total"] == 1
    assert result.detail["n_claim_support_checked"] == 0
    assert result.detail["n_claim_support_skipped_quota"] == 1
    assert result.detail["n_claim_support_skipped_api_down"] == 0
    assert result.detail["n_claim_support_skipped_parse_failure"] == 0


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

        audit_row = (
            await session.execute(
                text(
                    "SELECT payload FROM audit_log WHERE check_run_id = :id "
                    "AND event_type = 'citation_integrity_check_computed'"
                ),
                {"id": check_run_id},
            )
        ).first()
    # BUG-151: F5 wrote nothing to the audit log before this fix.
    assert audit_row is not None
    assert audit_row.payload["n_flags"] == 3
    assert (
        audit_row.payload["thresholds"]["cache_stale_days"]
        == get_settings().citation_cache_stale_days
    )
