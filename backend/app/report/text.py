"""F8: embedded, matching-face glyph fallback without changing source text."""

import json
import unicodedata
from copy import copy
from functools import lru_cache
from pathlib import Path
from threading import Lock

from reportlab.lib.fonts import ps2tt
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph as BaseParagraph

from app.errors import ReportExportUnavailableError

_FONT_DIRECTORY = Path(__file__).parent / "fonts"
_REGISTRATION_LOCK = Lock()


@lru_cache(maxsize=1)
def _font_configuration():
    return json.loads((_FONT_DIRECTORY / "families.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def register_fonts() -> str:
    with _REGISTRATION_LOCK:
        try:
            _font_capabilities.cache_clear()
            configuration = _font_configuration()
            for family in configuration.values():
                for face in family["faces"].values():
                    pdfmetrics.registerFont(
                        TTFont(face["name"], str(_FONT_DIRECTORY / face["file"]))
                    )
                pdfmetrics.registerFontFamily(
                    family["name"],
                    **{style: face["name"] for style, face in family["faces"].items()},
                )
            return configuration["primary"]["name"]
        except Exception as exc:
            raise ReportExportUnavailableError() from exc


def clusters(text: str):
    """Keep combining marks with their base; do not normalize manuscript text."""
    cluster = ""
    for character in text:
        if cluster and not unicodedata.category(character).startswith("M"):
            yield cluster
            cluster = ""
        cluster += character
    if cluster:
        yield cluster


@lru_cache
def _font_capabilities(font_name):
    return next(
        (
            family
            for family in _font_configuration().values()
            if any(face["name"] == font_name for face in family["faces"].values())
        ),
        {},
    )


def _supports(font_name: str, text: str) -> bool:
    capability = _font_capabilities(font_name)
    categories = capability.get("coverage_categories")
    directions = capability.get("coverage_bidi_classes")
    if categories and any(unicodedata.category(char)[0] not in categories for char in text):
        return False
    if directions and any(unicodedata.bidirectional(char) not in directions for char in text):
        return False
    font = pdfmetrics.getFont(font_name)
    coverage = getattr(font.face, "charToGlyph", None)
    if coverage is not None:
        return all(coverage.get(ord(character), 0) for character in text)
    try:
        encoded = text.encode(font.encName)
    except (UnicodeEncodeError, LookupError):
        return False
    return all(font.encoding.vector[code] in font.face.glyphWidths for code in encoded)


def text_runs(text: str, font_name: str):
    register_fonts()
    configuration = _font_configuration()
    style = next(
        (
            style
            for family in configuration.values()
            for style, face in family["faces"].items()
            if face["name"] == font_name
        ),
        None,
    )
    if style is None:
        _, bold, italic = ps2tt(font_name)
        style = (
            "boldItalic"
            if bold and italic
            else "bold"
            if bold
            else "italic"
            if italic
            else "normal"
        )
    fallbacks = [
        family["faces"][style]["name"] for key, family in configuration.items() if key != "primary"
    ]
    selected = None
    run = ""
    for cluster in clusters(text):
        # Cmap coverage alone does not position combining marks. The current
        # unshaped renderer visibly misplaces them, so do not deliver that PDF.
        if any(unicodedata.category(character).startswith("M") for character in cluster):
            raise ReportExportUnavailableError()
        candidate = next(
            (face for face in [font_name, *fallbacks] if _supports(face, cluster)), None
        )
        if candidate is None:
            raise ReportExportUnavailableError()
        if selected is not None and candidate != selected:
            yield selected, run
            run = ""
        selected = candidate
        run += cluster
    if run:
        yield selected, run


def text_width(text: str, font_name: str, size: float) -> float:
    return sum(stringWidth(run, face, size) for face, run in text_runs(text, font_name))


def draw_right_text(canvas, text: str, font_name: str, size: float, x: float, y: float):
    runs = list(text_runs(text, font_name))
    cursor = x - sum(stringWidth(run, face, size) for face, run in runs)
    for face, run in runs:
        canvas.setFont(face, size)
        canvas.drawString(cursor, y, run)
        cursor += stringWidth(run, face, size)


class Paragraph(BaseParagraph):
    """Resolve coverage after safe markup parsing, before measuring or wrapping."""

    def __init__(self, text, style=None, bulletText=None, frags=None, **kwargs):
        super().__init__(text, style, bulletText=bulletText, frags=frags, **kwargs)
        # split() supplies processed word structures that were already resolved.
        if frags is not None:
            return
        resolved = []
        for fragment in self.frags:
            if not fragment.text:
                resolved.append(fragment)
                continue
            for face, run in text_runs(fragment.text, fragment.fontName):
                item = copy(fragment)
                item.text = run
                item.fontName = face
                resolved.append(item)
        self.frags = resolved
