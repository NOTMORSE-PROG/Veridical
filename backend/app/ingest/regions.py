"""Anchor-to-region recovery (V-065 Q2, shared with V-070 per both tickets'
own Q5 — whichever ships first extracts it for the other; V-065 shipped it
first).

`Flag.page_anchor` is a free-form display string, not a structured
locator (confirmed: no character offsets, no bbox, no anchor-kind
discriminator anywhere in the schema). This module classifies that string
and recovers an actual page/bbox from the manuscript's stored PDF and
`section_tree`, so a viewer can highlight it or an export can crop it.

**Measured 2026-08-19 against 34 real flags on the real 47-page manuscript
that both V-065 and V-070 cite as "the audit's case"
(`tickets/V8-real-use/open/V-065.md` Pre-implementation research Q2) —
the hit rate is genuinely bimodal by flag kind, not a single percentage:**
`page.search_for()` recovers an exact bounding box for every flag whose
`evidence_excerpt` is real quoted prose (13/13 measured), and recovers
nothing for flags whose excerpt is a synthesized/computed summary that was
never on the page as written (0/12 measured, e.g. a statistical-forensics
flag's "n=5, M=4.20, SD=0.37"). That miss is not a bug in this module —
the text genuinely isn't there — so callers MUST handle `page_only` and
`unavailable` as real, honest outcomes and never fabricate a highlight box
where none is recoverable (charter judgment §1: an unverifiable-looking
flag is safer than a falsely-precise one).

A multi-line section heading also does not match `search_for()` as one
phrase (measured: a heading that visually wraps across two lines matched
each line separately but not as a combined string) — irrelevant here
because `section`/`whole_document` regions are resolved from
`section_tree`'s own recorded page number, never by searching the heading
text on the page.

**Kind names match the `ui-designer` V-065 spec's `AnchorRegion` contract
(2026-08-19) exactly, with one deliberate narrowing:** the spec's
`paragraph_bbox` (a character-offset span within a DOCX paragraph) is not
built here — DOCX resolves to `paragraph_only` only. Precise in-paragraph
offsets need the raw extraction store's paragraph text at view time
(`load_raw_store`, per the spec's own Q2.3), which is real additional
scope beyond this session's measured research; `paragraph_only` is still
an honest, useful result (jump to the right paragraph), not a placeholder.
"""

from dataclasses import dataclass
from typing import Literal

import pymupdf

from app.config import Settings, get_settings
from app.ingest.normalize import match_key, normalize
from app.ingest.patterns import load_patterns
from app.ingest.schemas import SectionNode, SectionTree

RegionKind = Literal[
    "page_bbox",
    "page_only",
    "reference_list",
    "reference_position",
    "section",
    "whole_document",
    "paragraph_only",
    "unavailable",
]

Bbox = tuple[float, float, float, float]


@dataclass(frozen=True)
class AnchorRegion:
    """What a viewer/export can do with one flag's anchor, never more than
    what was actually recovered:
    - `page_bbox`: precise highlight(s) on `page`, coordinates in
      `all_bboxes`. PDF sources only.
    - `page_only`: the flag names a real page, but no box on it — jump
      there, don't draw a false-precision rectangle. PDF sources only.
    - `reference_list`: points into the reference list, at `page` (coarse
      — the list's own page, not a specific entry's page; per-entry lookup
      would need re-parsing the reference-list segmentation at view time,
      not built here). PDF sources only — DOCX degrades to `paragraph_only`.
    - `reference_position`: same as `reference_list`, plus `index` (the
      1-based entry number the anchor named) — still only the list's page,
      never a specific entry's own location.
    - `section`: a chapter-duplicate flag naming its own chapter title;
      `page`..`end_page` is the chapter's span. PDF sources only.
    - `whole_document`: an F7 whole-manuscript duplicate flag; the entire
      document. PDF: `page` 1 to `end_page` = last page. DOCX: no page
      concept, both stay `None` — the viewer highlights the whole text
      pane, not a numbered range.
    - `paragraph_only`: DOCX sources (no PDF.js pane, no pages at all —
      decided 2026-08-19, V-065 Q1) — points at a paragraph index in the
      extracted-text pane. Never a bbox; there is no rendered page to box.
    - `unavailable`: nothing recoverable — the anchor names something (a
      page out of range, a heading with no page, a manuscript with no
      reference section) this module can't locate. Never invented. The
      manuscript-viewer caller also uses this same kind for a purged
      manuscript's flags (there is no file at all in that case) — one
      terminal state, one honest copy path on the frontend.
    """

    kind: RegionKind
    page: int | None = None
    end_page: int | None = None
    bbox: Bbox | None = None
    all_bboxes: tuple[Bbox, ...] = ()
    paragraph: int | None = None
    index: int | None = None


def _flatten(nodes: list[SectionNode]) -> list[SectionNode]:
    out: list[SectionNode] = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.children))
    return out


def _classify(page_anchor: str) -> tuple[str, str | int | None]:
    anchor = page_anchor.strip()
    if anchor.startswith("p. ") and anchor[3:].strip().isdigit():
        return "page", int(anchor[3:].strip())
    if anchor.startswith("¶") and anchor[1:].strip().isdigit():
        return "paragraph", int(anchor[1:].strip())
    if anchor == "reference list":
        return "reference", None
    if anchor.startswith("reference #") and anchor[len("reference #") :].strip().isdigit():
        return "reference", int(anchor[len("reference #") :].strip())
    if anchor == "whole document":
        return "whole_document", None
    return "section_title", anchor


def _search_excerpt(page: pymupdf.Page, excerpt: str, settings: Settings) -> list[Bbox]:
    """Returns boxes in RAW PDF point space (origin bottom-left, y increasing
    upward) -- the space pdf.js's `PageViewport.convertToViewportPoint`
    expects as input (its own JSDoc: "Converts PDF point to viewport
    coordinates"). `page.search_for()` returns MuPDF's own coordinate
    convention instead (origin TOP-left, y increasing downward -- verified
    empirically: inserting text at y=100 on an 842pt-tall page produces a
    ~y=90-103 rect, not ~y=739-752), so every box is flipped here via
    `page.rect.height` before being returned. Found live (`ux-critic`,
    V-065 review, 2026-08-19): without this flip, every highlight rendered
    roughly one text-line off from its real target -- close enough to look
    plausible, wrong enough to mislead."""
    min_chars = settings.region_search_min_candidate_chars
    page_height = page.rect.height
    for length in settings.region_search_candidate_lengths_list:
        candidate = excerpt.strip()[:length].strip().rstrip(".")
        if len(candidate) < min_chars:
            continue
        hits = page.search_for(candidate)
        if hits:
            return [(r.x0, page_height - r.y1, r.x1, page_height - r.y0) for r in hits]
    return []


def _section_span(node: SectionNode, flat: list[SectionNode]) -> tuple[int | None, int | None]:
    idx = flat.index(node)
    successor = next((n for n in flat[idx + 1 :] if n.level <= node.level), None)
    end = None
    if successor is not None and successor.page is not None and node.page is not None:
        end = max(node.page, successor.page - 1)
    return node.page, end


def recover_region(
    doc: pymupdf.Document | None,
    section_tree: SectionTree,
    page_anchor: str,
    evidence_excerpt: str,
    *,
    settings: Settings | None = None,
) -> AnchorRegion:
    """`doc` is None for DOCX manuscripts (no PDF.js pane, decided
    2026-08-19 V-065 Q1 — see the module docstring) — every branch below
    that would need to open a page degrades to `paragraph_only`/
    `whole_document` instead of raising."""
    settings = settings or get_settings()
    kind, value = _classify(page_anchor)
    flat = _flatten(section_tree.nodes)

    if kind == "paragraph":
        para = value if isinstance(value, int) else None
        if para is None:
            return AnchorRegion(kind="unavailable")
        return AnchorRegion(kind="paragraph_only", paragraph=para)

    if kind == "page":
        if doc is None:
            return AnchorRegion(kind="unavailable")
        page_no = value
        if not isinstance(page_no, int) or not (1 <= page_no <= doc.page_count):
            return AnchorRegion(kind="unavailable")
        boxes = _search_excerpt(doc[page_no - 1], evidence_excerpt, settings)
        if boxes:
            return AnchorRegion(
                kind="page_bbox", page=page_no, bbox=boxes[0], all_bboxes=tuple(boxes)
            )
        return AnchorRegion(kind="page_only", page=page_no)

    if kind == "reference":
        patterns = load_patterns(settings.ingest_patterns_file)
        node = next(
            (n for n in flat if normalize(n.title).casefold() in patterns.reference_titles),
            None,
        )
        if node is None:
            return AnchorRegion(kind="unavailable")
        index = value if isinstance(value, int) else None
        if doc is not None and node.page is not None:
            if index is not None:
                return AnchorRegion(kind="reference_position", page=node.page, index=index)
            return AnchorRegion(kind="reference_list", page=node.page)
        if doc is None and node.paragraph is not None:
            return AnchorRegion(kind="paragraph_only", paragraph=node.paragraph)
        return AnchorRegion(kind="unavailable")

    if kind == "whole_document":
        if doc is not None:
            return AnchorRegion(kind="whole_document", page=1, end_page=doc.page_count)
        return AnchorRegion(kind="whole_document")

    # section_title: an F7 chapter-duplicate flag naming the matched
    # chapter's own heading text as its anchor (app/checks/reuse/service.py).
    want = match_key(str(value))
    node = next((n for n in flat if match_key(n.title) == want), None)
    if node is None:
        return AnchorRegion(kind="unavailable")
    if doc is not None and node.page is not None:
        start, end = _section_span(node, flat)
        return AnchorRegion(kind="section", page=start, end_page=end)
    if doc is None and node.paragraph is not None:
        return AnchorRegion(kind="paragraph_only", paragraph=node.paragraph)
    return AnchorRegion(kind="unavailable")
