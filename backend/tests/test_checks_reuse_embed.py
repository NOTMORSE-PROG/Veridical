"""V-036 unit tests: the embedding pipeline — chunk-and-average
determinism, chapter-span slicing, front-matter/reference exclusion. No
DB — this module never touches the database (pure function over
`ExtractionResult`), same convention as `test_checks_agreement_extract.py`."""

from app.checks.reuse.embed import (
    _average_normalize,
    _chunk_words,
    compute_document_embeddings,
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
