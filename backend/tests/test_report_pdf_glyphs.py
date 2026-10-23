"""F8/BUG-454: source characters survive font selection and paragraph splitting."""

from io import BytesIO
from xml.sax.saxutils import escape

import fitz
import pytest
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import PTOContainer, SimpleDocTemplate

from app.errors import HTTP_STATUS, ReportExportUnavailableError
from app.report.export import _fit_running_identity, _styles
from app.report.text import Paragraph, draw_right_text, register_fonts, text_runs, text_width
from tests.test_return_export_summary import rendered, report, text


@pytest.mark.parametrize(
    ("markup", "fallback"),
    [
        ("{}", "IBMPlexSans"),
        ("<b>{}</b>", "IBMPlexSans-Bold"),
        ("<i>{}</i>", "IBMPlexSans-Italic"),
        ("<b><i>{}</i></b>", "IBMPlexSans-BoldItalic"),
    ],
)
def test_fallback_preserves_exact_characters_and_actual_style(markup, fallback):
    source = "αβμσχ≤≥≠≈∑√∞₂ café <tag> & end"
    paragraph = Paragraph(markup.format(escape(source)), _styles(register_fonts())["body"])
    assert paragraph.getPlainText() == source
    assert all(
        fragment.fontName == fallback for fragment in paragraph.frags if "α" in fragment.text
    )
    assert any(
        fragment.fontName.startswith("PublicSans") and "café" in fragment.text
        for fragment in paragraph.frags
    )
    stream = BytesIO()
    SimpleDocTemplate(stream).build([paragraph])
    with fitz.open(stream=stream.getvalue(), filetype="pdf") as document:
        extracted = document[0].get_text()
        assert source in extracted and "\x00" not in extracted
        spans = [
            span
            for block in document[0].get_text("dict")["blocks"]
            if "lines" in block
            for line in block["lines"]
            for span in line["spans"]
        ]
        assert any(fallback == span["font"] and "α" in span["text"] for span in spans)


def test_long_unicode_split_keeps_link_anchor_and_complete_source():
    source = "αβ ≤≥ café <not markup> & text. " * 120
    stream = BytesIO()
    style = _styles(register_fonts())["body"]
    flow = [
        Paragraph('<link href="#decision">Read decision</link>', style),
        PTOContainer([Paragraph(escape(source), style)]),
        Paragraph('<a name="decision"/>Final Decision', style),
    ]
    SimpleDocTemplate(stream, pagesize=(300, 350)).build(flow)
    with fitz.open(stream=stream.getvalue(), filetype="pdf") as document:
        assert len(document) > 2
        assert " ".join(source.split()) in " ".join(text(document).split())
        assert "\x00" not in text(document)
        [link] = [link for link in document[0].get_links() if link["kind"] == fitz.LINK_GOTO]
        assert "Final Decision" in document[link["page"]].get_text()


def test_running_identity_measurement_matches_drawn_fallback():
    font = register_fonts()
    value = "Research αβ café " * 20
    width = 150
    fitted = _fit_running_identity(value, font, 9, width)
    assert fitted.endswith("...") and text_width(fitted, font, 9) <= width
    stream = BytesIO()
    canvas = Canvas(stream)
    draw_right_text(canvas, fitted, font, 9, 250, 700)
    canvas.save()
    with fitz.open(stream=stream.getvalue(), filetype="pdf") as document:
        assert fitted in document[0].get_text()
        spans = [
            span
            for block in document[0].get_text("dict")["blocks"]
            if "lines" in block
            for line in block["lines"]
            for span in line["spans"]
        ]
        assert max(span["bbox"][2] for span in spans) == pytest.approx(250, abs=0.05)


@pytest.mark.parametrize("unsupported", ["\U0010ffff", "cafe\u0301", "\u0301"])
def test_unsupported_character_or_unverified_shaping_never_returns_partial_pdf(unsupported):
    with pytest.raises(ReportExportUnavailableError) as error:
        rendered(report(decision_note=f"Private reason with unsupported {unsupported}."))
    assert "Private reason" not in str(error.value)
    assert "report remains available" in str(error.value)
    assert HTTP_STATUS[error.value.code] == 422


def test_ordinary_latin_keeps_legacy_explicit_type1_font():
    assert list(text_runs("Ordinary café", "Helvetica")) == [("Helvetica", "Ordinary café")]
    assert pdfmetrics.stringWidth("Ordinary café", "Helvetica", 10) == text_width(
        "Ordinary café", "Helvetica", 10
    )
    paragraph = Paragraph("<b>α</b>", ParagraphStyle("legacy", fontName="Helvetica"))
    assert paragraph.frags[0].fontName == "IBMPlexSans-Bold"


def test_unavailable_fallback_face_does_not_silently_select_helvetica(monkeypatch):
    from app.report import text as pdf_text

    def unavailable(*args, **kwargs):
        raise OSError("PRIVATE-PATH-DO-NOT-EXPOSE")

    register_fonts.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(pdf_text, "TTFont", unavailable)
            with pytest.raises(ReportExportUnavailableError) as error:
                register_fonts()
            assert "PRIVATE-PATH" not in str(error.value)
    finally:
        register_fonts.cache_clear()
        register_fonts()
