"""F1/F5/F7: retain proven primary entries without classifying an appendix."""

import pytest

from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references, non_reference_blocks, select_references
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock


def source(*, incomplete=False, weak=False, secondary=False):
    texts = [
        "The body describes implementation and evaluation of a software system.",
        "REFERENCES",
        "Reyes, J. (2024). First complete source. Journal of Examples.",
        "Santos, M. (2023). Second complete source. Journal of Examples.",
        "APPENDIX A",
        "1. Which part of the software did you evaluate?",
        "2. Explain the outcome of your evaluation.",
        "1. Describe another scenario.",
    ]
    if incomplete:
        texts[3] = "Santos, M. ("
    if secondary:
        texts[5] = "[1] Cruz, A. (2022). A secondary appendix source. Example Journal."
    blocks = [
        TextBlock(
            page=1 if i == 0 else 2 if i < 4 else 3,
            text=text,
            bbox=(36, 50 + i * 30, 500, 70 + i * 30),
            max_font_size=12,
            bold_ratio=1 if i == 1 or (i == 4 and not weak) else 0,
        )
        for i, text in enumerate(texts)
    ]
    return ExtractionResult(
        page_count=3,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(t) for t in texts),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
    )


@pytest.mark.parametrize("secondary", [False, True])
def test_primary_entries_survive_while_later_membership_stays_protected(secondary):
    extraction = source(secondary=secondary)
    patterns = load_patterns()
    selected = select_references(extraction, patterns)
    assert not selected.issues
    assert selected.membership_issues == ("reference_tail_membership_unresolved",)
    assert len(extract_references(extraction, patterns)) == 2
    assert selected.protected_span
    body = non_reference_blocks(extraction, patterns, exclude_heading=True)
    assert [b.text for b in body] == [extraction.blocks[0].text]
    assert not any("appendix" in b.text.lower() for b in body)


@pytest.mark.parametrize("mutation", ["incomplete", "weak"])
def test_unsafe_primary_or_boundary_cannot_be_made_authoritative(mutation):
    extraction = source(**{mutation: True})
    selected = select_references(extraction, load_patterns())
    assert not selected.protected_span
    assert not extract_references(extraction, load_patterns())


@pytest.mark.parametrize("misleading_entry_node", [False, True])
def test_outline_cannot_promote_the_secondary_bibliography_into_body(misleading_entry_node):
    extraction = source(secondary=True)
    nodes = [SectionNode(title="REFERENCES", page=2, level=1)]
    if misleading_entry_node:
        nodes.append(SectionNode(title=extraction.blocks[2].text, page=2, level=1))
    nodes.append(SectionNode(title="APPENDIX A", page=3, level=1))
    extraction.section_tree = SectionTree(source="embedded_toc", nodes=nodes)
    selected = select_references(extraction, load_patterns())
    assert len(selected.groups) == 2 and not selected.issues
    assert selected.membership_issues and selected.protected_span
    assert all(
        "secondary appendix" not in b.text
        for b in non_reference_blocks(extraction, load_patterns())
    )


@pytest.mark.asyncio
async def test_primary_metadata_runs_without_linkage_or_absence_claims(monkeypatch):
    from tests.test_citations_partial_source import persisted, run

    extraction = source(secondary=True)
    result, rows, provider, support = await run(monkeypatch, extraction, persisted(extraction))
    assert provider.await_count == 2
    assert result.detail["n_reference_metadata_attempted"] == 2
    support.assert_not_called()
    assert result.detail["reference_inventory_status"] == "bound"
    assert result.detail["reference_membership_status"] == "partial"
    assert result.detail["coverage_reason"] == "reference_membership_partial"
    assert result.detail["n_orphans"] == result.detail["n_uncited"] == 0
    assert result.detail["n_linkage_unassessed"] == 2
    assert result.detail["claim_support_inventory_status"] == "not_captured"


def test_document_body_is_available_but_unknown_passage_inventory_stays_withheld(monkeypatch):
    from app.checks.reuse import embed
    from app.config import Settings

    extraction = source(secondary=True)
    inputs = []

    def embedded(text, **kwargs):
        inputs.append(text)
        return [1.0, 0.0]

    monkeypatch.setattr(embed, "embed_long_text", embedded)
    result = embed.compute_document_embeddings(extraction, Settings(_env_file=None))
    assert result.whole_document is not None
    assert inputs and all(
        "Which part" not in text and "secondary appendix" not in text for text in inputs
    )
    assert embed._passage_drafts(extraction, Settings(_env_file=None)) is None
