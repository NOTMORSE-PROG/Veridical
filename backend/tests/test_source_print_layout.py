"""Actual source flowable sizing/splitting; no Canvas, PDF or file creation."""

import struct
import zlib
from types import SimpleNamespace as Row

import pytest
from reportlab.platypus import PTOContainer, Table
from reportlab.platypus.flowables import Flowable, KeepTogether

from app.config import Settings
from app.report.evidence_captions import TEXT_LAYER_CAPTION
from app.report.export import _PRINT_LAYOUT, _register_fonts, _styles
from app.report.page_image_disclosure import PageImageUnavailable
from app.report.schemas import PageEvidenceImageOut, SourceEvidencePrintOut
from app.report.source_print import source_evidence_flowables, source_image_height


def png():
    """Small, valid white raster fixture in RAM, independently of PDF tooling."""

    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 640, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * 96) * 640))
        + chunk(b"IEND", b"")
    )


class MeasuredBlock(Flowable):
    width = 400

    def __init__(self, height):
        self.height = height

    def wrap(self, available_width, available_height):
        return self.width, self.height


def canvas_stub():
    return Row(_doctemplate=Row(), _fontname="Helvetica", _fontsize=10, _leading=12)


def source_parts(*, frame_height=None, quote="A complete saved source quotation."):
    styles = _styles(_register_fonts())
    entry = SourceEvidencePrintOut(
        reference_id="a" * 64,
        criterion_id=21,
        flag_id=None,
        role="criterion_evidence",
        index=0,
        quote=quote,
        anchor="page 2",
        placement_state="text_layer_match",
        max_print_height=500,
        image=PageEvidenceImageOut(image_png=png(), page=2, reason=TEXT_LAYER_CAPTION),
    )
    return source_evidence_flowables(
        entry, 450, styles, 72, _PRINT_LAYOUT, frame_height=frame_height
    )


def group():
    parts = source_parts()
    return next(part for part in parts if isinstance(part, Table))


def test_source_image_group_reports_true_height_and_keeps_caption_in_current_frame():
    block = group()
    canvas = canvas_stub()
    width, height = block.wrapOn(canvas, 450, 650)
    assert width == 450 and 500 < height < 600
    outer = PTOContainer([MeasuredBlock(30), block], header=[MeasuredBlock(11)])
    assert outer.wrapOn(canvas, 450, 650) == (450, 30 + height)
    # Discriminating historical control: the old wrapper lies about height,
    # even though this exact group and its caption fit in the available frame.
    old = PTOContainer([MeasuredBlock(30), KeepTogether(list(block._cellvalues[0][0]))])
    assert old.wrapOn(canvas_stub(), 450, 650)[1] > 1_000_000


def test_source_group_moves_whole_to_continuation_without_an_extra_frame_break():
    block = group()
    canvas = canvas_stub()
    _, height = block.wrapOn(canvas, 450, 650)
    remaining = height - 5
    assert block.splitOn(canvas, 450, remaining) == []
    outer = PTOContainer([MeasuredBlock(30), block], header=[MeasuredBlock(11)])
    outer.wrapOn(canvas, 450, remaining)
    fragments = outer.splitOn(canvas, 450, remaining)
    assert [type(part).__name__ for part in fragments] == [
        "MeasuredBlock",
        "UseUpSpace",
        "PTOContainer",
    ]
    assert fragments[-1]._content[-1] is block
    assert fragments[-1].wrapOn(canvas, 450, 650) == (450, height + 11)


def test_impossible_caption_budget_rejects_image_instead_of_shrinking_notice():
    ref = Row(
        criterion_id=21,
        owner_kind="criterion",
        owner_id=11,
        role="criterion_evidence",
        index=0,
        anchor="oversized source location " * 1000,
    )
    with pytest.raises(PageImageUnavailable, match="required caption"):
        source_image_height(
            ref, 450, 600, _styles(_register_fonts()), _PRINT_LAYOUT, Settings(_env_file=None)
        )


def test_short_quote_moves_with_its_image_when_the_complete_entry_fits_fresh_page():
    parts = source_parts(frame_height=650)
    assert len(parts) == 1 and isinstance(parts[0], Table)
    complete = parts[0]
    canvas = canvas_stub()
    height = complete.wrapOn(canvas, 450, 650)[1]
    assert 500 < height < 639
    outer = PTOContainer([MeasuredBlock(100), complete], header=[MeasuredBlock(11)])
    outer.wrapOn(canvas, 450, height + 50)
    fragments = outer.splitOn(canvas, 450, height + 50)
    assert [type(part).__name__ for part in fragments] == [
        "MeasuredBlock",
        "UseUpSpace",
        "PTOContainer",
    ]
    assert fragments[-1]._content[-1] is complete
    assert fragments[-1].wrapOn(canvas, 450, 650)[1] == height + 11


def test_genuinely_long_quote_remains_splittable_outside_image_group():
    parts = source_parts(frame_height=650, quote="A long saved quotation. " * 300)
    assert len(parts) > 1 and isinstance(parts[0], Flowable)
    quote = parts[1]
    assert quote.wrap(450, 650)[1] > 650
    assert any(isinstance(part, Table) for part in parts[2:])
