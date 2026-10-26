"""Qualified placement on three synthetic, retained PDF sources.

This module creates exactly three PDF fixture files unless an existing retained
input directory is supplied. For creation, run the installed official PDF create
preflight with expected-output-count 3 immediately before execution.
It never edits or saves a pinned source and performs no SQL/provider/network work.
"""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace as Row

import pymupdf
import pytest

from app.config import Settings
from app.errors import ConflictError
from app.ingest.schemas import SectionTree
from app.report.evidence_placement import (
    PinnedPdfSource,
    locate_pdf_evidence,
    pin_pdf_source,
    render_pdf_evidence,
)
from app.report.evidence_references import build_evidence_reference_snapshot

UNIQUE = "A complete unique source sentence supports navigation only."
MULTILINE = (
    "This is a multiline recorded excerpt whose full wording crosses "
    "several native lines on this page."
)
SECOND = "The second recorded source is located on the second page."
HIDDEN = "The hidden selectable layer has a distinct recorded quotation."
ROTATED = "A recorded sentence remains located after crop and rotation."
ANNOTATED = "A clean recorded source sentence for annotation policy checks."
TREE = SectionTree(source="none", nodes=[])


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    reuse = os.environ.get("EVIDENCE_NATIVE_REUSE_INPUT_DIR")
    if reuse:
        paths = {
            name: Path(reuse) / f"{name}.pdf" for name in ("text", "transforms", "annotations")
        }
        assert all(path.is_file() for path in paths.values())
        return {name: (path, path.read_bytes()) for name, path in paths.items()}
    directory = Path(
        os.environ.get("EVIDENCE_NATIVE_ARTIFACT_DIR")
        or tmp_path_factory.mktemp("evidence-placement")
    )
    directory.mkdir(parents=True, exist_ok=True)
    paths = {name: directory / f"{name}.pdf" for name in ("text", "transforms", "annotations")}
    assert not any(path.exists() for path in paths.values()), (
        "Use a new retained artifact directory for this campaign"
    )
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), UNIQUE)
        page.insert_text((60, 120), "Repeated source wording appears here.")
        page.insert_text((60, 160), "Repeated source wording appears here.")
        assert page.insert_textbox(pymupdf.Rect(60, 210, 260, 340), MULTILINE) >= 0
        doc.new_page().insert_text((60, 80), SECOND)
        page = doc.new_page()
        page.insert_text((60, 80), "This page has an OCR-like hidden text layer.")
        page.insert_text((60, 130), HIDDEN, render_mode=3)
        doc.save(paths["text"])
    with pymupdf.open() as doc:
        for rotation in (90, 180):
            page = doc.new_page(width=600, height=800)
            page.insert_text((100, 160), ROTATED)
            page.set_cropbox(pymupdf.Rect(40, 80, 560, 720))
            page.set_rotation(rotation)
        doc.save(paths["transforms"])
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), ANNOTATED)
        page.add_text_annot((300, 150), "PRIVATE_PDF_COMMENT")
        page = doc.new_page()
        page.insert_text((60, 80), ANNOTATED)
        page.add_redact_annot(pymupdf.Rect(50, 65, 500, 100), fill=(0, 0, 0))
        page = doc.new_page()
        page.insert_text((60, 80), ANNOTATED)
        widget = pymupdf.Widget()
        widget.field_name = "private_field"
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(60, 160, 350, 200)
        widget.field_value = "PRIVATE_WIDGET_VALUE"
        page.add_widget(widget)
        page = doc.new_page()
        page.insert_text((60, 80), ANNOTATED)
        page.insert_link(
            {
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(50, 65, 500, 100),
                "uri": "https://example.invalid/private-link",
            }
        )
        doc.new_page().insert_text((60, 80), ANNOTATED)
        doc.save(paths["annotations"])
    return {name: (path, path.read_bytes()) for name, path in paths.items()}


def reference(data, quote, page=1, *, audience="private", digest=None):
    row = Row(
        check_result_id=11,
        criterion_id=21,
        anchor=None,
        evidence=[Row(quote=quote, anchor=f"page {page}")],
    )
    report = Row(check_run_id=7, manuscript_id=8, assessment_revision=3, results=[row])
    return build_evidence_reference_snapshot(
        report,
        (),
        expected_flag_ids=(),
        source_sha256=digest or hashlib.sha256(data).hexdigest(),
        audience=audience,
    ).references[0]


def pinned(data, settings=None):
    return pin_pdf_source(
        data, hashlib.sha256(data).hexdigest(), settings or Settings(_env_file=None)
    )


def test_unique_full_quote_gets_only_qualified_text_layer_location(sources):
    _, data = sources["text"]
    with pinned(data) as source:
        result = locate_pdf_evidence(
            source, reference(data, UNIQUE), TREE, Settings(_env_file=None)
        )
        assert result.state == "text_layer_match" and result.page == 1 and result.boxes
        assert "verified" not in result.model_dump_json()
        assert source.document.is_dirty is False
    assert source.document.is_closed


@pytest.mark.parametrize(
    "quote,page",
    [(UNIQUE + " Missing ending.", 1), (UNIQUE, 2), ("Repeated source wording appears here.", 1)],
)
def test_prefix_only_wrong_page_and_repeated_quotes_never_reuse_prefix_search(
    sources, monkeypatch, quote, page
):
    _, data = sources["text"]

    def forbidden(*args, **kwargs):
        raise AssertionError("Whole-quote admission must precede native search")

    monkeypatch.setattr(pymupdf.Page, "search_for", forbidden)
    with pinned(data) as source:
        result = locate_pdf_evidence(
            source, reference(data, quote, page), TREE, Settings(_env_file=None)
        )
    assert result.state == "page_only" and result.boxes == ()


def test_multiline_quote_can_have_several_boxes_for_one_occurrence(sources):
    _, data = sources["text"]
    with pinned(data) as source:
        result = locate_pdf_evidence(
            source, reference(data, MULTILINE), TREE, Settings(_env_file=None)
        )
    assert result.state == "text_layer_match" and len(result.boxes) > 1


def test_hidden_selectable_text_never_receives_visible_ink_or_support_verification(sources):
    _, data = sources["text"]
    with pinned(data) as source:
        result = locate_pdf_evidence(
            source, reference(data, HIDDEN, 3), TREE, Settings(_env_file=None)
        )
    assert result.state == "text_layer_match"
    assert "visible" not in result.model_dump_json() and "support" not in result.model_dump_json()


@pytest.mark.parametrize("page", [1, 2])
def test_crop_and_rotation_use_actual_transform_and_retained_source(sources, page):
    path, data = sources["transforms"]
    with pinned(data) as source:
        result = render_pdf_evidence(
            source, reference(data, ROTATED, page), TREE, Settings(_env_file=None), llm_mode="real"
        )
        assert result.placement.state == "text_layer_match" and result.image_png
        native = source.document[page - 1]
        converted = pymupdf.Rect(result.placement.boxes[0]) * native.transformation_matrix
        # Public extraction dirties rotated native handles even though /Rotate
        # is restored. Keep this independent expected-geometry probe disposable.
        with pymupdf.open(stream=data, filetype="pdf") as expected_source:
            expected = expected_source[page - 1].search_for(ROTATED)[0]
        assert tuple(converted) == pytest.approx(tuple(expected), abs=0.001)
        repeated = render_pdf_evidence(
            source, reference(data, ROTATED, page), TREE, Settings(_env_file=None), llm_mode="real"
        )
        assert repeated.placement == result.placement and repeated.image_png == result.image_png
        (path.parent / f"transformed-page-{page}.png").write_bytes(result.image_png)
        assert source.document.is_dirty is False
    assert path.read_bytes() == data


@pytest.mark.parametrize("page", [1, 2, 3, 4])
def test_public_comment_redaction_widget_and_link_pages_are_withheld_before_raster(
    sources, monkeypatch, page
):
    _, data = sources["annotations"]

    def forbidden(*args, **kwargs):
        raise AssertionError("Annotated public page must not be rasterized")

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", forbidden)
    with pinned(data) as source:
        result = render_pdf_evidence(
            source,
            reference(data, ANNOTATED, page, audience="public"),
            TREE,
            Settings(_env_file=None),
            llm_mode="real",
        )
    assert result.placement.reason == "public_page_annotations" and result.image_png is None


def test_public_inspection_failure_is_not_empty_annotation_inventory(sources, monkeypatch):
    _, data = sources["annotations"]

    def failed(*args, **kwargs):
        raise ValueError("PRIVATE_NATIVE_PATH")

    monkeypatch.setattr(pymupdf.Page, "annot_xrefs", failed)
    with pinned(data) as source:
        result = render_pdf_evidence(
            source,
            reference(data, ANNOTATED, 5, audience="public"),
            TREE,
            Settings(_env_file=None),
            llm_mode="real",
        )
    assert (
        result.image_png is None and result.placement.reason == "public_page_inspection_unavailable"
    )
    assert "PRIVATE" not in result.placement.model_dump_json()


def test_private_annotations_keep_original_native_visibility_and_do_not_supply_public_variant(
    sources, monkeypatch
):
    _, data = sources["annotations"]
    original = pymupdf.Page.get_pixmap
    calls = []

    def traced(self, *args, **kwargs):
        calls.append(kwargs.get("annots"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", traced)
    with pinned(data) as source:
        own = render_pdf_evidence(
            source, reference(data, ANNOTATED), TREE, Settings(_env_file=None), llm_mode="real"
        )
        shared = render_pdf_evidence(
            source,
            reference(data, ANNOTATED, audience="public"),
            TREE,
            Settings(_env_file=None),
            llm_mode="real",
        )
    assert own.image_png and shared.image_png is None
    assert calls == [True]


def test_clean_public_page_can_render_and_source_bytes_stay_unchanged(sources):
    path, data = sources["annotations"]
    with pinned(data) as source:
        result = render_pdf_evidence(
            source,
            reference(data, ANNOTATED, 5, audience="public"),
            TREE,
            Settings(_env_file=None),
            llm_mode="fake",
        )
        assert result.image_png and result.placement.state == "text_layer_match"
        (path.parent / "public-clean-page.png").write_bytes(result.image_png)
    assert path.read_bytes() == data


def test_changed_source_or_native_mutation_cannot_retain_a_location(sources):
    _, data = sources["text"]
    with pytest.raises(ConflictError), pin_pdf_source(data, "b" * 64, Settings(_env_file=None)):
        pass
    with pinned(data) as source:
        result = locate_pdf_evidence(
            source, reference(data, UNIQUE, digest="b" * 64), TREE, Settings(_env_file=None)
        )
        assert result.state == "unavailable" and result.reason == "source_changed"
        changed = PinnedPdfSource(Row(is_closed=False, is_dirty=True), source.source_sha256, data)
        result = locate_pdf_evidence(
            changed, reference(data, UNIQUE), TREE, Settings(_env_file=None)
        )
        assert result.state == "unavailable" and result.reason == "source_mutated"


def test_partial_or_off_page_native_geometry_is_withheld(sources, monkeypatch):
    _, data = sources["text"]
    original = pymupdf.Page.search_for
    with pinned(data) as source:
        monkeypatch.setattr(
            pymupdf.Page,
            "search_for",
            lambda page, *args, **kwargs: original(page, "complete", quads=True),
        )
        result = locate_pdf_evidence(
            source, reference(data, UNIQUE), TREE, Settings(_env_file=None)
        )
        assert result.state == "page_only" and result.reason == "quote_geometry_unverified"
        monkeypatch.setattr(
            pymupdf.Page,
            "search_for",
            lambda *args, **kwargs: [pymupdf.Quad(pymupdf.Rect(-10, -10, 10, 10))],
        )
        result = locate_pdf_evidence(
            source, reference(data, UNIQUE), TREE, Settings(_env_file=None)
        )
        assert result.state == "page_only" and not result.boxes


@pytest.mark.parametrize(
    "pixel_setting", ["export_page_image_max_pixels", "evidence_source_max_rendered_pixels"]
)
def test_box_and_pixel_resource_limits_degrade_without_unbounded_render(
    sources, monkeypatch, pixel_setting
):
    _, data = sources["text"]
    with pinned(data) as source:
        result = locate_pdf_evidence(
            source,
            reference(data, MULTILINE),
            TREE,
            Settings(_env_file=None, evidence_source_max_highlight_boxes=1),
        )
        assert result.state == "page_only" and not result.boxes

        def forbidden(*args, **kwargs):
            raise AssertionError("Pixel guard must precede raster allocation")

        monkeypatch.setattr(pymupdf.Page, "get_pixmap", forbidden)
        rendered = render_pdf_evidence(
            source,
            reference(data, UNIQUE),
            TREE,
            Settings(_env_file=None, **{pixel_setting: 1}),
            llm_mode="real",
        )
        assert rendered.image_png is None and rendered.placement.reason == "resource_limit"


def test_png_response_byte_limit_is_enforced(sources):
    _, data = sources["text"]
    with pinned(data) as source:
        result = render_pdf_evidence(
            source,
            reference(data, UNIQUE),
            TREE,
            Settings(_env_file=None, evidence_source_max_rendered_bytes=1),
            llm_mode="real",
        )
        assert result.image_png is None and result.placement.reason == "resource_limit"


def test_disposable_extraction_handles_close_on_success_coarse_and_exception(sources, monkeypatch):
    import app.report.evidence_placement as placement

    _, data = sources["text"]
    opened = []
    original = placement.open_source_pdf_bytes

    def tracked(value):
        result = original(value)
        opened.append(result)
        return result

    with pinned(data) as source:
        monkeypatch.setattr(placement, "open_source_pdf_bytes", tracked)
        for quote in (UNIQUE, UNIQUE + " absent"):
            locate_pdf_evidence(source, reference(data, quote), TREE, Settings(_env_file=None))
            assert opened[-1].is_closed

        def failed(*args, **kwargs):
            raise RuntimeError("PRIVATE_NATIVE_ERROR")

        monkeypatch.setattr(pymupdf.Page, "search_for", failed)
        result = locate_pdf_evidence(
            source, reference(data, UNIQUE), TREE, Settings(_env_file=None)
        )
        assert result.state == "page_only" and result.reason == "quote_geometry_unverified"
        assert len(opened) == 3 and all(doc.is_closed for doc in opened)
        assert source.document.is_dirty is False


def test_same_page_references_never_inherit_each_others_marks(sources):
    _, data = sources["text"]
    settings = Settings(_env_file=None)
    with pinned(data) as source:
        original_objects = [
            (source.document.xref_object(index), source.document.xref_stream_raw(index))
            for index in range(1, source.document.xref_length())
        ]
        base = source.document[0].get_pixmap(
            dpi=settings.export_page_image_dpi, colorspace=pymupdf.csRGB, alpha=False, annots=True
        )
        outputs = [
            render_pdf_evidence(source, reference(data, quote), TREE, settings, llm_mode="real")
            for quote in (UNIQUE, MULTILINE)
        ]
        assert outputs[0].placement.reference_id != outputs[1].placement.reference_id
        assert outputs[0].image_png != outputs[1].image_png
        transform = source.document[0].transformation_matrix * pymupdf.Matrix(
            settings.export_page_image_dpi / 72, settings.export_page_image_dpi / 72
        )
        for own, other in (outputs, outputs[::-1]):
            marked = pymupdf.Pixmap(own.image_png)
            other_rect = pymupdf.Rect(other.placement.boxes[0]) * transform
            # The other passage's whole line remains pixel-identical to source.
            for y in range(max(0, int(other_rect.y0) - 3), int(other_rect.y1) + 4):
                for x in range(max(0, int(other_rect.x0) - 3), int(other_rect.x1) + 4):
                    assert marked.pixel(x, y) == base.pixel(x, y)
            own_rect = pymupdf.Rect(own.placement.boxes[0]) * transform
            assert any(
                marked.pixel(x, y) != base.pixel(x, y)
                for y in range(max(0, int(own_rect.y0) - 3), int(own_rect.y1) + 4)
                for x in range(max(0, int(own_rect.x0) - 3), int(own_rect.x1) + 4)
            )
        assert original_objects == [
            (source.document.xref_object(index), source.document.xref_stream_raw(index))
            for index in range(1, source.document.xref_length())
        ]
        assert source.document.is_dirty is False
