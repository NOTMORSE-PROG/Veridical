"""Finding pagination controls capture flowables without creating a PDF."""

from reportlab.platypus import CondPageBreak, Paragraph, PTOContainer, Table

from app.report import export
from app.report.schemas import PageEvidenceImageOut, SourceEvidencePrintOut
from tests.test_report_export_details import _data
from tests.test_source_print_layout import MeasuredBlock


def paragraphs(parts):
    for part in parts:
        if isinstance(part, Paragraph):
            yield part
        elif isinstance(part, (list, tuple)):
            yield from paragraphs(part)
        elif isinstance(part, Table):
            for row in part._cellvalues:
                for cell in row:
                    if isinstance(cell, (list, tuple)):
                        yield from paragraphs(cell)
        elif isinstance(part, PTOContainer):
            yield from paragraphs(part._content)


def flowables(parts):
    """Every flowable inside `parts`, looking through nested table cells."""
    for part in parts:
        if isinstance(part, (list, tuple)):
            yield from flowables(part)
        else:
            yield part


def captured(monkeypatch, *, source_height=200, long_quote=False):
    data = _data()
    data.report.results = []
    data.flags[0].check_kind = "statistical_forensics"
    data.flags[0].evidence_excerpt = "Summary that is not the bound quotation."
    entry = SourceEvidencePrintOut(
        reference_id="a" * 64,
        criterion_id=None,
        flag_id=data.flags[0].id,
        role="finding_excerpt",
        index=0,
        quote="COMPLETE_BOUND_QUOTE",
        anchor="page 3",
        placement_state="unavailable",
        image=PageEvidenceImageOut(image_png=None, page=None, reason="No image available."),
    )
    data.source_evidence = [entry]
    story = []
    calls = []
    source = Table([[MeasuredBlock(source_height)]], colWidths=[400])
    source.setStyle(
        export.TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    parts = (
        [
            Paragraph("LONG_BOUND_QUOTE " * 1000, export._styles(export._register_fonts())["body"]),
            source,
        ]
        if long_quote
        else [source]
    )

    def source_blocks(value, *args, **kwargs):
        calls.append(value)
        return parts

    monkeypatch.setattr(export, "source_evidence_flowables", source_blocks)
    monkeypatch.setattr(
        export.SimpleDocTemplate, "multiBuild", lambda self, flow, **kwargs: story.extend(flow)
    )
    before = data.model_dump_json()
    export.build_report_pdf(data)
    assert data.model_dump_json() == before
    assert calls == [entry]
    finding = next(
        part
        for part in story
        if isinstance(part, PTOContainer)
        and any(value.getPlainText() == "Manuscript evidence" for value in paragraphs([part]))
    )
    return story, finding, source


def test_finding_heading_is_reserved_with_its_reason_and_first_evidence(monkeypatch):
    # The reason now sits between the heading and the evidence, so the
    # heading is kept with what follows by a reserved page break and
    # keep-with-next, not by packing everything into one table row.
    story, finding, source = captured(monkeypatch)
    text = [item.getPlainText() for item in paragraphs([finding])]
    assert any(value.startswith("Finding 1:") for value in text)
    assert "Manuscript evidence" in text
    assert any(item is source for item in flowables(finding._content))
    index = next(i for i, part in enumerate(story) if part is finding)
    reserve = story[index - 1]
    assert isinstance(reserve, CondPageBreak)
    assert 0 < reserve.height <= 700
    heading = next(
        item
        for item in finding._content
        if isinstance(item, Paragraph) and item.getPlainText().startswith("Finding 1:")
    )
    assert heading.keepWithNext
    assert "Finding 1 (continued)" in heading._ptoinfo.header[0].getPlainText()


def test_source_too_tall_for_heading_retains_existing_splittable_continuation(monkeypatch):
    _, finding, source = captured(monkeypatch, source_height=690)
    assert finding._content[0] is not source
    assert not isinstance(finding._content[0], Table)
    assert source in finding._content
    assert any(value.getPlainText() == "Manuscript evidence" for value in paragraphs([finding]))


def test_genuinely_long_source_quote_is_not_wrapped_into_an_oversized_atomic_row(monkeypatch):
    _, finding, source = captured(monkeypatch, long_quote=True)
    assert not isinstance(finding._content[0], Table)
    quote = next(
        item
        for item in finding._content
        if isinstance(item, Paragraph) and "LONG_BOUND_QUOTE" in item.getPlainText()
    )
    assert quote.wrap(470, 700)[1] > 700
    assert source in finding._content
    assert quote.getPlainText().count("LONG_BOUND_QUOTE") == 1000


def test_calculation_detail_follows_primary_summary_and_coverage_without_changing_values(
    monkeypatch,
):
    data = _data(flags=[])
    data.report.results = data.report.results[:1]
    data.report.results[0].outcome = "escalated"
    data.report.results[0].resolution = None
    data.report.composite_score = 75.0
    data.report.decision = None
    story = []
    monkeypatch.setattr(
        export.SimpleDocTemplate, "multiBuild", lambda self, flow, **kwargs: story.extend(flow)
    )
    before = data.model_dump_json()
    export.build_report_pdf(data)
    texts = [part.getPlainText() for part in paragraphs(story)]
    calculation = texts.index("Calculation details")
    assert next(i for i, value in enumerate(texts) if "Instructor decision" in value) < calculation
    assert next(i for i, value in enumerate(texts) if "System readiness" in value) < calculation
    assert (
        next(i for i, value in enumerate(texts) if "1 of 1 criteria remain unresolved" in value)
        < calculation
    )
    assert (
        next(i for i, value in enumerate(texts) if "Composite score: 75.0%" in value) > calculation
    )
    assert sum("Composite score: 75.0%" in value for value in texts) == 1
    assert export._explainer(data.report) in texts
    assert any(value == "Ready at 85.0% or above. Not Ready at 60.0% or below." for value in texts)
    assert any(
        getattr(part, "navigation_key", None) == "calculation-details" for part in paragraphs(story)
    )
    assert data.model_dump_json() == before
