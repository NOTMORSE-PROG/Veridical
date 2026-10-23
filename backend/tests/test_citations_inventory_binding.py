"""F5: fresh conclusions require the complete source-bound reference inventory."""

from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.checks.citations import verify
from app.checks.citations.projection import SOURCE_FIELDS
from app.config import Settings
from app.external.schemas import VerificationResult
from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references
from app.ingest.schemas import TableBlock
from app.models.citation import Citation
from app.models.enums import CitationParseStatus, ResultOutcome
from app.models.run import Flag
from tests.test_citation_claim_occurrences import ScriptedClaims
from tests.test_ingest_reference_scope import source as partial_source
from tests.test_reference_inventory import REF_A, REF_B, source
from tests.test_stabilization_integrity_controls import MemorySession


def bound_source():
    extraction = source([REF_A, REF_B])
    extraction.blocks[0].text = "Prior studies (Reyes, 2024) and (Santos, 2022) found improvements."
    extraction.text_chars = sum(len(block.text) for block in extraction.blocks)
    return extraction


def retained(extraction):
    return [
        Citation(id=index + 1, manuscript_id=1, **asdict(draft))
        for index, draft in enumerate(extract_references(extraction, load_patterns()))
    ]


async def assess(monkeypatch, extraction, citations, *, reject=False):
    before = extraction.model_dump_json()
    rows_before = [{name: getattr(row, name) for name in SOURCE_FIELDS} for row in citations]
    metadata = AsyncMock(
        return_value=verify.CitationVerdict(
            verify.VerdictKind.existence_confirmed,
            VerificationResult(
                found=True, provider="crossref", abstract="The study found improvements."
            ),
        )
    )
    model, session, audit = ScriptedClaims(), MemorySession(), AsyncMock()
    monkeypatch.setattr(verify, "verify_citation", metadata)
    monkeypatch.setattr(verify, "write_audit_event", audit)
    monkeypatch.setattr("app.checks.source_quality.write_audit_event", audit)
    if reject:

        def forbidden(*args, **kwargs):
            raise AssertionError("Unbound input reached linkage or claim support")

        monkeypatch.setattr(verify, "cross_match", forbidden)
        monkeypatch.setattr(verify, "analyze_numbered_citations", forbidden)
        monkeypatch.setattr(verify, "run_claim_support_check", forbidden)
    result = await verify.run_citation_integrity_check(
        session, None, 1, citations, extraction, load_patterns(), Settings(_env_file=None), model
    )
    assert extraction.model_dump_json() == before
    assert [
        {name: getattr(row, name) for name in SOURCE_FIELDS} for row in citations
    ] == rows_before
    return result, session.rows, metadata, model, audit


@pytest.mark.parametrize("damage", ["subset", "extra", "reorder", "replacement", "raw", "index"])
async def test_unbound_membership_cannot_create_absence_or_coverage(monkeypatch, damage):
    extraction = bound_source()
    citations = retained(extraction)
    if damage == "subset":
        citations.pop()
    elif damage == "extra":
        citations.append(
            Citation(
                manuscript_id=1, **asdict(extract_references(source([REF_A]), load_patterns())[0])
            )
        )
        citations[-1].order_index = 2
    elif damage == "reorder":
        citations.reverse()
        for index, row in enumerate(citations):
            row.order_index = index
    elif damage == "replacement":
        citations[1] = retained(source([REF_A]))[0]
        citations[1].order_index = 1
    elif damage == "raw":
        citations[1].raw_text += " Unrelated content."
    else:
        citations[1].order_index = 0
    result, rows, metadata, model, audit = await assess(
        monkeypatch, extraction, citations, reject=True
    )
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["coverage_reason"] == "reference_source_projection_unbound"
    assert result.detail["analysis_coverage"] == "unsupported"
    assert result.detail["reference_inventory_status"] == "not_captured"
    assert result.detail["claim_support_inventory_status"] == "not_captured"
    assert result.detail["n_source_reference_groups"] == 2
    assert result.detail["n_retained_reference_rows"] == len(citations)
    assert result.detail["reference_inventory_issues"]
    assert result.detail["n_claim_support_checked"] == 0
    assert "claim_support_occurrences" not in result.detail
    assert "n_claim_support_candidate_pairs_total" not in result.detail
    assert "n_claim_support_pairs_total" not in result.detail
    assert not any(isinstance(row, Flag) for row in rows)
    metadata.assert_not_called()
    assert model.prompts == []
    assert audit.await_args.kwargs["payload"]["reference_inventory_issues"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("authors", ["Garcia, M."]),
        ("year", 1999),
        ("title", "Unrelated search title"),
        ("doi", "10.1234/unrelated"),
        ("isbn", "9780132350884"),
        ("url", "https://example.test/unrelated"),
        ("venue", "Unrelated venue"),
        ("parse_status", CitationParseStatus.parse_failed),
    ],
)
async def test_identical_raw_inventory_does_not_authorize_altered_metadata(
    monkeypatch, field, value
):
    extraction = bound_source()
    citations = retained(extraction)
    setattr(citations[1], field, value)
    result, rows, metadata, model, _ = await assess(monkeypatch, extraction, citations, reject=True)
    assert result.detail["reference_inventory_issues"] == ["reference_metadata_fields_mismatch"]
    assert result.outcome == ResultOutcome.escalated
    assert not any(isinstance(row, Flag) for row in rows)
    metadata.assert_not_called()
    assert model.prompts == []


async def test_normalized_raw_membership_retains_real_occurrence_ledger(monkeypatch):
    extraction = bound_source()
    citations = retained(extraction)
    citations[1].raw_text = "  " + citations[1].raw_text.replace(" ", "  ") + "\u200b "
    result, rows, metadata, model, _ = await assess(monkeypatch, extraction, citations)
    assert result.outcome == ResultOutcome.passed
    assert result.detail["reference_inventory_status"] == "bound"
    assert result.detail["n_claim_support_checked"] == 2
    assert len(result.detail["claim_support_occurrences"]) == 2
    assert not any(isinstance(row, Flag) for row in rows)
    assert metadata.await_count == 2 and model.prompts


async def test_bound_inventory_keeps_genuine_orphan_evidence(monkeypatch):
    extraction = bound_source()
    extraction.blocks[0].text += " Other work (Garcia, 2021) made a different claim."
    result, rows, metadata, _, _ = await assess(monkeypatch, extraction, retained(extraction))
    flags = [row for row in rows if isinstance(row, Flag)]
    assert [flag.detail["kind"] for flag in flags] == ["orphan_in_text_citation"]
    assert "Garcia" in flags[0].evidence_excerpt
    assert flags[0].page_anchor == "\u00b60"
    assert result.detail["reference_inventory_status"] == "bound"
    assert metadata.await_count == 2


@pytest.mark.parametrize("position,admit", [(2, False), (0, True), (4, True), (None, False)])
async def test_rich_content_scope_cannot_imply_text_inventory_complete(
    monkeypatch, position, admit
):
    extraction = bound_source()
    extraction.tables.append(TableBlock(paragraph=position, rows=[["Unassessed reference"]]))
    result, rows, metadata, _, _ = await assess(
        monkeypatch, extraction, retained(extraction), reject=not admit
    )
    if admit:
        assert result.detail["reference_inventory_status"] == "bound"
        assert metadata.await_count == 2
    else:
        assert result.detail["reference_inventory_status"] == "not_captured"
        assert result.outcome == ResultOutcome.escalated
        assert not any(isinstance(row, Flag) for row in rows)
        metadata.assert_not_called()


def assert_uncaptured_claim_inventory(detail):
    assert detail["claim_support_inventory_status"] == "not_captured"
    assert detail["n_claim_support_checked"] == 0
    for key in (
        "claim_support_occurrences",
        "n_claim_support_candidate_pairs_total",
        "n_claim_support_pairs_total",
        "n_claim_support_pairs_unassessed",
    ):
        assert key not in detail


@pytest.mark.parametrize("case", ["missing_all", "known_zero", "table"])
async def test_missing_all_rows_keeps_existing_no_reference_limitation(monkeypatch, case):
    extraction = bound_source() if case == "missing_all" else source([])
    if case == "table":
        extraction.tables.append(TableBlock(paragraph=1, rows=[[REF_A]]))
    result, rows, metadata, model, _ = await assess(monkeypatch, extraction, [])
    assert result.detail["coverage_reason"] == "reference_list_unavailable"
    assert result.detail["reference_inventory_status"] == "not_captured"
    assert_uncaptured_claim_inventory(result.detail)
    assert result.outcome == ResultOutcome.escalated
    assert not any(isinstance(row, Flag) for row in rows)
    metadata.assert_not_called()
    assert model.prompts == []


async def test_metadata_only_does_not_invent_a_body_candidate_inventory(monkeypatch):
    extraction = partial_source()
    result, rows, metadata, model, _ = await assess(monkeypatch, extraction, retained(extraction))
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["reference_inventory_status"] == "bound"
    assert result.detail["assessment_scope"] == "reference_metadata_only"
    assert result.detail["coverage_reason"] == "source_reading_order_partial"
    assert_uncaptured_claim_inventory(result.detail)
    assert metadata.await_count == 1
    assert model.prompts == []
    assert not any(isinstance(row, Flag) for row in rows)
