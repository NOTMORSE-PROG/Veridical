"""V-072 (F7.4) unit tests: block-quote detection. PDF-only (bbox-based) —
see `app/ingest/blockquotes.py`'s own module docstring for why DOCX has no
signal at all. Synthetic `TextBlock`s with hand-set bbox x0, same
directness as `test_checks_reuse_embed.py`'s `_block` helper."""

from app.config import get_settings
from app.ingest.blockquotes import block_quote_block_ids
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock

SETTINGS = get_settings()


def _block(
    text: str, *, page: int | None = 1, x0: float = 72.0, paragraph: int | None = None
) -> TextBlock:
    if paragraph is not None:
        page = None
    return TextBlock(
        text=text,
        page=page,
        paragraph=paragraph,
        bbox=(x0, 100.0, x0 + 200.0, 120.0) if paragraph is None else None,
        max_font_size=11.0,
        bold_ratio=0.0,
    )


def _extraction(
    blocks: list[TextBlock], nodes: list[SectionNode], anchor_kind: str = "page"
) -> ExtractionResult:
    return ExtractionResult(
        page_count=max((b.page or 1) for b in blocks) if blocks else 1,
        anchor_kind=anchor_kind,
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="heuristics", nodes=nodes),
        blocks=blocks,
        images=[],
    )


def test_indented_long_block_is_detected_as_a_quote():
    body_x0 = 72.0
    quote_x0 = body_x0 + SETTINGS.block_quote_indent_threshold_points + 5
    heading = _block("Chapter 1: Introduction", page=1, x0=body_x0)
    body = _block("This is ordinary body text at the normal margin.", page=1, x0=body_x0)
    quote = _block(
        "This is a long indented passage that reads like a properly cited "
        "block quotation pulled directly from another source document.",
        page=1,
        x0=quote_x0,
    )
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, page=1)]
    extraction = _extraction([heading, body, quote], nodes)

    ids = block_quote_block_ids(extraction, SETTINGS)
    assert id(quote) in ids
    assert id(body) not in ids
    assert id(heading) not in ids


def test_short_indented_block_is_not_flagged():
    """Below `block_quote_min_words` -- a short indented line (e.g. a list
    item) is not worth excluding from F7.4 candidacy."""
    body_x0 = 72.0
    quote_x0 = body_x0 + SETTINGS.block_quote_indent_threshold_points + 5
    heading = _block("Chapter 1: Introduction", page=1, x0=body_x0)
    body = _block(
        "Ordinary body text at the normal margin, long enough on its own.", page=1, x0=body_x0
    )
    short_indent = _block("Short item.", page=1, x0=quote_x0)
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, page=1)]
    extraction = _extraction([heading, body, short_indent], nodes)

    ids = block_quote_block_ids(extraction, SETTINGS)
    assert id(short_indent) not in ids


def test_uniformly_indented_page_has_no_false_positives():
    """The body-margin MODE is computed per chapter, not a hardcoded left
    edge -- a chapter whose every block shares one indent (no accidental
    outlier) must flag nothing."""
    x0 = 90.0
    heading = _block("Chapter 1: Introduction", page=1, x0=x0)
    b1 = _block("First paragraph of real body content, written at length.", page=1, x0=x0)
    b2 = _block("Second paragraph of real body content, also written at length.", page=1, x0=x0)
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, page=1)]
    extraction = _extraction([heading, b1, b2], nodes)

    assert block_quote_block_ids(extraction, SETTINGS) == set()


def test_docx_has_no_bbox_signal_so_nothing_is_flagged():
    """Honest, named gap (module docstring): DOCX blocks carry no bbox at
    all, so this module never guesses at DOCX indentation."""
    heading = _block("Chapter 1: Introduction", paragraph=0)
    quote = _block(
        "A long paragraph that WOULD look like a block quote if this were "
        "a PDF with a real indent signal, but DOCX has none at all here.",
        paragraph=1,
    )
    nodes = [SectionNode(title="Chapter 1: Introduction", level=1, paragraph=0)]
    extraction = _extraction([heading, quote], nodes, anchor_kind="paragraph")

    assert block_quote_block_ids(extraction, SETTINGS) == set()
