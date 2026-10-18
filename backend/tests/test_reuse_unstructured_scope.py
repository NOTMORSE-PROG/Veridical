"""F7 source-consumer regression controls, not labelled accuracy evaluation."""

import pytest

from app.checks.reuse import embed
from app.config import get_settings
from app.ingest.normalize import normalize
from app.ingest.patterns import load_patterns
from app.ingest.references import select_references
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, SourceLineSpan, TextBlock


def block(text, page=1, x=50):
    return TextBlock(text=text, page=page, bbox=(x, 50, 500, 70), max_font_size=11, bold_ratio=0)


def source(blocks, nodes=()):
    return ExtractionResult(
        page_count=max((b.page or 1 for b in blocks), default=1),
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="heuristics", nodes=list(nodes)),
    )


@pytest.fixture
def encoded(monkeypatch):
    inputs = []

    def recorder(texts, settings):
        inputs.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed, "embed_texts", recorder)
    return inputs


@pytest.mark.parametrize("with_node", [False, True])
def test_flat_and_reference_only_tree_include_body_but_not_bibliography(encoded, with_node):
    body = "The study implemented a service for classifying documents."
    extraction = source(
        [
            block(body),
            block("References", 2),
            block("Reyes, J. (2024). A separate source. Journal of Examples.", 2),
        ],
        [SectionNode(title="References", level=1, page=2)] if with_node else [],
    )
    before = extraction.model_dump()
    document = embed.compute_document_embeddings(extraction)
    assert document.whole_document is not None and document.chapters == []
    assert encoded == [body]
    passages = embed.compute_passage_embeddings(extraction)
    body_passages = [p for p in passages if p.chapter_index == embed.UNSTRUCTURED_BODY_SCOPE]
    assert len(body_passages) == 1
    assert body_passages[0].text == body
    assert body_passages[0].page == 1
    assert not body_passages[0].is_reference_list
    assert extraction.model_dump() == before


@pytest.mark.parametrize("with_node", [False, True])
def test_reference_only_source_never_becomes_body_comparison(encoded, with_node):
    extraction = source(
        [block("References"), block("Reyes, J. (2024). A separate source. Journal of Examples.")],
        [SectionNode(title="References", level=1, page=1)] if with_node else [],
    )
    assert embed.compute_document_embeddings(extraction).whole_document is None
    assert encoded == []
    assert all(p.is_reference_list for p in embed.compute_passage_embeddings(extraction))


def native_block(lines):
    spans = []
    offset = 0
    for i, text in enumerate(lines):
        spans.append(
            SourceLineSpan(
                start=offset,
                end=offset + len(text),
                bbox=(50, 50 + i * 20, 500, 65 + i * 20),
                max_font_size=11,
                bold_ratio=0,
                source_id=f"1:0:{i}",
            )
        )
        offset += len(text) + 1
    return block("\n".join(lines)).model_copy(
        update={
            "bbox": (50, 50, 500, 50 + len(lines) * 20),
            "source_lines": spans,
            "source_line_ids": [s.source_id for s in spans],
        }
    )


def test_mixed_native_block_uses_exact_non_reference_intervals(encoded):
    lines = [
        "An independently readable body passage.",
        "References",
        "Reyes, J. (2024). A separate source. Journal of Examples.",
    ]
    expected_body = "\n".join(lines[:-2])
    mixed = native_block(lines)
    extraction = source([mixed], [SectionNode(title="References", level=1, page=1)])
    before = extraction.model_dump()
    passages = embed.compute_passage_embeddings(extraction)
    body_passages = [p for p in passages if p.chapter_index == embed.UNSTRUCTURED_BODY_SCOPE]
    assert len(body_passages) == 1
    assert body_passages[0].text.strip() == expected_body
    assert not body_passages[0].is_reference_list
    assert (
        body_passages[0].text
        == body_passages[0].context_text[body_passages[0].char_start : body_passages[0].char_end]
    )
    assert all("Reyes" not in p.text for p in body_passages)
    assert extraction.model_dump() == before


def test_later_prose_reference_label_does_not_cut_away_body(encoded):
    lines = [
        "This parser recognizes the literal section label",
        "References",
        "when it appears in the required template.",
    ]
    following = "The following ordinary body text must remain available for comparison."
    extraction = source([native_block(lines), block(following, 2)])
    before = extraction.model_dump()
    selection = select_references(extraction, load_patterns())
    assert selection.span == [] and selection.issues == ()
    document = embed.compute_document_embeddings(extraction)
    assert document.whole_document is not None
    assert encoded == [normalize("\n".join([*lines, following]))]
    passages = embed.compute_passage_embeddings(extraction)
    assert "References" in "\n".join(p.text for p in passages)
    assert following in "\n".join(p.text for p in passages)
    assert extraction.model_dump() == before


@pytest.mark.parametrize("with_node", [False, True])
def test_empty_reference_heading_does_not_create_comparison_text(encoded, with_node):
    extraction = source(
        [native_block(["References"])],
        [SectionNode(title="References", level=1, page=1)] if with_node else [],
    )
    assert embed.compute_document_embeddings(extraction).whole_document is None
    assert not any(not p.is_reference_list for p in embed.compute_passage_embeddings(extraction))


def test_only_the_selected_heading_is_excluded_from_body(encoded):
    lines = [
        "This parser recognizes the literal section label",
        "References",
        "when it appears in the required template.",
    ]
    extraction = source(
        [
            native_block(lines),
            block("References", 2),
            block("Reyes, J. (2024). A separate source. Journal of Examples.", 2),
        ],
        [SectionNode(title="References", level=1, page=2)],
    )
    before = extraction.model_dump()
    selection = select_references(extraction, load_patterns())
    assert selection.heading is not None
    assert selection.heading.text == "References"
    assert selection.heading.page == 2
    assert selection.heading.source_block_index == 1
    assert selection.heading.source_start == 0
    assert selection.heading.source_end == len("References")
    assert embed.compute_document_embeddings(extraction).whole_document is not None
    assert encoded == [normalize("\n".join(lines))]
    passages = embed.compute_passage_embeddings(extraction)
    body_passages = [p for p in passages if p.chapter_index == embed.UNSTRUCTURED_BODY_SCOPE]
    assert len(body_passages) == 1
    assert body_passages[0].text == "\n".join(lines)
    assert extraction.model_dump() == before


def test_structured_front_matter_policy_does_not_expand(encoded):
    extraction = source(
        [
            block("Unrelated approval sheet."),
            block("Chapter 1", 2),
            block("The actual chapter text.", 2),
        ],
        [SectionNode(title="Chapter 1", level=1, page=2)],
    )
    passages = embed.compute_passage_embeddings(extraction)
    assert all(p.chapter_index == 0 for p in passages)
    assert all("approval" not in p.text for p in passages)


def test_unstructured_quote_exclusion_uses_configured_indentation_rule(encoded):
    settings = get_settings().model_copy(update={"reuse_passage_chunk_words": 5})
    quote = " ".join(["Quoted source content"] * (settings.block_quote_min_words + 1))
    extraction = source(
        [
            block("Ordinary body text at the usual margin."),
            block("More body text at the usual margin."),
            block(quote, x=50 + settings.block_quote_indent_threshold_points + 1),
        ]
    )
    passages = embed.compute_passage_embeddings(extraction, settings)
    assert any(p.is_block_quote and "Quoted source" in p.text for p in passages)
    assert any(not p.is_block_quote and "Ordinary body" in p.text for p in passages)


def test_damaged_line_provenance_does_not_produce_body_embeddings(encoded):
    damaged = block("Body text.").model_copy(update={"source_lines": []})
    extraction = source([damaged])
    assert embed.compute_document_embeddings(extraction).whole_document is None
    assert embed.compute_passage_embeddings(extraction) == []
    assert encoded == []
