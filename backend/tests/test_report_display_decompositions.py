"""F8: typeset ligatures in saved evidence no longer stop the PDF export."""

import unicodedata

import pytest

from app.errors import ReportExportUnavailableError
from app.report.text import register_fonts, text_runs


def drawn(text: str) -> str:
    return "".join(run for _face, run in text_runs(text, register_fonts()))


def test_allowlisted_ligatures_are_drawn_as_their_letters():
    text = drawn("Eﬀectiveness and artiﬁcial eﬃciency")
    # A face that covers a ligature still draws it; only uncovered ones decompose.
    assert unicodedata.normalize("NFKC", text) == "Effectiveness and artificial efficiency"
    assert "ﬀ" not in text and "ﬃ" not in text


def test_text_the_fonts_cover_is_drawn_unchanged():
    assert drawn("x² + 60\u2013117") == "x² + 60\u2013117"


def test_unlisted_unsupported_characters_still_stop_the_export():
    with pytest.raises(ReportExportUnavailableError):
        drawn("\U0001d54f")
