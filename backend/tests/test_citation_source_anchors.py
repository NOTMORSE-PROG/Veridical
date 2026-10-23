"""F5 source-anchor integration controls; provider responses are explicit stubs."""

import pytest

from app.checks.citations.extract import CitationFlagDraft
from app.checks.citations.verify import _with_reference_anchor
from app.ingest.patterns import load_patterns
from app.ingest.references import reference_entry_locations
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.enums import FlagSeverity, ResultOutcome
from app.models.run import Flag
from tests.native_math_fixtures import record_empty_native_inventory
from tests.test_citations_partial_source import persisted, run


def manuscript(anchor_kind="page", *, numbered=False):
    texts = [
        "Prior work [1] reports this result."
        if numbered
        else "Prior work (Reyes, 2023) reports this result.",
        "References",
        ("[1] " if numbered else "") + "Reyes, J. (2023). An earlier study. Example Journal.",
        ("[2] " if numbered else "") + "Cruz, A. (2024). A different study. Example Journal.",
    ]
    return record_empty_native_inventory(
        ExtractionResult(
            page_count=4 if anchor_kind == "page" else 1,
            anchor_kind=anchor_kind,
            image_only=False,
            text_chars=sum(map(len, texts)),
            section_tree=SectionTree(source="none", nodes=[]),
            blocks=[
                TextBlock(
                    text=text,
                    page=page if anchor_kind == "page" else None,
                    paragraph=paragraph if anchor_kind == "paragraph" else None,
                    max_font_size=11,
                    bold_ratio=0,
                )
                for text, page, paragraph in zip(texts, [1, 2, 3, 4], [3, 8, 10, 14], strict=True)
            ],
            images=[],
        )
    )


@pytest.mark.parametrize("anchor_kind,expected", [("page", "p. 4"), ("paragraph", "\u00b614")])
@pytest.mark.parametrize("numbered", [False, True])
async def test_uncited_reference_uses_exact_entry_anchor_not_reference_heading(
    monkeypatch, anchor_kind, expected, numbered
):
    extraction = manuscript(anchor_kind, numbered=numbered)
    citations = persisted(extraction)
    before = extraction.model_dump_json()
    result, rows, _, _ = await run(monkeypatch, extraction, citations)
    flags = [
        row for row in rows if isinstance(row, Flag) and row.detail["kind"] == "uncited_reference"
    ]
    assert result.detail["n_uncited"] == len(flags) == 1
    assert flags[0].page_anchor == expected
    assert flags[0].evidence_excerpt == citations[1].raw_text
    assert flags[0].detail["order_index"] == 1
    assert extraction.model_dump_json() == before


@pytest.mark.parametrize("mismatch", ["raw_text", "order_index"])
async def test_old_mismatched_reference_does_not_borrow_a_current_entry_location(
    monkeypatch, mismatch
):
    extraction = manuscript("paragraph")
    citations = persisted(extraction)
    locations = {
        (item.index, item.raw_text): item
        for item in reference_entry_locations(extraction, load_patterns())
    }
    matched = CitationFlagDraft(
        severity=FlagSeverity.low,
        evidence_excerpt=citations[1].raw_text,
        page_anchor="reference #2",
        detail={"kind": "uncited_reference"},
    )
    assert (
        _with_reference_anchor(matched, citations[1].order_index, locations).page_anchor
        == "\u00b614"
    )
    if mismatch == "raw_text":
        citations[1].raw_text += " Extra historical text."
    else:
        citations[1].order_index = 7
    result, rows, metadata, support = await run(monkeypatch, extraction, citations)
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["coverage_reason"] == "reference_source_projection_unbound"
    assert not any(isinstance(row, Flag) for row in rows)
    metadata.assert_not_called()
    support.assert_not_called()
    # Preserve the lower-level historical anchor guard as well as fresh admission.
    draft = CitationFlagDraft(
        severity=FlagSeverity.low,
        evidence_excerpt=citations[1].raw_text,
        page_anchor=f"reference #{citations[1].order_index + 1}",
        detail={"kind": "uncited_reference"},
    )
    flag = _with_reference_anchor(draft, citations[1].order_index, locations)
    assert flag.page_anchor == f"reference #{citations[1].order_index + 1}"
    assert flag.evidence_excerpt == citations[1].raw_text


async def test_orphan_citation_retains_its_body_anchor(monkeypatch):
    extraction = manuscript("paragraph")
    extraction.blocks[0].text += " A different source (Santos, 2020) is mentioned."
    _, rows, _, _ = await run(monkeypatch, extraction, persisted(extraction))
    flag = next(
        row
        for row in rows
        if isinstance(row, Flag) and row.detail["kind"] == "orphan_in_text_citation"
    )
    assert flag.page_anchor == "\u00b63"
    assert "Santos, 2020" in flag.evidence_excerpt
