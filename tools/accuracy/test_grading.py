"""Grading proxies: where a quote sits in the source, and how repeats agree."""

import pytest
from tools.accuracy.lanes.grading import (
    IMAGE_PAGE,
    NO_PAGE_ANCHOR,
    NOT_FOUND,
    ON_ANCHOR_PAGE,
    OTHER_PAGE,
    Grade,
    classify_quote,
    page_number,
    quote_fragments,
    stability,
)

PAGES = {
    1: "Chapter 1\nIntroduction\nThis study builds a self-\nconsistency check for theses.",
    2: "The respon-\ndents were 120 “faculty” members ﬁling forms.",
    3: "   ",
}
SCALE = ["Beginner", "Acceptable", "Proficient", "Exemplary"]


def where(quote, anchor="page 1"):
    return classify_quote(quote, anchor, PAGES, image_page_max_chars=40)


def test_a_quote_on_its_cited_page_is_found_across_line_breaks():
    assert where("Chapter 1 Introduction This study") == ON_ANCHOR_PAGE


def test_a_hyphen_at_a_line_break_matches_with_or_without_the_hyphen():
    assert where("self-consistency check") == ON_ANCHOR_PAGE
    assert where("selfconsistency check") == ON_ANCHOR_PAGE
    assert where("The respondents were 120", "page 2") == ON_ANCHOR_PAGE


def test_curly_quotes_and_ligatures_match_their_plain_forms():
    assert where('120 "faculty" members filing', "page 2") == ON_ANCHOR_PAGE


def test_an_ellipsis_splits_a_quote_into_parts_that_must_come_in_order():
    assert where("Chapter 1 ... check for theses") == ON_ANCHOR_PAGE
    assert where("check for theses … Chapter 1") == NOT_FOUND
    assert quote_fragments("a ... b … c") == ["a", "b", "c"]


def test_a_quote_on_another_page_is_reported_as_a_wrong_anchor():
    assert where("faculty", "page 1") == OTHER_PAGE


def test_an_invented_or_stitched_quote_is_not_found():
    assert where("Chapter 1 Introduction Background and Rationale") == NOT_FOUND
    assert where("") == NOT_FOUND


def test_a_cited_page_without_a_text_layer_goes_to_a_person():
    assert where("anything at all", "page 3") == IMAGE_PAGE


def test_a_non_page_anchor_is_not_scored_as_missing():
    assert where("Introduction", "para 4") == NO_PAGE_ANCHOR
    assert page_number("Page 12") == 12
    assert page_number(None) is None


def grades(unit, levels, decided=None):
    decided = decided or [True] * len(levels)
    return [
        Grade(unit, repeat, level, ok)
        for repeat, (level, ok) in enumerate(zip(levels, decided, strict=True))
    ]


def test_stability_counts_agreement_only_where_every_repeat_decided():
    rows = (
        grades("A", ["Proficient"] * 3)
        + grades("B", ["Proficient", "Exemplary", "Proficient"])
        + grades("C", ["Beginner", "Exemplary", "Proficient"])
        + grades("D", ["Proficient", None, "Proficient"], [True, False, True])
        + grades("E", ["Proficient", "Proficient"])
    )
    summary = stability(rows, SCALE, repeats=3)
    assert summary.units == 3
    assert summary.all_equal == 1
    assert (summary.pairs, summary.pairs_exact, summary.pairs_within_one) == (9, 4, 7)
    assert summary.decided_flips == ["D"]
    assert summary.incomplete_units == ["E"]
    assert summary.prevalence["Proficient"] == pytest.approx(6 / 9)
    assert summary.prevalence["Acceptable"] == 0
    assert summary.fleiss_kappa is not None and summary.ac1 is not None


def test_stability_with_nothing_decided_reports_no_coefficients():
    summary = stability(grades("A", [None] * 3, [False] * 3), SCALE, repeats=3)
    assert summary.units == 0
    assert summary.fleiss_kappa is None and summary.ac1 is None
