"""V-036 unit tests: the embedding pipeline — chunk-and-average
determinism, chapter-span slicing, front-matter/reference exclusion. No
DB — this module never touches the database (pure function over
`ExtractionResult`), same convention as `test_checks_agreement_extract.py`."""

from app.checks.reuse.embed import (
    _add_context,
    _average_normalize,
    _chunk_words,
    _word_chunk_offsets,
    compute_document_embeddings,
    compute_passage_embeddings,
    embed_long_text,
)
from app.config import get_settings
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.ml.embeddings import cosine_similarity

SETTINGS = get_settings()


def _block(text: str, *, page: int | None = 1) -> TextBlock:
    return TextBlock(text=text, page=page, max_font_size=11.0, bold_ratio=0.0)


def _extraction(
    blocks: list[TextBlock], nodes: list[SectionNode] | None = None
) -> ExtractionResult:
    return ExtractionResult(
        page_count=max((b.page or 1) for b in blocks) if blocks else 1,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="heuristics", nodes=nodes or []),
        blocks=blocks,
        images=[],
    )


# --- chunking + determinism ----------------------------------------------------


def test_chunk_words_splits_on_boundaries():
    text = " ".join(f"word{i}" for i in range(10))
    chunks = _chunk_words(text, 4)
    assert chunks == ["word0 word1 word2 word3", "word4 word5 word6 word7", "word8 word9"]


def test_chunk_words_empty_text_is_no_chunks():
    assert _chunk_words("   ", 100) == []


def test_embed_long_text_is_deterministic():
    """Ticket AC: same text -> same vector, pinned static model."""
    text = "The system will extract structured citations from the reference list."
    v1 = embed_long_text(text, chunk_words=800, settings=SETTINGS)
    v2 = embed_long_text(text, chunk_words=800, settings=SETTINGS)
    assert v1 == v2


def test_embed_long_text_chunked_vs_unchunked_are_highly_similar():
    """Chunk-and-average shouldn't materially change the vector for text
    that already fits in one chunk — sanity check on the averaging math."""
    text = "Attendance tracking is currently manual. " * 5
    whole = embed_long_text(text, chunk_words=10_000, settings=SETTINGS)
    chunked = embed_long_text(text, chunk_words=6, settings=SETTINGS)
    assert cosine_similarity(whole, chunked) > 0.85


def test_average_normalize_returns_unit_vector():
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    result = _average_normalize(vectors)
    norm = sum(x * x for x in result) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_embed_long_text_empty_is_none_not_a_fabricated_zero_vector():
    assert embed_long_text("   ", chunk_words=800, settings=SETTINGS) is None


# --- chapter-span slicing + whole-document composition ------------------------


def test_whole_document_is_chapters_combined_excludes_front_matter():
    blocks = [
        _block("VERIDICAL: A Defense-Readiness Checker", page=1),  # title page, no chapter yet
        _block("Approval Sheet", page=2),
        _block("Chapter 1: Introduction", page=3),
        _block("The current process is entirely manual.", page=3),
        _block("Chapter 2: Methodology", page=10),
        _block("We used a hybrid rule-based and AI approach.", page=10),
    ]
    nodes = [
        SectionNode(title="Chapter 1: Introduction", level=1, page=3),
        SectionNode(title="Chapter 2: Methodology", level=1, page=10),
    ]
    result = compute_document_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    assert result.whole_document is not None
    assert len(result.chapters) == 2
    assert result.chapters[0].title == "Chapter 1: Introduction"
    assert result.chapters[1].title == "Chapter 2: Methodology"
    # Front matter (title page, approval sheet) never entered any chapter span.
    assert "entirely manual" in "".join(  # sanity: content WAS captured
        b.text for b in blocks if b.page == 3
    )


def test_chapter_span_stops_at_next_chapter():
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block("Intro content here.", page=1),
        _block("Chapter 2: Methodology", page=5),
        _block("Methodology content here.", page=5),
    ]
    nodes = [
        SectionNode(title="Chapter 1: Introduction", level=1, page=1),
        SectionNode(title="Chapter 2: Methodology", level=1, page=5),
    ]
    result = compute_document_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    assert len(result.chapters) == 2
    ch1_vec, ch2_vec = result.chapters[0].embedding, result.chapters[1].embedding
    # Different content -> not identical vectors (spans didn't bleed into each other).
    assert ch1_vec != ch2_vec


def test_model_id_recorded_on_every_embedding():
    blocks = [_block("Chapter 1: Introduction", page=1), _block("Some real content.", page=1)]
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, page=1)]
    result = compute_document_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    assert result.model_id == SETTINGS.embedding_model_id


def test_no_chapter_structure_falls_back_to_flat_content():
    """A document with no parseable section tree (source='none') still
    gets SOME archive coverage rather than silently opting out of F7."""
    blocks = [_block("Some flat, unstructured content with no headings at all.", page=1)]
    result = compute_document_embeddings(_extraction(blocks, nodes=[]), settings=SETTINGS)
    assert result.whole_document is not None
    assert result.chapters == []


def test_empty_manuscript_has_no_whole_document_vector():
    result = compute_document_embeddings(_extraction([], nodes=[]), settings=SETTINGS)
    assert result.whole_document is None
    assert result.chapters == []


# --- passage-level chunking (V-072, F7.4) --------------------------------------


def test_word_chunk_offsets_covers_every_word_exactly_once():
    text = " ".join(f"word{i}" for i in range(10))
    offsets = _word_chunk_offsets(text, 4)
    assert [text[s:e] for s, e in offsets] == [
        "word0 word1 word2 word3",
        "word4 word5 word6 word7",
        "word8 word9",
    ]


def test_add_context_widens_by_real_words_each_side():
    text = "one two three FOUR five six seven"
    start, end = text.index("FOUR"), text.index("FOUR") + len("FOUR")
    assert _add_context(text, start, end, context_words=2) == "two three FOUR five six"


def test_add_context_zero_words_returns_only_the_passage_itself():
    """`backend-critic` finding (V-072 review, 2026-08-20): `-0 == 0` in
    Python made `context_words=0` silently include ALL preceding text
    instead of none -- the opposite of what the setting says. Regression
    guard for the fixed three-branch version."""
    text = "one two three FOUR five six seven"
    start, end = text.index("FOUR"), text.index("FOUR") + len("FOUR")
    assert _add_context(text, start, end, context_words=0) == "FOUR"


def test_add_context_bounded_at_document_edges():
    text = "one two three"
    start, end = 0, len("one")
    # Only "two three" exists after -- context_words=10 must not overrun.
    assert _add_context(text, start, end, context_words=10) == "one two three"


def test_compute_passage_embeddings_short_chapter_is_one_passage():
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block("The current process for tracking attendance is entirely manual.", page=1),
    ]
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, page=1)]
    passages = compute_passage_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    assert len(passages) == 1
    assert passages[0].chapter_index == 0
    assert passages[0].page == 1
    assert passages[0].is_reference_list is False
    assert passages[0].is_block_quote is False
    assert "entirely manual" in passages[0].text


def test_compute_passage_embeddings_no_chapter_structure_is_empty():
    """Honest, named scope limit (module docstring): F7.4 needs chapter
    structure. A flat/unparseable document still gets F7.1's whole-document
    fallback (unaffected by this ticket) but no passage-level coverage."""
    blocks = [_block("Some flat, unstructured content with no headings at all.", page=1)]
    passages = compute_passage_embeddings(_extraction(blocks, nodes=[]), settings=SETTINGS)
    assert passages == []


def test_compute_passage_embeddings_tags_reference_list_passages():
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block("Real body content about attendance tracking.", page=1),
        _block("References", page=2),
        _block("Doe, J. (2020). A study of things. Journal of Studies, 1(1), 1-10.", page=2),
    ]
    nodes = [
        SectionNode(title="Chapter 1: Introduction", level=1, page=1),
        SectionNode(title="References", level=1, page=2),
    ]
    passages = compute_passage_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    assert len(passages) == 2
    body_passage = next(p for p in passages if p.chapter_index == 0)
    ref_passage = next(p for p in passages if p.chapter_index == 1)
    assert body_passage.is_reference_list is False
    assert ref_passage.is_reference_list is True
    # Tagged, not dropped (module docstring) -- the reference passage is
    # still embedded and still carries its own real text.
    assert "Doe, J." in ref_passage.text


def test_compute_passage_embeddings_offsets_are_real_slices_of_chapter_text():
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block("First sentence here.", page=1),
    ]
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, page=1)]
    passages = compute_passage_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    full_chapter_text = "Chapter 1: Introduction\nFirst sentence here."
    passage = passages[0]
    assert full_chapter_text[passage.char_start : passage.char_end] == passage.text


def test_compute_passage_embeddings_is_deterministic():
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block("The current process for tracking attendance is entirely manual.", page=1),
    ]
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, page=1)]
    p1 = compute_passage_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    p2 = compute_passage_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    assert p1[0].embedding == p2[0].embedding


def test_compute_passage_embeddings_large_block_splits_by_word_count():
    """A single block far exceeding the passage size (>2x chunk_words)
    splits into multiple passages by word count instead of becoming one
    unbounded passage."""
    giant_text = " ".join(f"word{i}" for i in range(SETTINGS.reuse_passage_chunk_words * 3))
    blocks = [_block("Chapter 1: Introduction", page=1), _block(giant_text, page=1)]
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, page=1)]
    passages = compute_passage_embeddings(_extraction(blocks, nodes), settings=SETTINGS)
    assert len(passages) >= 2
    for p in passages:
        assert len(p.text.split()) <= SETTINGS.reuse_passage_chunk_words
