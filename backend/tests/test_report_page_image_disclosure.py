"""V-081 / BUG-437: mode notices must preserve the visible submitted evidence."""

from math import inf, nextafter
from pathlib import Path

import pymupdf
import pytest

from app.config import get_settings
from app.report import export
from app.report.export import _LLM_MODE_DISCLOSURE
from app.report.page_image_disclosure import PageImageUnavailable
from app.report.page_images import _render_page_png
from app.report.schemas import PageEvidenceImageOut

_SOURCE = Path(__file__).parent / "fixtures" / "ingest" / "native.pdf"
_BOTTOM_SENTENCE = "Visible evidence at the bottom of the submitted page."
_LONG_TEST_NOTICE = (
    "Test-mode AI responses. This run is recorded in test AI mode. "
    "AI responses use fixtures instead of real AI grading. "
    "Do not treat AI-derived judgments as a real assessment of the manuscript. "
    "Check the recorded evidence directly."
)


def _source(variant):
    doc = pymupdf.open(_SOURCE)
    page = doc[0]
    if variant == "cropped":
        page.set_cropbox(pymupdf.Rect(25, 30, 565, 820))
    elif variant in (90, 180, 270):
        page.set_rotation(variant)
    elif variant == "wide":
        page.set_mediabox(pymupdf.Rect(0, 0, 1600.25, 300.5))
    elif variant == "tall":
        page.set_mediabox(pymupdf.Rect(0, 0, 400.25, 1800.5))
    page.insert_text((40, page.rect.height - 35), _BOTTOM_SENTENCE, fontsize=11)
    annot = page.add_rect_annot(pymupdf.Rect(35, 60, 180, 100))
    annot.set_colors(stroke=(0, 0, 1))
    annot.update()
    return doc


@pytest.mark.parametrize("variant", ["ordinary", "cropped", 90, 180, 270])
@pytest.mark.parametrize("mode", ["fake", "unknown"])
def test_disclosure_preserves_every_source_pixel_and_existing_annotation(variant, mode):
    settings = get_settings()
    with _source(variant) as doc:
        page = doc[0]
        source = page.get_pixmap(dpi=settings.export_page_image_dpi, annots=True)
        original_geometry = (tuple(page.mediabox), tuple(page.cropbox), page.rotation)
        drawings = page.get_drawings()
        annotations = [(a.xref, tuple(a.rect)) for a in page.annots()]
        combined = pymupdf.Pixmap(
            _render_page_png(page, (), llm_disclosure=_LLM_MODE_DISCLOSURE[mode], settings=settings)
        )

        assert combined.width == source.width
        # This includes the actual near-bottom sentence and visible blue annotation.
        assert combined.samples[: len(source.samples)] == source.samples
        assert combined.height > source.height
        assert page.get_drawings() == drawings
        assert [(a.xref, tuple(a.rect)) for a in page.annots()] == annotations
        assert (tuple(page.mediabox), tuple(page.cropbox), page.rotation) == original_geometry


def test_fake_render_does_not_contaminate_a_later_real_render_with_the_same_highlights():
    settings = get_settings()
    with _source("ordinary") as doc:
        page = doc[0]
        box = page.search_for(_BOTTOM_SENTENCE)[0]
        boxes = ((box.x0, page.rect.height - box.y1, box.x1, page.rect.height - box.y0),)
        before = _render_page_png(page, boxes, llm_disclosure=None, settings=settings)
        annotated = pymupdf.Pixmap(before)
        plain = page.get_pixmap(dpi=settings.export_page_image_dpi, annots=True)
        assert annotated.samples != plain.samples, "the intended finding mark must be present"

        combined = pymupdf.Pixmap(
            _render_page_png(
                page, boxes, llm_disclosure=_LLM_MODE_DISCLOSURE["fake"], settings=settings
            )
        )
        assert combined.samples[: len(annotated.samples)] == annotated.samples
        after = _render_page_png(page, boxes, llm_disclosure=None, settings=settings)
        assert after == before
        assert len(list(page.annots())) == 1, "retain only the original visible annotation"


@pytest.mark.parametrize(
    ("mode", "variant"),
    [
        (mode, variant)
        for mode in ("fake", "unknown")
        for variant in ("ordinary", "cropped", 90, "wide")
    ]
    + [("unknown", "tall")],
)
@pytest.mark.parametrize("dpi", [73, 150])
def test_warning_font_meets_minimum_in_the_actual_export_flowable(variant, mode, dpi, monkeypatch):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Image

    settings = get_settings().model_copy(update={"export_page_image_dpi": dpi})
    monkeypatch.setattr(export, "get_settings", lambda: settings)
    observed_font_pixels = []
    real_get_pixmap = pymupdf.Page.get_pixmap

    def inspect_band(self, *args, **kwargs):
        if "matrix" in kwargs:
            spans = [
                span
                for block in self.get_text("dict")["blocks"]
                for line in block["lines"]
                for span in line["spans"]
            ]
            observed_font_pixels.extend(span["size"] for span in spans)
            for span in spans:
                assert pymupdf.Rect(span["bbox"]) in self.rect
        return real_get_pixmap(self, *args, **kwargs)

    from app.report.page_image_disclosure import disclosure_layout

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", inspect_band)
    summary_text = disclosure_layout()["summaries"].get(mode, _LLM_MODE_DISCLOSURE[mode][0])
    disclosure = (summary_text, _LLM_MODE_DISCLOSURE[mode][1])
    with _source(variant) as doc:
        png = _render_page_png(doc[0], (), llm_disclosure=disclosure, settings=settings)
    raster = pymupdf.Pixmap(png)
    content_width = A4[0] - 2 * export._PRINT_LAYOUT["side_margin"]
    flow = export._page_evidence_flowables(
        PageEvidenceImageOut(image_png=png, page=1, reason=None),
        content_width,
        export._styles(export._register_fonts()),
        dpi,
    )
    image = next(item for item in flow if isinstance(item, Image))
    # ReportLab's final multiply can round one ULP above the exact bound.
    assert image.drawWidth <= nextafter(content_width, inf)
    assert image.drawHeight <= nextafter(settings.export_page_image_max_height, inf)
    assert observed_font_pixels, "read the actual rendered band font, not the layout estimate"
    effective = min(observed_font_pixels) * image.drawHeight / raster.height
    assert effective >= settings.export_page_disclosure_font_pt


@pytest.mark.parametrize("dpi", [73, 150])
def test_long_notice_on_a_narrow_tall_page_retains_the_honest_unavailable_boundary(dpi):
    settings = get_settings().model_copy(update={"export_page_image_dpi": dpi})
    # This 400 x 1800 source cannot retain the long full report notice at
    # readable size within a 420pt complete image and its bounded canvas.
    # A shorter separately approved image summary can fit; silently shrinking
    # this full notice or covering the source is not an acceptable substitute.
    with _source("tall") as doc, pytest.raises(PageImageUnavailable, match="configured"):
        _render_page_png(
            doc[0], (), llm_disclosure=(_LONG_TEST_NOTICE, "danger"), settings=settings
        )


def test_source_size_limit_is_checked_before_rasterization(monkeypatch):
    settings = get_settings().model_copy(update={"export_page_image_max_pixels": 1})
    with _source("ordinary") as doc:

        def forbidden(*args, **kwargs):
            pytest.fail("oversized source must not be rasterized")

        monkeypatch.setattr(pymupdf.Page, "get_pixmap", forbidden)
        with pytest.raises(PageImageUnavailable, match="configured image size limit"):
            _render_page_png(doc[0], (), llm_disclosure=None, settings=settings)


@pytest.mark.parametrize("text", [" ", "UNSPLITTABLE" * 500])
def test_unreadable_notice_is_explicitly_unavailable_instead_of_blank_or_clipped(text):
    with (
        _source("ordinary") as doc,
        pytest.raises(PageImageUnavailable, match="configured readable size"),
    ):
        _render_page_png(doc[0], (), llm_disclosure=(text, "caution"), settings=get_settings())


@pytest.mark.parametrize("visible", [False, True])
def test_finding_mark_preserves_original_optional_content_visibility(visible):
    settings = get_settings()
    with pymupdf.open(_SOURCE) as source:
        layer = source.add_ocg("Review layer", on=visible)
        source[0].insert_text(
            (45, 145),
            "HIDDEN LAYER MUST NOT BECOME VISIBLE",
            fontsize=15,
            color=(1, 0, 0),
            oc=layer,
        )
        pdf = source.tobytes()
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        original = page.get_pixmap(dpi=settings.export_page_image_dpi, annots=True)
        was_dirty = doc.is_dirty
        marked = pymupdf.Pixmap(
            _render_page_png(
                page,
                ((35, page.rect.height - 65, 200, page.rect.height - 35),),
                llm_disclosure=None,
                settings=settings,
            )
        )
        # The intended mark is near the top. Every pixel below it, including
        # this document-level optional-content layer, must retain its state.
        start = int(100 * settings.export_page_image_dpi / 72) * original.stride
        assert marked.samples[start:] == original.samples[start:]
        assert marked.samples[:start] != original.samples[:start]
        assert doc.is_dirty == was_dirty
        assert doc.get_ocgs()[layer]["on"] is visible
