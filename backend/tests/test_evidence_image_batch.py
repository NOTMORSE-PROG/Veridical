"""Batch accounting with admitted-image stubs; no native PDF or source I/O."""

import hashlib
import struct
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as Row

import pytest

from app.config import Settings
from app.ingest.schemas import SectionTree
from app.report import evidence_images as images
from app.report.evidence_placement import EvidencePlacementOut, ReferencePageImage
from app.report.evidence_references import build_evidence_reference_snapshot

TREE = SectionTree(source="none", nodes=[])
SOURCE = b"immutable synthetic source bytes"


def snapshot(*, audience="private", digest=None):
    report = Row(
        check_run_id=7,
        manuscript_id=8,
        assessment_revision=3,
        results=[
            Row(
                check_result_id=11,
                criterion_id=21,
                anchor=None,
                evidence=[
                    Row(quote=f"Saved complete quotation {number}.", anchor="page 1")
                    for number in range(3)
                ],
            )
        ],
    )
    return build_evidence_reference_snapshot(
        report,
        (),
        expected_flag_ids=(),
        audience=audience,
        source_sha256=digest if digest is not None else hashlib.sha256(SOURCE).hexdigest(),
    )


@pytest.fixture
def render_stubs(monkeypatch):
    calls = []
    closed = []
    monkeypatch.setattr(images, "manuscript_source_file", lambda *args: (Path("unused"), "pdf"))

    @contextmanager
    def snapshot(path, digest, settings):
        assert path == Path("unused")
        assert digest == hashlib.sha256(SOURCE).hexdigest()
        yield object()

    monkeypatch.setattr(images, "SourceSnapshot", snapshot)

    @contextmanager
    def pinned(snapshot, settings):
        try:
            yield object()
        finally:
            closed.append(True)

    def render(source, reference, tree, settings, **kwargs):
        calls.append((reference, settings, kwargs))
        png = (
            b"\x89PNG\r\n\x1a\n" + bytes(8) + struct.pack(">II", 10, 10) + bytes([reference.index])
        )
        if (
            settings.evidence_source_max_rendered_pixels < 100
            or settings.evidence_source_max_rendered_bytes < len(png)
        ):
            return images.unavailable_image(reference, "resource_limit")
        return ReferencePageImage(
            EvidencePlacementOut(
                reference_id=reference.reference_id,
                state="text_layer_match",
                page=1,
                boxes=((float(reference.index), 1.0, float(reference.index) + 1, 2.0),),
            ),
            png,
        )

    monkeypatch.setattr(images, "pin_pdf_source_file", pinned)
    monkeypatch.setattr(images, "render_pdf_evidence", render)
    monkeypatch.setattr(
        images,
        "locate_pdf_evidence",
        lambda source, ref, *args: EvidencePlacementOut(
            reference_id=ref.reference_id,
            state="page_only",
            page=1,
        ),
    )
    return calls, closed


def build(settings, record=None):
    return images.build_recorded_evidence_images(
        images.EvidenceSourceFile("unused", None),
        record or snapshot(),
        TREE,
        settings,
        llm_mode="real",
    )


def test_attempted_variant_limit_keeps_every_saved_passage_and_omission_reason(render_stubs):
    calls, closed = render_stubs
    rows = build(Settings(_env_file=None, export_max_page_images=1))
    assert len(rows) == 3 and len(calls) == 1 and closed == [True]
    assert rows[0].image.image_png and all(row.image.image_png is None for row in rows[1:])
    assert all("configured image limit" in row.image.reason for row in rows[1:])
    assert [row.quote for row in rows] == [ref.quote for ref in snapshot().references]


def test_unlocatable_entry_does_not_consume_the_next_eligible_image_attempt(
    render_stubs, monkeypatch
):
    calls, _ = render_stubs
    admitted = images.locate_pdf_evidence
    monkeypatch.setattr(
        images,
        "locate_pdf_evidence",
        lambda source, ref, *args: (
            EvidencePlacementOut(
                reference_id=ref.reference_id, state="unavailable", reason="location_unavailable"
            )
            if ref.index == 0
            else admitted(source, ref, *args)
        ),
    )
    rows = build(Settings(_env_file=None, export_max_page_images=1))
    assert len(rows) == 3 and len(calls) == 1 and calls[0][0].index == 1
    assert rows[0].image.image_png is None and rows[1].image.image_png
    assert rows[2].image.image_png is None


@pytest.mark.parametrize(
    "field,budget,expected_images,remaining",
    [
        ("evidence_source_max_rendered_pixels", 150, 1, 50),
        ("evidence_source_max_rendered_bytes", 40, 1, 15),
    ],
)
def test_cumulative_budget_is_passed_to_each_next_render_before_allocation(
    render_stubs, field, budget, expected_images, remaining
):
    calls, closed = render_stubs
    rows = build(Settings(_env_file=None, **{field: budget}))
    assert (
        len(rows) == 3 and sum(row.image.image_png is not None for row in rows) == expected_images
    )
    assert getattr(calls[1][1], field) == remaining
    assert getattr(calls[2][1], field) == remaining
    assert closed == [True]


def test_same_page_variants_keep_independent_reference_and_png_identity(render_stubs):
    rows = build(Settings(_env_file=None))
    assert len({row.reference_id for row in rows}) == 3
    assert len({row.image.image_png for row in rows}) == 3
    assert all(
        "Text-layer match" in row.image.reason and "does not verify" in row.image.reason
        for row in rows
    )


def test_unknown_recorded_source_hash_never_opens_current_source(render_stubs, monkeypatch):
    calls, closed = render_stubs

    def forbidden(*args, **kwargs):
        raise AssertionError("Unknown historical source must not be rebound to current bytes")

    monkeypatch.setattr(images, "manuscript_source_file", forbidden)
    rows = build(Settings(_env_file=None), snapshot(digest="invalid"))
    assert len(rows) == 3 and calls == [] and closed == []
    assert all("identity was not recorded" in row.image.reason for row in rows)


def test_docx_source_keeps_passages_without_fabricating_page_images(render_stubs, monkeypatch):
    calls, closed = render_stubs
    monkeypatch.setattr(images, "manuscript_source_file", lambda *args: (Path("unused"), "docx"))
    rows = build(Settings(_env_file=None))
    assert len(rows) == 3 and calls == [] and closed == []
    assert all(row.image.image_png is None and "DOCX" in row.image.reason for row in rows)


def test_encoded_byte_rejection_does_not_refund_raster_pixel_work(render_stubs, monkeypatch):
    import pymupdf

    from app.report import evidence_placement as placement
    from tests.test_source_print_layout import png

    raster = png()
    raster_calls = []

    class Document:
        def __getitem__(self, index):
            return Row(rect=pymupdf.Rect(0, 0, 32, 640))

    @contextmanager
    def pinned(*args):
        yield Row(document=Document())

    def rendered(*args, **kwargs):
        raster_calls.append(True)
        return raster

    monkeypatch.setattr(
        images,
        "pin_pdf_source_file",
        lambda snapshot, settings: pinned(SOURCE, hashlib.sha256(SOURCE).hexdigest(), settings),
    )
    monkeypatch.setattr(images, "render_pdf_evidence", placement.render_pdf_evidence)
    monkeypatch.setattr(placement, "locate_pdf_evidence", images.locate_pdf_evidence)
    monkeypatch.setattr(placement, "_render_page_png", rendered)
    rows = build(
        Settings(
            _env_file=None,
            export_page_image_dpi=72,
            evidence_source_max_rendered_pixels=51_200,
            evidence_source_max_rendered_bytes=len(raster) + 1,
        )
    )
    assert len(rows) == 3 and sum(row.image.image_png is not None for row in rows) == 1
    assert len(raster_calls) == 2 and len(raster_calls) * 32 * 640 <= 51_200
    assert all(row.placement_state == "unavailable" for row in rows[1:])
    assert sum(len(row.image.image_png or b"") for row in rows) <= len(raster) + 1


@pytest.mark.parametrize("ratio", [0.0, 1.5])
def test_export_pauses_after_each_page_render_only_when_configured(
    render_stubs, monkeypatch, ratio
):
    calls, _ = render_stubs
    pauses = []
    monkeypatch.setattr(images.time, "sleep", pauses.append)
    rows = build(Settings(_env_file=None, export_render_pause_ratio=ratio))
    assert len(rows) == 3 and len(calls) == 3
    assert len(pauses) == (len(calls) if ratio else 0)
    assert all(pause >= 0 for pause in pauses)
