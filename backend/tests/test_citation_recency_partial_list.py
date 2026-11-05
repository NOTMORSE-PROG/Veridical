"""BUG-602: the recency note runs on a partially assessed reference list.

When material after the reference list (an appendix) cannot be classified,
F5 keeps the entries it proved complete and leaves the rest unassessed. The
recency note only reads each kept entry's year, so it runs on those entries
and says that later material was left out, instead of switching off for the
whole list (production check #43, LensMatch).
"""

from unittest.mock import AsyncMock

import pytest

from app.checks.citations import verify
from app.checks.citations.recency import FINDING_KIND
from app.config import Settings
from app.ingest.patterns import load_patterns
from app.models.run import Flag
from app.report.citation_recency import recency_sentences, recency_summary
from tests.test_citations_partial_source import persisted
from tests.test_reference_primary_partition import source
from tests.test_stabilization_integrity_controls import MemorySession
from tests.test_stabilization_integrity_controls import source as complete_source

RECENCY_ON = Settings(
    _env_file=None,
    citation_recency_enabled=True,
    citation_recency_window_years=1,
    citation_recency_reference_year_basis="check_date",
)


async def run(monkeypatch, extraction, settings=RECENCY_ON):
    session = MemorySession()
    confirmed = verify.CitationVerdict(verify.VerdictKind.existence_confirmed)
    monkeypatch.setattr(verify, "verify_citation", AsyncMock(return_value=confirmed))
    monkeypatch.setattr(verify, "run_claim_support_check", AsyncMock())
    monkeypatch.setattr(verify, "write_audit_event", AsyncMock())
    monkeypatch.setattr("app.checks.source_quality.write_audit_event", AsyncMock())
    result = await verify.run_citation_integrity_check(
        session, None, 1, persisted(extraction), extraction, load_patterns(), settings, object()
    )
    return result, [row for row in session.rows if isinstance(row, Flag)]


@pytest.mark.asyncio
async def test_kept_entries_are_dated_when_an_appendix_follows_the_list(monkeypatch):
    result, flags = await run(monkeypatch, source(secondary=True))
    assert result.detail["reference_membership_status"] == "partial"
    assert result.detail["coverage_reason"] == "reference_membership_partial"
    recency = result.detail["citation_recency"]
    assert recency["status"] == "assessed"
    assert recency["n_references"] == 2
    assert recency["n_dated"] == 2
    assert recency["scope_limit_reason"] == "reference_membership_partial"
    # The appendix's "[1] Cruz, A. (2022)" line is not one of the dated entries.
    assert {row["reference_order_index"] for row in recency["sources"]} == {0, 1}


@pytest.mark.asyncio
async def test_outside_window_notes_are_raised_on_the_kept_entries(monkeypatch):
    result, flags = await run(monkeypatch, source(secondary=True))
    notes = [f for f in flags if f.detail.get("kind") == FINDING_KIND]
    window_start = result.detail["citation_recency"]["oldest_year_in_window"]
    assert len(notes) == sum(year < window_start for year in (2024, 2023))
    assert all("Cruz" not in note.evidence_excerpt for note in notes)


@pytest.mark.asyncio
async def test_complete_list_records_no_scope_limit(monkeypatch):
    extraction = complete_source(
        "Prior work (Reyes, 2024).",
        ["Reyes, J. (2024). First complete source. Journal of Examples, 1(1), 1-2."],
    )
    result, _ = await run(monkeypatch, extraction)
    assert result.detail["reference_membership_status"] == "complete"
    recency = result.detail["citation_recency"]
    assert recency["status"] == "assessed"
    assert recency.get("scope_limit_reason") is None


@pytest.mark.asyncio
async def test_list_that_cannot_be_established_stays_unassessed(monkeypatch):
    result, _ = await run(monkeypatch, source(incomplete=True))
    recency = result.detail["citation_recency"]
    assert recency["status"] == "unassessed"
    assert recency["unassessed_reason"] != "reference_membership_partial"


@pytest.mark.asyncio
async def test_report_and_export_say_later_material_was_left_out(monkeypatch):
    result, _ = await run(monkeypatch, source(secondary=True))
    summary = recency_summary(result.detail)
    assert summary is not None and summary.scope_limit_reason == "reference_membership_partial"
    text = " ".join(recency_sentences(summary))
    assert "Dates were read only from the 2 entries kept as part of the reference list" in text
    assert "left out" in text


@pytest.mark.parametrize("failure", ["none", "grammar", "conflicting", "undated", "empty_list"])
async def test_required_recency_gaps_are_incomplete_in_the_whole_module(
    monkeypatch, tmp_path, failure
):
    from dataclasses import asdict

    from app.checks.citations.support import ClaimSupportPairResult, ClaimSupportResult
    from app.external.schemas import VerificationResult
    from app.ingest.references import parse_reference
    from app.models.citation import Citation
    from app.models.enums import CheckKind, ResultOutcome
    from app.report.module_scope import integrity_modules

    raw = "Reyes, J. (2024). First complete source. Journal of Examples, 1(1), 1-2."
    body = "Prior work (Reyes, 2024)."
    if failure == "conflicting":
        raw = "Reyes, J. (2024). First complete source. Journal of Examples, 2025, 1(1), 1-2."
    elif failure == "undated":
        raw = "Reyes, J. (n.d.). First complete source. Journal of Examples, 1(1), 1-2."
        body = "Prior work (Reyes, n.d.)."
    extraction = complete_source(body, [] if failure == "empty_list" else [raw])
    citations = (
        []
        if failure == "empty_list"
        else [Citation(manuscript_id=1, **asdict(parse_reference(raw, 0)))]
    )
    session = MemorySession()
    monkeypatch.setattr(
        verify,
        "verify_citation",
        AsyncMock(
            return_value=verify.CitationVerdict(
                verify.VerdictKind.existence_confirmed,
                VerificationResult(
                    found=True, provider="crossref", abstract="Prior work was consulted."
                ),
            )
        ),
    )
    monkeypatch.setattr(
        verify,
        "run_claim_support_check",
        AsyncMock(
            return_value=ClaimSupportResult(
                [],
                0,
                0,
                0,
                pair_results=[
                    ClaimSupportPairResult(
                        i, "assessed", model_verdict="supported", effective_verdict="supported"
                    )
                    for i in range(len(citations))
                ],
            )
        ),
    )
    monkeypatch.setattr(verify, "write_audit_event", AsyncMock())
    settings = Settings(
        _env_file=None,
        citation_recency_grammar_file=tmp_path / "missing.json" if failure == "grammar" else None,
    )
    result = await verify.run_citation_integrity_check(
        session, None, 1, citations, extraction, load_patterns(), settings, object()
    )
    summary = result.detail["citation_recency"]
    if failure == "none":
        assert result.outcome == ResultOutcome.passed
        assert summary["assessment_coverage"] == "complete"
    else:
        assert result.outcome != ResultOutcome.passed
        assert result.detail["coverage_reason"]
        assert result.detail["analysis_coverage"] in {"partial", "unsupported"}
        assert summary["enabled"] is True
        module = next(
            m
            for m in integrity_modules([result], set())
            if m.check_kind == CheckKind.citation_integrity.value
        )
        assert module.state != "completed"
        assert summary.get("n_outside_window", 0) == 0
