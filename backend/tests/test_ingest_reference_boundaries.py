"""Omitted bibliography outline nodes must not discard later anchored headings."""

import pytest

from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references, non_reference_blocks, select_references
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock


def bibliography(*, later_page=1):
    texts = [
        "Bibliography",
        "[1] Author A. (2024). First source.",
        "[2] Author B. Resource, 2023. URL https://example.org/resource.",
        ".1\nAdditional materials",
        "The accompanying survey is available separately.",
    ]
    blocks = [
        TextBlock(
            page=1 if index < 3 else later_page,
            bbox=(10, index * 30, 400, index * 30 + 20),
            text=text,
            max_font_size=24 if index == 0 else 14 if index == 3 else 10,
            bold_ratio=1 if index in (0, 3) else 0,
        )
        for index, text in enumerate(texts)
    ]
    return ExtractionResult(
        page_count=later_page,
        anchor_kind="page",
        image_only=False,
        text_chars=300,
        blocks=blocks,
        images=[],
        section_tree=SectionTree(
            source="embedded_toc",
            nodes=[
                SectionNode(title="Additional materials", page=later_page, level=2),
            ],
        ),
    )


@pytest.mark.parametrize("page", [1, 2])
def test_later_source_anchored_heading_ends_omitted_bibliography(page):
    result = bibliography(later_page=page)
    refs = extract_references(result, load_patterns())
    assert len(refs) == 2
    assert refs[-1].raw_text == result.blocks[2].text
    assert result.blocks[3] in non_reference_blocks(result, load_patterns())


@pytest.mark.parametrize(
    "mutation", ["wrong_page", "duplicate", "body", "weak", "heuristic", "incomplete"]
)
def test_uncertain_heading_cannot_truncate_reference_inventory(mutation):
    result = bibliography()
    if mutation == "wrong_page":
        result.section_tree.nodes[0].page = 2
    elif mutation == "duplicate":
        result.blocks.append(result.blocks[3].model_copy())
    elif mutation == "body":
        result.blocks[3].text += "\nThis paragraph shares the block."
    elif mutation == "weak":
        result.blocks[3].max_font_size = 10
        result.blocks[3].bold_ratio = 0
    elif mutation == "heuristic":
        result.section_tree.source = "heuristics"
    elif mutation == "incomplete":
        result.blocks[2].text = "[2] Author B. (2023)."
    refs = extract_references(result, load_patterns())
    assert any("Additional materials" in ref.raw_text for ref in refs)


@pytest.mark.parametrize(
    "continuation",
    [
        "[3] Author C. (2025). A later reference.",
        "Author, C. (2025). An author-year reference.",
        "[3] [Online]. Available: https://example.org/three.",
        "[3]\n[Online]. Available: https://example.org/three.",
    ],
)
def test_bibliography_subsection_keeps_following_references(continuation):
    result = bibliography()
    result.blocks[4].text = continuation
    refs = extract_references(result, load_patterns())
    assert any(" ".join(continuation.split()) in ref.raw_text for ref in refs)


def test_restarted_bibliography_is_preserved_but_not_a_unique_reference_inventory():
    result = bibliography()
    result.blocks[4].text = "[1] Author C. (2025). A restarted bibliography."
    selection = select_references(result, load_patterns())
    assert selection.issues == ("ambiguous_duplicate_reference_number",)
    assert any(result.blocks[4].text in part.text for part in selection.span)
    assert extract_references(result, load_patterns()) == []
    assert non_reference_blocks(result, load_patterns()) == []


def test_explicit_docx_style_boundary_uses_paragraph_identity():
    result = bibliography()
    result.anchor_kind = "paragraph"
    result.page_count = 0
    result.section_tree.source = "styles"
    for index, block in enumerate(result.blocks):
        block.page, block.bbox, block.paragraph = None, None, index
    result.section_tree.nodes[0].page = None
    result.section_tree.nodes[0].paragraph = 3
    refs = extract_references(result, load_patterns())
    assert len(refs) == 2
    assert all("Additional materials" not in ref.raw_text for ref in refs)


@pytest.mark.parametrize("label", ["[3]", "3.", "3)"])
def test_detached_numbered_label_retains_unparsed_bibliography_subsection(label):
    result = bibliography()
    result.blocks[4].text = label
    result.blocks.append(
        result.blocks[4].model_copy(
            update={
                "text": "[Online]. Available: https://example.org/three.",
            }
        )
    )
    refs = extract_references(result, load_patterns())
    assert any("Additional materials" in ref.raw_text for ref in refs)
    assert any("https://example.org/three." in ref.raw_text for ref in refs)
