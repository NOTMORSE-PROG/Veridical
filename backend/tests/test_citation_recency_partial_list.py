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
