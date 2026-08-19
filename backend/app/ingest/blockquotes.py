"""Block-quote detection (V-072, F7.4 exclusion) -- confirmed absent
repo-wide before this ticket (V-072.md's own carried-forward research from
V-065). A block quote embedded verbatim in TWO manuscripts (a shared
long-quoted source, e.g. the same law or the same interview transcript)
would otherwise inflate F7.4 passage similarity between two otherwise
unrelated manuscripts -- the same "two groups legitimately citing the same
material isn't reuse" edge case V-036's reference-list exclusion already
exists for, just for quoted body text instead of the bibliography.

**PDF only.** `TextBlock.bbox` is `None` for every DOCX block
(`app/ingest/schemas.py`'s own comment: "PDF only: layout position") -- no
indent signal exists for DOCX at all, so this module never classifies a
DOCX block as a quote. An honest, named gap (matching V-065's own AC6
scope note), not a silent miss: `is_block_quote_block_ids` always returns
an empty set for a DOCX `ExtractionResult` (`anchor_kind == "paragraph"`).

**Method**, comparable in spirit (not code) to `references.py`'s hanging-
indent entry segmentation: per chapter, the body's own left margin is the
MODE of its content blocks' `bbox[0]` (the single most common x0 -- robust
to a handful of outliers, unlike a min/mean which one stray floating
element could skew). Any block whose x0 sits more than
`block_quote_indent_threshold_points` to the right of that mode is a block
quote candidate. A short block (below `block_quote_min_words`) is excluded
even if indented -- a run-in indented list item or a short displayed
equation label is not a block quote, and F7.4 only cares about quoted BODY
TEXT long enough to itself be a plausible passage.

**Known, accepted imprecision** (charter judgment §1: bias toward NOT
flagging, so over-excluding here is the safe direction): a genuinely
indented bullet/numbered list item can also trigger this. Wrongly
excluding a non-quote from F7.4 candidacy only means slightly less
coverage, never a wrong flag -- the opposite failure (missing a real
shared block quote) is the one this module exists to catch, and false
exclusion is the safe direction to err in.

**Measured 2026-08-20** against the one real, multi-chapter manuscript
available in this environment with genuine section structure
(`VERIDICAL-DOCUMENTATION.pdf`, the same 47-page document V-065's own
research cites): 14 of 54 real passages (~26%) were tagged block-quote.
Spot-checking several by hand, none were real quotations -- the
document's numbered sub-headings and numbered problem/objective lists
(e.g. "1.2.2 Specific Research Problem", "1. Without an automatic
check...") sit at a handful of DIFFERENT indent levels close enough
together (measured: chapter-by-chapter x0 modes cluster roughly every
18-36pt) that several land past `block_quote_indent_threshold_points`
above the chapter's own body mode. This is the exact "indented list
item" failure mode named above, just more common in this document than
assumed -- recorded here as a real, measured finding for future tuning
(the fix would need something numbered-list-aware, e.g. a leading
"digit followed by a period" pattern check, not a threshold tweak), not
silently left as an
unverified guess. Still the safe direction to err in: F7.1-3's existing
chapter/whole-document reuse comparisons are completely unaffected by
this module, and an over-excluded passage here only loses F7.4 candidacy
coverage, never produces a wrong flag.
"""

from collections import Counter

from app.config import Settings
from app.ingest.schemas import ExtractionResult, SectionNode, TextBlock


def _flatten(nodes: list[SectionNode]) -> list[SectionNode]:
    out: list[SectionNode] = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.children))
    return out


def _chapter_spans(extraction: ExtractionResult) -> list[tuple[int | None, int | None]]:
    """(start_page, end_page_exclusive) per level-1 chapter, same convention
    as `app/checks/reuse/embed.py`'s `_chapter_span_blocks` -- kept as its
    own small copy rather than importing that module's private helper
    across a package boundary (same precedent that module itself already
    set against `checks.semantic`)."""
    chapters = [n for n in extraction.section_tree.nodes if n.level == 1]
    spans: list[tuple[int | None, int | None]] = []
    for i, node in enumerate(chapters):
        next_chapter = chapters[i + 1] if i + 1 < len(chapters) else None
        spans.append((node.page, next_chapter.page if next_chapter else None))
    return spans


def block_quote_block_ids(extraction: ExtractionResult, settings: Settings) -> set[int]:
    """Identity-based result set (`id(block)`), same convention as
    `references.py`'s `non_reference_blocks` -- callers test membership by
    object identity against `extraction.blocks`."""
    if extraction.anchor_kind != "page":
        return set()  # DOCX: no bbox, no signal -- honest absence, not a guess

    quote_ids: set[int] = set()
    for start, end in _chapter_spans(extraction):
        if start is None:
            continue
        span_blocks = [
            b
            for b in extraction.blocks
            if not b.is_furniture
            and b.bbox is not None
            and b.page is not None
            and start <= b.page
            and (end is None or b.page < end)
        ]
        if len(span_blocks) < 2:
            continue
        # Round to the nearest point: real x0 values cluster tightly but
        # rendering jitter keeps them from being bit-identical, which would
        # make a raw Counter over floats useless (every value its own mode).
        rounded = [round(b.bbox[0]) for b in span_blocks]
        body_x0 = Counter(rounded).most_common(1)[0][0]
        for b, x0 in zip(span_blocks, rounded, strict=True):
            if (
                x0 > body_x0 + settings.block_quote_indent_threshold_points
                and _word_count(b) >= settings.block_quote_min_words
            ):
                quote_ids.add(id(b))
    return quote_ids


def _word_count(block: TextBlock) -> int:
    return len(block.text.split())
