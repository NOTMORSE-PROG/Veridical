"""F7 source-consumer regression controls, not labelled accuracy evaluation."""

import json
from pathlib import Path

import pytest

from app.checks.reuse import embed
from app.config import get_settings
from app.ingest import patterns as patterns_module
from app.ingest.normalize import normalize
from app.ingest.patterns import load_patterns
from app.ingest.references import select_references
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, SourceLineSpan, TextBlock
from tests.native_math_fixtures import record_empty_native_inventory


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


def ancillary_source(*, title="Acknowledgements", mixed=False):
    """A short paper with no detected substantive headings, not a synthetic chapter."""
    lines = [
        "Closing discussion belongs to the manuscript body.",
        title,
        "The authors thank the maintainers for their feedback.",
        "References",
        "Reyes, J. (2024). A separate source. Journal of Examples.",
    ]
    tail = (
        [native_block(lines).model_copy(update={"page": 3})]
        if mixed
        else [block(text, 3) for text in lines]
    )
    return source(
        [
            block("The study implemented a document classification service."),
            block("Its evaluation used a separately collected sample.", 2),
            *tail,
        ],
        [
            SectionNode(title=title, level=1, page=3),
            SectionNode(title="References", level=1, page=3),
        ],
    )


@pytest.mark.parametrize("mixed", [False, True])
def test_ancillary_only_tree_restores_exact_body_without_duplicate_reference_scope(encoded, mixed):
    extraction = ancillary_source(mixed=mixed)
    before = extraction.model_dump()
    document = embed.compute_document_embeddings(extraction)
    assert document.whole_document is not None
    assert document.chapters == []
    assert len(encoded) == 1
    assert "classification service" in encoded[0]
    assert "maintainers" in encoded[0]
    assert "Reyes" not in encoded[0]
    assert "References" not in encoded[0]

    passages = embed.compute_passage_embeddings(extraction)
    body = [p for p in passages if not p.is_reference_list]
    references = [p for p in passages if p.is_reference_list]
    assert body and references
    assert all(p.chapter_index == embed.UNSTRUCTURED_BODY_SCOPE for p in body)
    assert all(p.chapter_index == 1 for p in references)
    body_text = "\n".join(p.text for p in body)
    assert body_text.count("The authors thank the maintainers") == 1
    assert "Closing discussion" in body_text
    assert "Reyes" not in body_text and "References" not in body_text
    assert "Reyes" in "\n".join(p.text for p in references)
    assert all(
        "maintainers" not in p.context_text and "Closing discussion" not in p.context_text
        for p in references
    )
    assert extraction.model_dump() == before


def test_ancillary_and_substantive_headings_preserve_structured_scope(encoded):
    extraction = ancillary_source()
    extraction.blocks.insert(0, block("Approval text outside the real chapter."))
    extraction.section_tree.nodes.insert(0, SectionNode(title="Evaluation", level=1, page=2))
    document = embed.compute_document_embeddings(extraction)
    assert [chapter.chapter_index for chapter in document.chapters] == [0]
    assert all("Approval text" not in text for text in encoded)
    passages = embed.compute_passage_embeddings(extraction)
    assert all(p.chapter_index != embed.UNSTRUCTURED_BODY_SCOPE for p in passages)
    assert all("Approval text" not in p.context_text for p in passages)


def configured_settings(tmp_path, ancillary_titles):
    original = Path(patterns_module.__file__).parent / "data" / "heading_patterns.json"
    raw = json.loads(original.read_text(encoding="utf-8"))
    if ancillary_titles is None:
        raw.pop("reuse_ancillary_titles", None)
    else:
        raw["reuse_ancillary_titles"] = ancillary_titles
    target = tmp_path / "custom-heading-patterns.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    return get_settings().model_copy(update={"ingest_patterns_file": target})


def test_older_custom_patterns_inherit_ancillary_default(encoded, tmp_path):
    settings = configured_settings(tmp_path, None)
    result = embed.compute_document_embeddings(ancillary_source(), settings)
    assert result.whole_document is not None and result.chapters == []


def test_explicit_custom_ancillary_titles_are_exact_normalized_matches(encoded, tmp_path):
    settings = configured_settings(tmp_path, ["  Contributor   notes  "])
    result = embed.compute_document_embeddings(
        ancillary_source(title="CONTRIBUTOR NOTES"), settings
    )
    assert result.whole_document is not None and result.chapters == []
    encoded.clear()
    different = ancillary_source(title="Contributor notes analysis")
    assert embed.compute_document_embeddings(different, settings).whole_document is None
    assert encoded == []


def test_explicit_empty_ancillary_config_preserves_original_chapter_selection(encoded, tmp_path):
    settings = configured_settings(tmp_path, [])
    assert embed.compute_document_embeddings(ancillary_source(), settings).whole_document is None
    assert encoded == []


@pytest.mark.parametrize("invalid", ["acknowledgements", [None], ["  "]])
def test_invalid_ancillary_configuration_is_not_silently_coerced(tmp_path, invalid):
    settings = configured_settings(tmp_path, invalid)
    with pytest.raises(ValueError, match="ancillary"):
        load_patterns(settings.ingest_patterns_file)


@pytest.mark.parametrize("damaged", [False, True])
def test_ancillary_fallback_does_not_bypass_ambiguous_or_invalid_source(encoded, damaged):
    extraction = ancillary_source()
    if damaged:
        extraction.blocks[0] = extraction.blocks[0].model_copy(update={"source_lines": []})
    else:
        extraction.blocks[-1:] = [
            block("[1] Reyes, J. (2024). One work.", 3),
            block("[1] Santos, A. (2023). A different work.", 3),
        ]
    assert select_references(extraction, load_patterns()).issues
    assert embed.compute_document_embeddings(extraction).whole_document is None
    assert embed.compute_passage_embeddings(extraction) == []
    assert encoded == []


@pytest.mark.parametrize("printed_title", ["7. References", "II. References"])
def test_numbered_selected_heading_retains_reference_passages(encoded, printed_title):
    extraction = ancillary_source()
    extraction.blocks[-2] = block(printed_title, 3)
    selection = select_references(extraction, load_patterns())
    assert not selection.issues and len(selection.span) == 1
    passages = embed.compute_passage_embeddings(extraction)
    references = [p for p in passages if p.is_reference_list]
    assert references and all(p.chapter_index == 1 for p in references)
    assert "Reyes" in "\n".join(p.text for p in references)
    assert all("maintainers" not in p.context_text for p in references)
    assert all(
        "Reyes" not in p.context_text and printed_title not in p.context_text
        for p in passages
        if not p.is_reference_list
    )


def test_ancillary_fallback_preserves_docx_paragraph_anchors(encoded):
    extraction = ancillary_source()
    extraction.anchor_kind = "paragraph"
    for index, source_block in enumerate(extraction.blocks):
        source_block.page = None
        source_block.paragraph = index
    extraction.section_tree.nodes = [
        SectionNode(title="Acknowledgements", level=1, paragraph=3),
        SectionNode(title="References", level=1, paragraph=5),
    ]
    extraction = record_empty_native_inventory(extraction)
    before = extraction.model_dump()
    document = embed.compute_document_embeddings(extraction)
    passages = embed.compute_passage_embeddings(extraction)
    assert document.whole_document is not None and document.chapters == []
    assert all(p.page is None and p.paragraph is not None for p in passages)
    assert all(p.anchor_kind == "paragraph" for p in passages)
    assert all(p.paragraph == 5 for p in passages if p.is_reference_list)
    assert all("maintainers" not in p.context_text for p in passages if p.is_reference_list)
    assert extraction.model_dump() == before
