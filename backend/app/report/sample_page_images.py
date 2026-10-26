"""V086: isolated reuse of the committed resource-capped native image renderer.

Appends notice pixels outside the source; never paints on a manuscript page.
"""

import json
from math import ceil
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4

from app.config import Settings
from app.report.export import _TONE_COLORS
from app.report.page_image_disclosure import (
    append_disclosure,
    check_pixel_budget,
    disclosure_layout,
)
from app.report.page_images import _merge_line_boxes, _to_mupdf_rect

Bbox = tuple[float, float, float, float]
_PRINT_LAYOUT = json.loads(
    (Path(__file__).parent / "data/sample_evidence_print_layout.json").read_text()
)


def _render_page_png(
    page: pymupdf.Page,
    boxes: tuple[Bbox, ...],
    *,
    llm_disclosure: tuple[str, str] | None,
    settings: Settings,
) -> bytes:
    """Render the visible source unchanged, with an optional external notice.

    Only finding marks shared by all consumers are added. The original page
    is rasterized with its own layer/annotation state, then marks are drawn
    directly on those pixels. No page copy can lose document-level visibility.
    """
    raster_rect = (
        page.rect
        * pymupdf.Matrix(settings.export_page_image_dpi / 72, settings.export_page_image_dpi / 72)
    ).irect
    check_pixel_budget(raster_rect.width, raster_rect.height, settings)
    source = page.get_pixmap(
        dpi=settings.export_page_image_dpi, colorspace=pymupdf.csRGB, alpha=False, annots=True
    )
    check_pixel_budget(source.width, source.height, settings)
    if boxes:
        merged = _merge_line_boxes(boxes, settings.export_box_line_merge_tolerance)
        box_color = _TONE_COLORS["danger"][1]
        color = tuple(round(c * 255) for c in (box_color.red, box_color.green, box_color.blue))
        dpi_scale = settings.export_page_image_dpi / 72
        stroke = ceil(disclosure_layout()["highlight_stroke_pt"] * dpi_scale)
        transform = page.rotation_matrix * pymupdf.Matrix(dpi_scale, dpi_scale)
        for bbox in merged:
            # Recovered coordinates are unrotated. Map into the same visible
            # viewport as get_pixmap, then clip each border through set_rect.
            rect = _to_mupdf_rect(bbox, page.rect.height) * transform
            border = (rect + (-stroke / 2, -stroke / 2, stroke / 2, stroke / 2)).irect
            for edge in (
                (border.x0, border.y0, border.x1, border.y0 + stroke),
                (border.x0, border.y1 - stroke, border.x1, border.y1),
                (border.x0, border.y0, border.x0 + stroke, border.y1),
                (border.x1 - stroke, border.y0, border.x1, border.y1),
            ):
                source.set_rect(edge, color)
    if llm_disclosure is None:
        return source.tobytes("png")
    text, tone = llm_disclosure
    fg, bg = _TONE_COLORS[tone][1], _TONE_COLORS[tone][0]
    return append_disclosure(
        source,
        text,
        (fg.red, fg.green, fg.blue),
        (bg.red, bg.green, bg.blue),
        content_width=A4[0] - 2 * _PRINT_LAYOUT["side_margin"],
        settings=settings,
    )
