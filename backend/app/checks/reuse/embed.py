"""Content embedding pipeline (F7.1, V-036): whole-document + per-chapter
vectors for the originality/reuse archive, feeding V-037's similarity
query and write-back.

**Model** (D-011, confirmed via DECISIONS.md's 2026-08-08 addendum):
potion-base-8M, `app.ml.embeddings` — measured ~102MB alone, well inside
the free-tier ceiling. Static (non-contextual) embedding, so re-embedding
the same text is bit-for-bit deterministic by construction (ticket AC).

**Chunk-and-average for long text** (ticket edge case: "100+ page docs
exceed embedding input limits"): potion-base-8M has no hard token-limit
error in practice (measured: a 200,000-word string encodes without
failing), but averaging a single huge bag-of-tokens vector over an entire
manuscript dilutes whatever made it distinctive — chunking bounds that
dilution to `reuse_embedding_chunk_words`-sized pieces and averages their
(L2-normalized) vectors, a documented, stable method (not a workaround for
an error that doesn't actually occur).

**Text selection excludes furniture and the reference list**
(`app.ingest.references.non_reference_blocks`, same utility F5's citation
extraction already uses) — reused here rather than re-derived, because a
reference list embedded into the vector would inflate similarity between
two UNRELATED manuscripts that happen to cite the same course readings
(V-037's own edge case: "two groups legitimately citing the same sources
≠ reuse"). Filtering only at V-037's QUERY time couldn't fix a vector that
was embedded with that noise already baked in — so it belongs here, at
the point the vector is actually computed, and V-037 inherits a clean
archive for free. **Whole-document = every chapter's text concatenated**
(not literally the raw entire manuscript) — the same choice naturally
excludes front-matter (title page, approval sheet, table of contents),
which V-037 also names as a source of boilerplate-inflated similarity:
front matter isn't inside any Chapter-level section span, and this reuses
ONE mechanism for both edge cases rather than two.
"""

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.ingest.patterns import load_patterns
from app.ingest.references import non_reference_blocks
from app.ingest.schemas import ExtractionResult, SectionNode, TextBlock
from app.ml.embeddings import embed_texts


@dataclass(frozen=True)
class ChapterEmbedding:
    chapter_index: int
    title: str
    anchor: str
    page: int | None
    paragraph: int | None
    embedding: list[float]


@dataclass(frozen=True)
class DocumentEmbeddings:
    model_id: str
    whole_document: list[float] | None  # None only when the manuscript has no chapter text at all
    chapters: list[ChapterEmbedding]


def _chunk_words(text: str, chunk_size: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    return [" ".join(words[i : i + chunk_size]) for i in range(0, len(words), chunk_size)]


def _average_normalize(vectors: list[list[float]]) -> list[float]:
    """Mean of L2-normalized vectors, re-normalized — keeps the result a
    valid unit vector for cosine similarity regardless of how many chunks
    fed into it (a plain arithmetic mean would NOT stay unit-length)."""
    dim = len(vectors[0])
    normalized = []
    for v in vectors:
        norm = sum(x * x for x in v) ** 0.5
        normalized.append([x / norm for x in v] if norm > 0 else v)
    mean = [sum(v[i] for v in normalized) / len(normalized) for i in range(dim)]
    mean_norm = sum(x * x for x in mean) ** 0.5
    return [x / mean_norm for x in mean] if mean_norm > 0 else mean


def embed_long_text(
    text: str, *, chunk_words: int, settings: Settings | None = None
) -> list[float] | None:
    """Returns None for empty/whitespace-only text — an honest absence, not
    a fabricated zero-vector that would silently look "maximally
    dissimilar" from everything in similarity queries."""
    chunks = _chunk_words(text, chunk_words)
    if not chunks:
        return None
    vectors = embed_texts(chunks, settings)
    return vectors[0] if len(vectors) == 1 else _average_normalize(vectors)


def _anchor_for(node: SectionNode) -> str:
    if node.page is not None:
        return f"p. {node.page}"
    if node.paragraph is not None:
        return f"¶{node.paragraph}"
    return "document"


def _chapter_span_blocks(
    node: SectionNode, next_chapter: SectionNode | None, blocks: list[TextBlock], anchor_kind: str
) -> list[TextBlock]:
    """Same convention as `app.checks.semantic._section_span_blocks`, kept
    as its own small copy rather than importing that module's private
    helper across a package boundary — this one only ever needs top-level
    (chapter) boundaries, not `semantic`'s deeper sibling-or-higher logic
    for arbitrarily-named sections."""
    if anchor_kind == "page":
        start, end = node.page or 0, next_chapter.page if next_chapter else None
        return [
            b
            for b in blocks
            if b.page is not None and start <= b.page and (end is None or b.page < end)
        ]
    start = node.paragraph or 0
    end = next_chapter.paragraph if next_chapter else None
    return [
        b
        for b in blocks
        if b.paragraph is not None and start <= b.paragraph and (end is None or b.paragraph < end)
    ]


def compute_document_embeddings(
    extraction: ExtractionResult, settings: Settings | None = None
) -> DocumentEmbeddings:
    settings = settings or get_settings()
    patterns = load_patterns(settings.ingest_patterns_file)
    content_blocks = non_reference_blocks(extraction, patterns)

    chapters = [n for n in extraction.section_tree.nodes if n.level == 1]
    chapter_embeddings: list[ChapterEmbedding] = []
    chapter_texts: list[str] = []

    for i, node in enumerate(chapters):
        next_chapter = chapters[i + 1] if i + 1 < len(chapters) else None
        span_blocks = _chapter_span_blocks(
            node, next_chapter, content_blocks, extraction.anchor_kind
        )
        text = "\n".join(b.text for b in span_blocks if not b.is_furniture and b.text.strip())
        if not text.strip():
            continue
        vector = embed_long_text(
            text, chunk_words=settings.reuse_embedding_chunk_words, settings=settings
        )
        if vector is None:
            continue
        chapter_texts.append(text)
        chapter_embeddings.append(
            ChapterEmbedding(
                chapter_index=i,
                title=node.title,
                anchor=_anchor_for(node),
                page=node.page,
                paragraph=node.paragraph,
                embedding=vector,
            )
        )

    whole_document = None
    if chapter_texts:
        whole_document = embed_long_text(
            "\n".join(chapter_texts),
            chunk_words=settings.reuse_embedding_chunk_words,
            settings=settings,
        )
    elif not chapters:
        # No chapter structure detected at all (e.g. `section_tree.source
        # == "none"`, a scan or a document heuristics couldn't parse) —
        # fall back to every non-furniture, non-reference block so a
        # manuscript with a genuinely flat structure still gets SOME
        # archive coverage rather than silently opting out of F7 entirely.
        text = "\n".join(b.text for b in content_blocks if not b.is_furniture and b.text.strip())
        whole_document = embed_long_text(
            text, chunk_words=settings.reuse_embedding_chunk_words, settings=settings
        )

    return DocumentEmbeddings(
        model_id=settings.embedding_model_id,
        whole_document=whole_document,
        chapters=chapter_embeddings,
    )
