"""V-081: compose a mode notice outside the unchanged submitted-page raster."""

import json
from dataclasses import dataclass
from functools import lru_cache
from math import ceil, inf, nextafter
from pathlib import Path

import pymupdf

from app.config import Settings


@lru_cache
def disclosure_layout() -> dict:
    return json.loads(
        (Path(__file__).parent / "data" / "page_image_disclosure.json").read_text("utf-8")
    )


class PageImageUnavailable(ValueError):
    """A bounded image could not retain both source evidence and a readable notice."""


def check_pixel_budget(width: int, height: int, settings: Settings) -> None:
    if width * height > settings.export_page_image_max_pixels:
        raise PageImageUnavailable(disclosure_layout()["unavailable"]["pixel_limit"])


@dataclass(frozen=True)
class _Band:
    height_px: int
    font_pt: float
    lines: tuple[str, ...]


def _wrap(text: str, font: pymupdf.Font, size: float, width: float) -> tuple[str, ...] | None:
    lines: list[str] = []
    current = ""
    for word in text.split():
        if font.text_length(word, fontsize=size) > width:
            return None
        candidate = f"{current} {word}" if current else word
        if current and font.text_length(candidate, fontsize=size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return tuple(lines)


def _fit_band(
    width: int, height: int, text: str, content_width: float, settings: Settings
) -> _Band:
    layout = disclosure_layout()
    font = pymupdf.Font(layout["font"])
    leading = max(layout["line_height_em"], font.ascender - font.descender)
    inset = layout["inset_em"]
    q = 72 / settings.export_page_image_dpi
    w, h = width * q, height * q
    minimum = settings.export_page_disclosure_font_pt
    maximum_height = settings.export_page_image_max_height

    # There can be no more wrapped lines than words. For each candidate,
    # solve f * M / (h + A*f + one raster pixel) >= the desired font size.
    # The extra pixel covers ceil() when allocating the band. Width and
    # native-size constraints are solved at the same time. No retry loop
    # or progressively larger render is needed.
    for count in range(1, len(text.split()) + 1):
        a = count * leading + 2 * inset
        denominator = maximum_height - minimum * a
        if denominator <= 0:
            break
        size = nextafter(
            max(minimum, minimum * w / content_width, minimum * (h + q) / denominator), inf
        )
        # Quantize upward to a whole raster-pixel font size. This preserves
        # the lower bound through PDF font serialization and float rounding.
        size = ceil(size / q) * q
        band_height = ceil(a * size / q)
        check_pixel_budget(width, height + band_height, settings)
        scale = min(content_width / w, maximum_height / ((height + band_height) * q), 1.0)
        lines = _wrap(text, font, size, w - 2 * inset * size)
        if lines and len(lines) <= count and size * scale >= minimum:
            return _Band(band_height, size, lines)

    raise PageImageUnavailable(layout["unavailable"]["disclosure_fit"])


def append_disclosure(
    source: pymupdf.Pixmap,
    text: str,
    fg_rgb: tuple[float, float, float],
    bg_rgb: tuple[float, float, float],
    *,
    content_width: float,
    settings: Settings,
) -> bytes:
    """Copy source pixels verbatim, followed by a separately rendered notice.

    All measurements use the actual raster dimensions and the same width,
    height and native-size limits as the PDF image flowable.
    """
    band = _fit_band(source.width, source.height, text, content_width, settings)
    layout = disclosure_layout()
    font = pymupdf.Font(layout["font"])
    q = 72 / settings.export_page_image_dpi
    size = band.font_pt / q
    inset = layout["inset_em"] * size
    leading = max(layout["line_height_em"], font.ascender - font.descender) * size

    # Pixel coordinates at matrix=1 give exactly W x B band pixels: no
    # second DPI rounding can push the final effective font below its minimum.
    with pymupdf.open() as band_doc:
        page = band_doc.new_page(width=source.width, height=band.height_px)
        page.draw_rect(
            page.rect, color=None, fill=bg_rgb, fill_opacity=layout["background_opacity"]
        )
        for index, line in enumerate(band.lines):
            x = (source.width - font.text_length(line, fontsize=size)) / 2
            y = inset + font.ascender * size + index * leading
            page.insert_text((x, y), line, fontname=layout["font"], fontsize=size, color=fg_rgb)
        rendered_band = page.get_pixmap(matrix=pymupdf.Identity, annots=True)

    if (rendered_band.width, rendered_band.height) != (source.width, band.height_px):
        raise PageImageUnavailable(layout["unavailable"]["disclosure_fit"])
    combined = pymupdf.Pixmap(
        source.colorspace, (0, 0, source.width, source.height + band.height_px), source.alpha
    )
    combined.clear_with(255)
    source.set_origin(0, 0)
    combined.copy(source, source.irect)
    rendered_band.set_origin(0, source.height)
    combined.copy(rendered_band, rendered_band.irect)
    combined.set_dpi(settings.export_page_image_dpi, settings.export_page_image_dpi)
    return combined.tobytes("png")
