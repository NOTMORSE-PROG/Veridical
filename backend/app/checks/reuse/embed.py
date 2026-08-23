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

import re
from dataclasses import dataclass
from typing import Literal

from app.config import Settings, get_settings
from app.ingest.blockquotes import block_quote_block_ids
from app.ingest.normalize import normalize
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
        # BUG-114: a chapter whose OWN heading is the references/bibliography
        # title (a real, common section-tree shape, not a contrived one) has
        # every real entry excluded by `non_reference_blocks` above, but the
        # heading block itself survives (`_reference_span` only excludes
        # blocks STRICTLY BETWEEN the heading and the next one) -- so the
        # chapter's "content" degenerates to the single word "References".
        # Any two manuscripts with this shape then produce a byte-identical
        # chapter text and a false 1.0-similarity match. Same title-matching
        # mechanism `references.py::_reference_span` already uses to FIND
        # this heading -- skip the chapter entirely rather than embed a
        # heading-only span.
        if normalize(node.title).casefold() in patterns.reference_titles:
            continue
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


# --- Passage-level embedding (V-072, F7.4) ----------------------------------
#
# A deliberately separate pipeline from `compute_document_embeddings` above,
# not a parameter added to it: F7.1-7.3's whole-doc/chapter vectors already
# EXCLUDE reference-list text entirely (`non_reference_blocks`, module
# docstring), because at that granularity there is no reason to keep it.
# F7.4 needs the opposite — reference-list and block-quote passages must
# still be EMBEDDED and STORED (tagged, not dropped), because the exclusion
# toggle (ticket AC7) needs something real to reveal when switched off. Two
# genuinely different inclusion policies, so two functions; touching
# `compute_document_embeddings` to serve both would risk the F7.1-3 tests
# this module already carries.
#
# **No chapter structure -> no passages at all** (`section_tree.source ==
# "none"`): an honest, named scope limit for this ticket, not a silent
# gap — F7.1's whole-document fallback (this module, above) still runs for
# such a manuscript; only the finer F7.4 granularity is unavailable, same
# spirit as V-065's own AC6 split (build what's real, name what isn't).


@dataclass(frozen=True)
class PassageEmbedding:
    passage_index: int
    chapter_index: int
    anchor_kind: Literal["page", "paragraph"]
    page: int | None
    paragraph: int | None
    # Offsets within the CHAPTER's own assembled text (the same
    # "\n".join(block texts) basis `_chapter_span_blocks` already produces
    # for chapter-level embedding) — not the whole document, and not a
    # byte offset into the original file. Real, but scoped to what this
    # ticket actually needs: locating a passage relative to its own
    # chapter, and slicing `context_text` around it.
    char_start: int
    char_end: int
    text: str  # the passage itself, bounded to ~reuse_passage_chunk_words
    # Passage + up to `reuse_passage_context_words` on each side, computed
    # and bounded HERE, once, at archive-build time — never re-derived from
    # a live file read (the bounded-excerpt rule applies to the MATCHED
    # side too, and this is what makes that side never need file access at
    # all; see `app.report.service`'s passage-pair assembly).
    context_text: str
    is_reference_list: bool
    is_block_quote: bool
    embedding: list[float]


def _word_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in re.finditer(r"\S+", text)]


def _word_chunk_offsets(text: str, chunk_words: int) -> list[tuple[int, int]]:
    """Splits one block's text into `chunk_words`-sized pieces by WORD
    count, returning each piece's (start, end) char offset — only reached
    for a single block far larger than a normal passage (twice
    `chunk_words`); the common case chunks at block boundaries instead
    (see `_build_passages`), which keeps every passage's anchor a real
    block rather than a mid-paragraph word slice."""
    spans = _word_spans(text)
    if not spans:
        return []
    return [
        (spans[i][0], spans[min(i + chunk_words, len(spans)) - 1][1])
        for i in range(0, len(spans), chunk_words)
    ]


def split_context(context_text: str, excerpt: str) -> tuple[str | None, str | None]:
    """Inverse of `_add_context` below: splits a stored, bounded
    `context_text` back into (before, after) around `excerpt` — used by
    both `app.flags.service` (a real passage flag) and
    `app.report.service` (the excluded-match exploration endpoint) to
    render the passage itself distinctly from its surrounding context.
    `excerpt` is always a real substring of `context_text` by
    construction (both were sliced from the same source text), so this
    only ever splits around it, never guesses. Empty before/after
    (passage at its chapter's own start/end) becomes `None`, not an empty
    string — an honest absence, not a placeholder callers would have to
    special-case against "" too."""
    idx = context_text.find(excerpt)
    if idx == -1:
        return None, None
    before = context_text[:idx].strip() or None
    after = context_text[idx + len(excerpt) :].strip() or None
    return before, after


def _add_context(full_text: str, char_start: int, char_end: int, context_words: int) -> str:
    """Widens [char_start, char_end) by up to `context_words` real words on
    each side, never past the chapter's own text — the bounded window the
    ticket's `reuse_passage_context_words` setting controls."""
    before_words = _word_spans(full_text[:char_start])
    after_words = _word_spans(full_text[char_end:])
    # `backend-critic` finding (V-072 review, 2026-08-20): `-context_words`
    # as a Python negative index is wrong at `context_words == 0` --
    # `-0 == 0`, so `before_words[-0]` was the FIRST preceding word, not
    # "none", silently including ALL preceding text instead of nothing for
    # an operator who configures a zero-word window. Dormant at the
    # default (60) but this function IS the bounded-excerpt mechanism the
    # owner's Branch B ruling requires, so a config value producing the
    # opposite of what it says is a real latent correctness bug, not a
    # cosmetic one. Written as three explicit branches instead of Python's
    # negative-index trick so `context_words == 0` can never resurface.
    if context_words <= 0:
        pre_start = char_start
    elif len(before_words) <= context_words:
        pre_start = 0
    else:
        pre_start = before_words[len(before_words) - context_words][0]
    kept_after = after_words[:context_words]
    post_end = char_end + kept_after[-1][1] if kept_after else char_end
    return full_text[pre_start:post_end]


def _build_passages(
    span_blocks: list[TextBlock],
    *,
    chunk_words: int,
    ref_ids: set[int],
    quote_ids: set[int],
) -> list[dict]:
    """Groups consecutive blocks into ~`chunk_words`-sized passages. Chunks
    at BLOCK boundaries (not raw word-slicing across a block join) so every
    passage's char offsets and anchor stay aligned to a real block — the
    one exception is a single block alone far exceeding `chunk_words`
    (a giant undifferentiated paragraph), split by word count via
    `_word_chunk_offsets` so one block can't produce one unbounded
    "passage." Passage sizes vary somewhat around `chunk_words` rather
    than being exact — acceptable: F7.4 needs a real, checkable span, not
    an exact word count."""
    drafts: list[dict] = []
    pos = 0
    buffer_blocks: list[TextBlock] = []
    buffer_start = 0

    def flush() -> None:
        if not buffer_blocks:
            return
        text = "\n".join(b.text for b in buffer_blocks)
        anchor_block = buffer_blocks[0]
        drafts.append(
            {
                "text": text,
                "char_start": buffer_start,
                "char_end": buffer_start + len(text),
                "page": anchor_block.page,
                "paragraph": anchor_block.paragraph,
                "is_reference_list": any(id(b) in ref_ids for b in buffer_blocks),
                "is_block_quote": any(id(b) in quote_ids for b in buffer_blocks),
            }
        )

    for b in span_blocks:
        text = b.text
        start = pos
        pos += len(text) + 1  # +1 accounts for the "\n" join separator
        words = len(text.split())

        if words > chunk_words * 2:
            flush()
            buffer_blocks = []
            for sub_start, sub_end in _word_chunk_offsets(text, chunk_words):
                drafts.append(
                    {
                        "text": text[sub_start:sub_end],
                        "char_start": start + sub_start,
                        "char_end": start + sub_end,
                        "page": b.page,
                        "paragraph": b.paragraph,
                        "is_reference_list": id(b) in ref_ids,
                        "is_block_quote": id(b) in quote_ids,
                    }
                )
            continue

        if not buffer_blocks:
            buffer_start = start
        buffer_blocks.append(b)
        if sum(len(bb.text.split()) for bb in buffer_blocks) >= chunk_words:
            flush()
            buffer_blocks = []

    flush()
    return drafts


def compute_passage_embeddings(
    extraction: ExtractionResult, settings: Settings | None = None
) -> list[PassageEmbedding]:
    """F7.4's chunker: passage-level vectors for every chapter's content,
    INCLUDING reference-list and block-quote passages (tagged, not
    dropped — see module note above). One batched `embed_texts` call for
    the whole manuscript, not one call per passage."""
    settings = settings or get_settings()
    patterns = load_patterns(settings.ingest_patterns_file)
    content_blocks = [b for b in extraction.blocks if not b.is_furniture]
    non_ref_ids = {id(b) for b in non_reference_blocks(extraction, patterns)}
    ref_ids = {id(b) for b in content_blocks if id(b) not in non_ref_ids}
    quote_ids = block_quote_block_ids(extraction, settings)

    chapters = [n for n in extraction.section_tree.nodes if n.level == 1]
    drafts: list[dict] = []
    texts: list[str] = []

    for chapter_idx, node in enumerate(chapters):
        # BUG-114 (passage-level extension, backend-critic finding on this
        # ticket's own review): `ref_ids` above is computed the same way
        # `non_reference_blocks` is -- it never includes the references
        # HEADING block itself, only real entries below it. When a
        # References chapter has zero real entries left after that (e.g. an
        # extraction gap, or the section is genuinely empty), the heading
        # forms its own standalone passage, untagged (`is_reference_list`
        # False), so F7.4's default exclusion never catches it -- the exact
        # false-1.0-similarity mechanism BUG-114 fixed at chapter level,
        # reproduced at passage level under a narrower precondition BUG-114's
        # own regression fixture (which always includes a real reference
        # entry) never exercised. Unlike the chapter-level fix, this chapter
        # must still be EMBEDDED and STORED (F7.4's whole point: the
        # exclusion toggle needs real, revealable content) -- so instead of
        # skipping the chapter, every passage built from a chapter whose OWN
        # heading matches `patterns.reference_titles` is forced
        # `is_reference_list=True`, whether or not its particular blocks
        # individually fell inside `_reference_span`'s narrower window.
        chapter_is_reference_list = normalize(node.title).casefold() in patterns.reference_titles
        next_chapter = chapters[chapter_idx + 1] if chapter_idx + 1 < len(chapters) else None
        span_blocks = [
            b
            for b in _chapter_span_blocks(
                node, next_chapter, content_blocks, extraction.anchor_kind
            )
            if b.text.strip()
        ]
        if not span_blocks:
            continue
        full_chapter_text = "\n".join(b.text for b in span_blocks)
        for draft in _build_passages(
            span_blocks,
            chunk_words=settings.reuse_passage_chunk_words,
            ref_ids=ref_ids,
            quote_ids=quote_ids,
        ):
            if chapter_is_reference_list:
                draft["is_reference_list"] = True
            draft["chapter_index"] = chapter_idx
            draft["context_text"] = _add_context(
                full_chapter_text,
                draft["char_start"],
                draft["char_end"],
                settings.reuse_passage_context_words,
            )
            drafts.append(draft)
            texts.append(draft["text"])

    if not texts:
        return []

    vectors = embed_texts(texts, settings)
    return [
        PassageEmbedding(
            passage_index=i,
            chapter_index=draft["chapter_index"],
            anchor_kind=extraction.anchor_kind,
            page=draft["page"],
            paragraph=draft["paragraph"],
            char_start=draft["char_start"],
            char_end=draft["char_end"],
            text=draft["text"],
            context_text=draft["context_text"],
            is_reference_list=draft["is_reference_list"],
            is_block_quote=draft["is_block_quote"],
            embedding=vector,
        )
        for i, (draft, vector) in enumerate(zip(drafts, vectors, strict=True))
    ]
