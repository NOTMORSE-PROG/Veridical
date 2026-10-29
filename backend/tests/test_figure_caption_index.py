"""Figure captions are found from per-page and per-paragraph indexes, so a
manuscript with tens of thousands of image fragments still ingests."""

import time

from app.config import get_settings
from app.ingest.image_source import prepare_figures
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult, ImageBlock, SectionTree, TextBlock


def _result(anchor_kind, blocks, images, page_count=0):
    return ExtractionResult(
        page_count=page_count,
        anchor_kind=anchor_kind,
        image_only=False,
        text_chars=sum(len(block.text) for block in blocks),
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=blocks,
        images=images,
    )


def _captions(result):
    prepare_figures(result, load_patterns(), get_settings())
    return [(reading.source_caption, reading.caption_anchor) for reading in result.figure_readings]


def _block(text, *, page=None, paragraph=None):
    return TextBlock(page=page, paragraph=paragraph, text=text, max_font_size=11, bold_ratio=0.0)


def test_page_images_take_the_one_caption_on_their_page():
    blocks = [
        _block("Figure 1. Booking flow", page=1),
        _block("Figure 2. Two charts share this page", page=2),
    ]
    images = [
        ImageBlock(page=1, bbox=(0, 0, 200, 200)),
        ImageBlock(page=2, bbox=(0, 0, 200, 200)),
        ImageBlock(page=2, bbox=(0, 300, 200, 500)),
    ]
    assert _captions(_result("page", blocks, images, page_count=2)) == [
        ("Figure 1. Booking flow", "page 1"),
        (None, None),
        (None, None),
    ]


def test_paragraph_images_look_ahead_only_until_the_next_image():
    blocks = [
        _block("Body text", paragraph=4),
        _block("Figure 1. The first diagram", paragraph=6),
        _block("Figure 2. Belongs to the second image", paragraph=9),
    ]
    images = [ImageBlock(paragraph=5), ImageBlock(paragraph=8)]
    assert _captions(_result("paragraph", blocks, images)) == [
        ("Figure 1. The first diagram", "paragraph 6"),
        ("Figure 2. Belongs to the second image", "paragraph 9"),
    ]


def test_tens_of_thousands_of_image_fragments_do_not_stall_ingest():
    pages = 400
    blocks = [_block(f"Text on page {page}", page=page) for page in range(1, pages + 1)]
    images = [
        ImageBlock(page=page, bbox=(index, 0, index + 1, 1))
        for page in range(1, pages + 1)
        for index in range(50)
    ]
    result = _result("page", blocks, images, page_count=pages)
    started = time.perf_counter()
    prepare_figures(result, load_patterns(), get_settings())
    assert time.perf_counter() - started < 5
    assert len(result.figure_readings) == len(images)
