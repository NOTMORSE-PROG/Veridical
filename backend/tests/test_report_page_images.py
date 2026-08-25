"""V-070 tests: `app.report.page_images` -- rendering a flag's actual
submitted page as a raster image for the PDF export, with the flagged
region marked when a bounding box was recoverable. Same synthetic-PDF/
`PdfBuilder` convention as `test_ingest_regions.py` (which this module's
own anchor recovery is built on) -- no binary fixtures, deterministic.
"""

from pathlib import Path

import pymupdf
import pytest

from app.config import get_settings
from app.ingest.pdf import extract_document
from app.report.page_images import (
    _draw_disclosure_band,
    _merge_line_boxes,
    build_page_evidence_images,
)
from app.report.schemas import FlagSummaryOut
from tests.test_ingest_pdf import PdfBuilder

_REAL_QUOTE = "Before a student can defend their capstone the format must be checked."
_SYNTHESIZED_EXCERPT = "n=5, M=4.20, SD=0.37 (Instructors)"


def _flag(
    id: int,
    page_anchor: str,
    evidence_excerpt: str = _REAL_QUOTE,
    check_kind: str = "citation_integrity",
) -> FlagSummaryOut:
    return FlagSummaryOut(
        id=id,
        check_kind=check_kind,
        severity="high",
        criterion_text=None,
        evidence_excerpt=evidence_excerpt,
        page_anchor=page_anchor,
        overridden=False,
    )


def _fixture_pdf(tmp_path: Path) -> Path:
    b = PdfBuilder()
    b.new_page().line("CHAPTER 1 INTRODUCTION", bold=True)
    b.line(_REAL_QUOTE)
    b.new_page().line("CHAPTER 2 METHODS", bold=True)
    b.line("This chapter presents how the tool was planned and developed.")
    b.new_page().line("REFERENCES", bold=True)
    b.line("Reyes, J. P., & Cruz, M. A. (2023). Assessing capstone readiness.")
    return b.save(tmp_path / "page_images.pdf")


@pytest.fixture()
def doc_and_tree(tmp_path):
    path = _fixture_pdf(tmp_path)
    extraction = extract_document(str(path), get_settings())
    doc = pymupdf.open(str(path))
    yield doc, extraction.section_tree
    doc.close()


def _build(doc, tree, flags, **overrides):
    kwargs = dict(
        source_format="pdf",
        unavailable_reason=None,
        llm_mode="real",
        settings=get_settings(),
    )
    kwargs.update(overrides)
    return build_page_evidence_images(doc, tree, flags, **kwargs)


def test_page_bbox_flag_gets_a_real_png_with_no_reason(doc_and_tree):
    doc, tree = doc_and_tree
    flags = [_flag(1, "p. 1", _REAL_QUOTE)]
    result = _build(doc, tree, flags)
    image = result[1]
    assert image.image_png is not None
    assert image.image_png.startswith(b"\x89PNG")
    assert image.page == 1
    assert image.reason is None


def test_page_only_flag_gets_a_png_and_an_honest_reason(doc_and_tree):
    """A synthesized excerpt (never on the page as written) recovers no
    bbox (`app.ingest.regions`'s own measured behavior) -- the export must
    still show the real page, with a stated reason for the missing mark,
    never a fabricated box."""
    doc, tree = doc_and_tree
    flags = [_flag(2, "p. 1", _SYNTHESIZED_EXCERPT)]
    result = _build(doc, tree, flags)
    image = result[2]
    assert image.image_png is not None
    assert image.page == 1
    assert image.reason is not None
    assert "could not be located automatically" in image.reason


def test_whole_document_flag_shows_only_page_one_with_a_stated_scope(doc_and_tree):
    doc, tree = doc_and_tree
    flags = [_flag(3, "whole document", "irrelevant")]
    result = _build(doc, tree, flags)
    image = result[3]
    assert image.image_png is not None
    assert image.page == 1
    assert "entire" in image.reason and "3-page document" in image.reason


def test_section_flag_shows_the_chapters_first_page_with_a_stated_scope(doc_and_tree):
    doc, tree = doc_and_tree
    flags = [_flag(4, "CHAPTER 2 METHODS", "irrelevant")]
    result = _build(doc, tree, flags)
    image = result[4]
    assert image.image_png is not None
    assert image.page == 2
    assert "chapter beginning on page 2" in image.reason


def test_reference_list_flag_shows_the_page_with_a_stated_reason(doc_and_tree):
    doc, tree = doc_and_tree
    flags = [_flag(5, "reference list", "Reyes, J. P., & Cruz, M. A. (2023)...")]
    result = _build(doc, tree, flags)
    image = result[5]
    assert image.image_png is not None
    assert image.page == 3
    assert "reference entry could not be boxed" in image.reason


def test_unavailable_anchor_gets_no_image_and_a_stated_reason(doc_and_tree):
    doc, tree = doc_and_tree
    flags = [_flag(6, "p. 999", "irrelevant")]
    result = _build(doc, tree, flags)
    image = result[6]
    assert image.image_png is None
    assert image.reason


def test_docx_source_gets_no_image_for_any_flag_and_says_so(doc_and_tree):
    """DECIDED 2026-08-16, V-070.md Q3: PDF sources only. A DOCX manuscript
    never opens a doc at all -- `doc` is None here on purpose, matching
    what `_open_manuscript_source` returns for a real DOCX."""
    _doc, tree = doc_and_tree
    flags = [_flag(7, "p. 1", _REAL_QUOTE), _flag(8, "¶3", "irrelevant")]
    result = _build(None, tree, flags, source_format="docx", unavailable_reason=None)
    for flag_id in (7, 8):
        assert result[flag_id].image_png is None
        assert "submitted as a DOCX" in result[flag_id].reason


def test_purged_manuscript_states_the_purge_reason_verbatim(doc_and_tree):
    """AC3: a purged source states its reason explicitly -- reusing
    `_open_manuscript_source`'s own wording verbatim, never re-derived, so
    the export and the on-screen viewer never disagree about why."""
    _doc, tree = doc_and_tree
    flags = [_flag(9, "p. 1", _REAL_QUOTE)]
    purge_reason = (
        "This manuscript's source file was purged on 2026-08-01 and can no longer be viewed."
    )
    result = _build(None, tree, flags, source_format="pdf", unavailable_reason=purge_reason)
    assert result[9].image_png is None
    assert result[9].reason == purge_reason


def test_two_flags_sharing_a_page_and_box_render_only_once(doc_and_tree, monkeypatch):
    """Edge case: 'A 34-flag report where several flags share a page --
    deduplicate the render.' Two flags anchored to the identical excerpt on
    the identical page must produce byte-identical output from exactly one
    `get_pixmap` call, not two."""
    doc, tree = doc_and_tree
    calls = []
    real_get_pixmap = pymupdf.Page.get_pixmap

    def counting_get_pixmap(self, *args, **kwargs):
        calls.append(1)
        return real_get_pixmap(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", counting_get_pixmap)

    flags = [_flag(10, "p. 1", _REAL_QUOTE), _flag(11, "p. 1", _REAL_QUOTE)]
    result = _build(doc, tree, flags)
    assert result[10].image_png == result[11].image_png
    assert len(calls) == 1


def test_max_page_images_cap_skips_honestly_past_the_limit(doc_and_tree):
    doc, tree = doc_and_tree
    settings = get_settings().model_copy(update={"export_max_page_images": 1})
    flags = [_flag(12, "p. 1", _REAL_QUOTE), _flag(13, "CHAPTER 2 METHODS", "irrelevant")]
    result = _build(doc, tree, flags, settings=settings)
    assert result[12].image_png is not None
    assert result[13].image_png is None
    assert "rendered-page limit" in result[13].reason
    assert "1" in result[13].reason


def test_different_flags_sharing_a_page_render_once_and_count_as_one_page(
    doc_and_tree, monkeypatch
):
    """`backend-critic` finding, 2026-08-25: keying the render cache by
    (page, boxes) meant three DIFFERENT flags anchored to the SAME page
    with three DIFFERENT (real, distinct) excerpts each triggered their
    own `get_pixmap` call and consumed three separate slots in
    `export_max_page_images` -- so a single heavily-flagged page could
    exhaust the whole cap, and a genuinely different SECOND page (the only
    other page in a 2-page document) would then be skipped as
    "rendered-page limit reached" even though only 2 real pages exist.
    Reproduced here with the critic's own exact shape: 3 distinct excerpts
    on page 1, 1 flag on page 2, cap=3 -- with the fix, page 1 costs
    exactly ONE render (all three flags share it) and page 2 fits easily
    inside the remaining budget."""
    doc, tree = doc_and_tree
    calls = []
    real_get_pixmap = pymupdf.Page.get_pixmap

    def counting_get_pixmap(self, *args, **kwargs):
        calls.append(self.number)
        return real_get_pixmap(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", counting_get_pixmap)

    settings = get_settings().model_copy(update={"export_max_page_images": 3})
    flags = [
        _flag(20, "p. 1", _REAL_QUOTE),
        _flag(21, "p. 1", "This chapter presents how the tool was planned and developed."),
        _flag(22, "p. 1", _SYNTHESIZED_EXCERPT),
        _flag(23, "p. 2", "This chapter presents how the tool was planned and developed."),
    ]
    result = _build(doc, tree, flags, settings=settings)

    assert result[20].image_png is not None
    assert result[21].image_png is not None
    assert result[22].image_png is not None
    assert result[20].image_png == result[21].image_png == result[22].image_png
    assert result[23].image_png is not None, (
        "page 2 was skipped as 'limit reached' even though only 2 real pages "
        "exist -- the cache is still counting renders per excerpt, not per page"
    )
    assert len(calls) == 2, f"expected exactly 2 renders (one per real page), got {len(calls)}"


def test_fake_llm_mode_stamps_a_visibly_different_image_than_real(doc_and_tree):
    """AC5: a fake-mode run's page images must be marked, same disclosure
    BUG-049 already established for the header/footer -- asserted here by
    the one thing a raster comparison CAN honestly assert (the stamped
    banner changes the rendered bytes), not by OCR-ing the PNG."""
    doc, tree = doc_and_tree
    flags = [_flag(14, "p. 1", _REAL_QUOTE)]
    real = _build(doc, tree, flags, llm_mode="real")[14]
    fake = _build(doc, tree, flags, llm_mode="fake")[14]
    assert real.image_png != fake.image_png


def _band_has_visible_text(image_png: bytes, fg_rgb_255: tuple[int, int, int]) -> bool:
    """Pixel-level check for the disclosure band's TEXT, not just its
    background -- `professor` finding, 2026-08-25: `page.insert_textbox`
    does not raise or clip on overflow, it silently draws NOTHING and
    returns a negative number, so a byte-level `!=` comparison (the two
    tests above) can pass even when the band's background color changed
    but no text was ever drawn. Samples the bottom 15% of the image for
    ANY pixel close to the tone's own foreground color -- present only if
    real glyphs were rendered."""
    pix = pymupdf.Pixmap(image_png)
    band_top = int(pix.height * 0.85)
    tolerance = 12
    for y in range(band_top, pix.height, 2):
        for x in range(0, pix.width, 4):
            r, g, b = pix.pixel(x, y)
            if (
                abs(r - fg_rgb_255[0]) <= tolerance
                and abs(g - fg_rgb_255[1]) <= tolerance
                and abs(b - fg_rgb_255[2]) <= tolerance
            ):
                return True
    return False


def test_unknown_llm_mode_disclosure_text_is_actually_visible_not_just_the_band_background(
    doc_and_tree,
):
    """`professor` finding, 2026-08-25, reproduced independently: the
    'unknown' disclosure string, at the fontsize this module computes for
    a real A4-height page, overflowed the ORIGINAL fixed-multiplier band
    height (`fontsize * 2.9`) -- `insert_textbox` returned a negative
    "overflow" number and drew nothing, while the band's background still
    painted, producing a visually plausible but textless banner. Fixed by
    `_draw_disclosure_band` (measures via real font-metric wrapping, then
    verifies against `insert_textbox`'s own return value and grows the
    band if the estimate undershot). Asserted here at the pixel level --
    the one honest way to distinguish 'background painted' from 'text
    rendered' for a raster image, per the reviewer's own suggested fix."""
    doc, tree = doc_and_tree
    flags = [_flag(16, "p. 1", _REAL_QUOTE)]
    image = _build(doc, tree, flags, llm_mode="unknown")[16]
    from app.report.export import _TONE_COLORS

    fg = _TONE_COLORS["caution"][1]
    fg_rgb_255 = (round(fg.red * 255), round(fg.green * 255), round(fg.blue * 255))
    assert _band_has_visible_text(image.image_png, fg_rgb_255), (
        "no pixels matching the caution foreground color were found in the "
        "disclosure band -- the background painted but the text did not render"
    )


def test_unknown_llm_mode_also_stamps_distinctly_from_real(doc_and_tree):
    """BUG-113: an `unknown` (pre-migration) run gets its OWN distinct
    disclosure, never silently treated the same as `real`."""
    doc, tree = doc_and_tree
    flags = [_flag(15, "p. 1", _REAL_QUOTE)]
    real = _build(doc, tree, flags, llm_mode="real")[15]
    unknown = _build(doc, tree, flags, llm_mode="unknown")[15]
    assert real.image_png != unknown.image_png


def test_reuse_flag_discloses_the_matched_manuscript_is_not_shown(doc_and_tree):
    """`ui-designer` finding, 2026-08-25: the ticket's own named edge case
    ("Reuse/originality flags anchor to two documents... Either show both
    under the bounded-excerpt rule or state that only the local side is
    shown") wasn't actually stated anywhere -- the span note explained
    WHICH page of the local document is shown, not that the MATCHED
    manuscript (the other half of what the flag is about) is entirely
    absent from this export. Only applies to `originality_reuse` flags
    anchored to a chapter/whole-document span -- a `section`/`whole_document`
    region can occur for other check kinds too (in principle), where this
    note would be a non-sequitur."""
    doc, tree = doc_and_tree
    reuse_flag = _flag(30, "whole document", "irrelevant", check_kind="originality_reuse")
    other_flag = _flag(31, "CHAPTER 2 METHODS", "irrelevant", check_kind="internal_agreement")
    result = _build(doc, tree, [reuse_flag, other_flag])
    assert "other manuscript in this comparison is not included" in result[30].reason
    assert "other manuscript in this comparison is not included" not in result[31].reason


def test_draw_disclosure_band_grows_and_retries_when_the_initial_estimate_undershoots(
    monkeypatch,
):
    """`professor` finding, 2026-08-25: `page.insert_textbox` silently
    draws NOTHING and returns a negative number when the text overflows
    its box -- reproduced live for the real 'unknown' disclosure string
    on a real A4 page (measured: -2.57 at the ORIGINAL fixed-multiplier
    height). `_draw_disclosure_band`'s fix is a measure-then-VERIFY loop;
    this test proves the retry/grow path itself works by forcing the
    initial estimate to undershoot on purpose (monkeypatching
    `_wrapped_line_count` to always return 1, regardless of the real
    string), rather than depending on any one string/page-width
    combination happening to sit on the exact overflow boundary."""
    import app.report.page_images as page_images_module

    monkeypatch.setattr(page_images_module, "_wrapped_line_count", lambda *a, **k: 1)

    doc = pymupdf.open()
    page = doc.new_page()
    long_text = (
        "AI mode unknown: this run predates AI-mode tracking, so whether it used real "
        "or simulated AI results can't be confirmed, and this sentence is deliberately "
        "extended further to guarantee it cannot possibly fit inside a single-line-sized "
        "band no matter how the real wrapping estimate would have sized it."
    )
    _draw_disclosure_band(page, long_text, 16.0, (0.48, 0.21, 0.4), (0.96, 0.91, 0.95))
    pix = doc[0].get_pixmap(dpi=150)
    doc.close()

    assert _band_has_visible_text(pix.tobytes("png"), (122, 53, 102)), (
        "the retry/grow loop did not recover from a deliberately undersized initial "
        "estimate -- text should still be visible after growing the band"
    )


def test_merge_line_boxes_unions_same_line_word_runs():
    """`page.search_for()` returns one rect per word-run on a matched
    line, not one rect for the whole phrase (measured live against a real
    excerpt) -- this collapses same-line runs into one continuous rect."""
    same_line = [(10.0, 100.0, 30.0, 112.0), (35.0, 100.0, 60.0, 112.0)]
    other_line = [(10.0, 80.0, 40.0, 92.0)]
    merged = _merge_line_boxes(same_line + other_line, tolerance=2.0)
    assert len(merged) == 2
    line_box = next(b for b in merged if b[1] == 100.0)
    assert line_box == (10.0, 100.0, 60.0, 112.0)
