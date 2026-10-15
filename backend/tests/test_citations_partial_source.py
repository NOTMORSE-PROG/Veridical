"""F5 metadata-only source scope; incomplete manuscript content never passes."""

from unittest.mock import AsyncMock

import pytest

from app.checks.citations import verify
from app.config import Settings
from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references
from app.models.citation import Citation
from app.models.enums import ResultOutcome
from app.models.run import Flag
from tests.test_ingest_reference_scope import source
from tests.test_stabilization_integrity_controls import MemorySession


async def run(monkeypatch, extraction, citations, verdict=None):
    session = MemorySession()
    provider = AsyncMock(
        return_value=verdict or verify.CitationVerdict(verify.VerdictKind.not_found)
    )
    support = AsyncMock()
    monkeypatch.setattr(verify, "verify_citation", provider)
    monkeypatch.setattr(verify, "run_claim_support_check", support)
    monkeypatch.setattr(verify, "write_audit_event", AsyncMock())
    monkeypatch.setattr("app.checks.source_quality.write_audit_event", AsyncMock())
    result = await verify.run_citation_integrity_check(
        session, None, 1, citations, extraction, load_patterns(), Settings(), object()
    )
    return result, session.rows, provider, support


def persisted(extraction):
    return [
        Citation(manuscript_id=1, **vars(draft))
        for draft in extract_references(extraction, load_patterns())
    ]


async def test_readable_reference_check_runs_without_whole_document_absence_claim(monkeypatch):
    extraction = source()
    before = extraction.model_dump_json()
    result, rows, provider, support = await run(monkeypatch, extraction, persisted(extraction))
    provider.assert_awaited_once()
    support.assert_not_called()
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["analysis_coverage"] == "partial"
    assert result.detail["assessment_scope"] == "reference_metadata_only"
    assert result.detail["coverage_reason"] == "source_reading_order_partial"
    assert result.detail["n_reference_metadata_attempted"] == 1
    assert result.detail["n_source_pages_unassessed"] == 1
    assert result.detail["n_in_text_citations"] == 0
    assert result.detail["n_linkage_unassessed"] == 1
    assert result.detail["n_orphans"] == result.detail["n_uncited"] == 0
    assert result.detail["n_claim_support_checked"] == 0
    flags = [row for row in rows if isinstance(row, Flag)]
    assert len(flags) == 1
    assert flags[0].detail["kind"] == "unverifiable_not_found"
    assert flags[0].page_anchor == "p. 2"
    assert flags[0].evidence_excerpt == persisted(extraction)[0].raw_text
    assert extraction.model_dump_json() == before


async def test_zero_partial_findings_is_not_complete_or_not_applicable(monkeypatch):
    extraction = source()
    result, rows, provider, support = await run(
        monkeypatch,
        extraction,
        persisted(extraction),
        verify.CitationVerdict(verify.VerdictKind.existence_confirmed),
    )
    provider.assert_awaited_once()
    support.assert_not_called()
    assert not any(isinstance(row, Flag) for row in rows)
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["analysis_coverage"] == "partial"


@pytest.mark.parametrize(
    "damage", ["bibliography", "metadata", "stored_text", "stored_fields", "venue", "url"]
)
async def test_partial_path_cannot_hide_unreadable_or_mismatched_source(monkeypatch, damage):
    extraction = source()
    citations = persisted(extraction)
    if damage == "bibliography":
        extraction.reading_order[-1].status = "unresolved"
        extraction.reading_order[-1].reason = "overlapping_text"
    elif damage == "metadata":
        extraction.reading_order.pop(0)
    elif damage == "stored_text":
        citations[0].raw_text += " unrelated content"
    elif damage in {"venue", "url"}:
        setattr(citations[0], damage, "Unrelated stored metadata")
    else:
        citations[0].year = 1999
    result, rows, provider, support = await run(monkeypatch, extraction, citations)
    provider.assert_not_called()
    support.assert_not_called()
    assert result.detail["analysis_coverage"] == "unsupported"
    assert result.detail["coverage_reason"] == "source_reading_order_unresolved"
    assert result.outcome == ResultOutcome.escalated
    assert not any(isinstance(row, Flag) for row in rows)


async def test_extraction_without_lookup_is_not_partial_verification(monkeypatch):
    extraction = source()
    extraction.blocks[-1].text = "Unsupported reference without a known metadata format."
    citations = persisted(extraction)
    assert len(citations) == 1
    result, _, provider, support = await run(monkeypatch, extraction, citations)
    provider.assert_not_called()
    support.assert_not_called()
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["analysis_coverage"] == "unsupported"
    assert result.detail["n_reference_metadata_attempted"] == 0
