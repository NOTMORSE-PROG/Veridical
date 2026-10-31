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
    pair_confusion,
    quote_fragments,
    specific_agreement,
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


def test_repeat_scores_skip_stopped_runs_and_use_the_grader_level(tmp_path):
    import json

    from tools.accuracy.grading_repeat_scores import score_repeats

    levels = [
        {"name": n, "points": p} for n, p in (("Beginner", 1), ("Proficient", 3), ("Exemplary", 4))
    ]
    criteria = [
        {"id": 1, "text": "Intro", "levels": levels},
        {"id": 2, "text": "Tone", "levels": levels},
    ]
    (tmp_path / "setup.json").write_text(json.dumps({"criteria": criteria}))
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "thesis.json").write_text(json.dumps({"pages": {"1": "The study previews chapters."}}))

    def record(repeat, intro, tone_outcome, overridden=False):
        intro_detail = {
            "level": {"name": intro},
            "support_check": {"status": "supported"},
            "evidence": [{"quote": "previews chapters", "anchor": "page 1"}],
        }
        if overridden:
            intro_detail = {
                **intro_detail,
                "level": {"name": "Beginner"},
                "original_result": {"level": {"name": intro}},
                "resolution": {},
            }
        return {
            "document": "thesis",
            "llm_events": [
                {"event_type": "llm_call", "payload": {"prompt_type": "semantic_grading"}}
            ],
            "results": [
                {
                    "criterion_id": 1,
                    "kind": "semantic",
                    "outcome": "passed",
                    "detail": intro_detail,
                },
                {
                    "criterion_id": 2,
                    "kind": "semantic",
                    "outcome": tone_outcome,
                    "detail": {
                        "level": {"name": "Proficient"},
                        "support_check": {"status": "unsupported"},
                    },
                },
            ],
        }

    for number, data in (
        (1, record(1, "Proficient", "passed", overridden=True)),
        (2, record(2, "Proficient", "escalated")),
    ):
        folder = tmp_path / "repeats" / f"r{number}"
        folder.mkdir(parents=True)
        (folder / "thesis.json").write_text(json.dumps(data))
    stopped = tmp_path / "repeats" / "r2" / "stopped"
    stopped.mkdir()
    (stopped / "other.json").write_text(
        json.dumps(record(2, "Exemplary", "passed") | {"document": "other"})
    )
    # A thesis the product refused to grade before any call: escalated in
    # every repeat, but that is not the grader agreeing with itself.
    unread = {
        "document": "unread",
        "llm_events": [],
        "results": [
            {
                "criterion_id": cid,
                "kind": "semantic",
                "outcome": "escalated",
                "detail": {"coverage_reason": "source_reading_order_unresolved"},
            }
            for cid in (1, 2)
        ],
    }
    for number in (1, 2):
        (tmp_path / "repeats" / f"r{number}" / "unread.json").write_text(json.dumps(unread))
    (pages / "unread.json").write_text(json.dumps({"pages": {}}))

    rows, details = score_repeats(
        tmp_path, pages, image_page_max_chars=5, targets={"repeat_all_equal": 0.95}
    )
    by_metric = {row.metric: row for row in rows}
    stable = by_metric["every repeat gave the same level"]
    assert (stable.numerator, stable.denominator) == (1, 1)
    assert stable.extra["decided_escalated_flips"] == [["thesis", "Tone"]]
    assert stable.extra["target"] == 0.95
    support = by_metric["automatic grades whose support check passed (repeats)"]
    assert (support.numerator, support.denominator) == (2, 3)
    grounded = by_metric["quotes found on the cited page (repeats)"]
    assert (grounded.numerator, grounded.denominator) == (2, 2)
    assert details["stopped"] == ["repeats/r2/stopped/other.json"]
    assert details["not_graded_by_the_model"] == {"unread": ["source_reading_order_unresolved"]}
    # Tone went from decided to escalated: a change for the instructor, but
    # the grader proposed the same level both times.
    strict = by_metric["every repeat gave the same result (an escalation flip counts as a change)"]
    assert (strict.numerator, strict.denominator) == (1, 2)
    proposed = by_metric["every repeat proposed the same level (kept or escalated)"]
    assert (proposed.numerator, proposed.denominator) == (2, 2)
    # One thesis is one cluster, so nothing here can be more than indicative.
    assert stable.extra["clusters"] == 1 and stable.indicative
    by_strategy = by_metric["automatic grades whose support check passed: single passage"]
    assert (by_strategy.numerator, by_strategy.denominator) == (2, 3)


def test_strict_stability_counts_a_steady_escalation_as_the_same_result():
    rows = grades("A", [None] * 3, [False] * 3) + grades(
        "B", ["Proficient", None, "Proficient"], [True, False, True]
    )
    summary = stability(rows, SCALE, repeats=3)
    assert (summary.strict_all_equal, summary.strict_units) == (1, 2)


def test_an_off_scale_level_is_listed_and_counts_as_a_change():
    summary = stability(grades("A", ["Proficient", "passed", "Proficient"]), SCALE, repeats=3)
    assert summary.off_scale_units == ["A"]
    assert summary.units == 0
    assert (summary.strict_all_equal, summary.strict_units) == (0, 1)


def test_proposed_levels_are_compared_even_when_a_repeat_escalated():
    rows = [
        Grade("A", 0, "Proficient", True, "Proficient"),
        Grade("A", 1, None, False, "Proficient"),
        Grade("A", 2, "Proficient", True, "Proficient"),
    ]
    summary = stability(rows, SCALE, repeats=3)
    assert (summary.proposed_all_equal, summary.proposed_units) == (1, 1)


def test_pair_confusion_is_symmetric_and_shows_a_single_level_grader():
    table = pair_confusion([["Proficient"] * 3, ["Proficient", "Exemplary", "Proficient"]], SCALE)
    assert table["Proficient"]["Proficient"] == 2 * (3 + 1)
    assert table["Proficient"]["Exemplary"] == table["Exemplary"]["Proficient"] == 2
    assert sum(sum(row.values()) for row in table.values()) == 2 * 6


def test_specific_agreement_by_hand():
    # Unit 1: three Proficient. Unit 2: two Proficient, one Exemplary.
    # Proficient: (3*2 + 2*1) / (3*2 + 2*2) = 8/10. Exemplary: 0 / (1*2) = 0.
    result = specific_agreement(
        [["Proficient"] * 3, ["Proficient", "Exemplary", "Proficient"]], SCALE
    )
    assert result["Proficient"] == pytest.approx(0.8)
    assert result["Exemplary"] == 0
    assert result["Beginner"] is None


@pytest.mark.parametrize(
    ("detail", "basis"),
    [
        ({"votes": ["Proficient", "Proficient"]}, "passes agreed"),
        ({"votes": ["Proficient", "Exemplary", "Proficient"]}, "tie-break majority"),
        ({"votes": ["A", "B"], "decision_basis": "single_verified_pass"}, "single_verified_pass"),
        ({}, "unrecorded"),
    ],
)
def test_decision_basis_comes_from_the_votes_when_none_is_recorded(detail, basis):
    from tools.accuracy.grading_repeat_scores import decision_basis

    assert decision_basis(detail) == basis


def test_strategy_uses_the_products_own_evidence_rules():
    from tools.accuracy.grading_repeat_scores import (
        LOCATABLE,
        SINGLE_PASSAGE,
        WHOLE_DOCUMENT,
        strategy,
    )

    levels = [
        {"name": "Acceptable", "level": 2, "points": 2, "descriptor": "States the topic only."},
        {
            "name": "Proficient",
            "level": 3,
            "points": 3,
            "descriptor": "States the topic and previews the structure of the paper.",
        },
    ]
    intro = {"text": "Introduction", "levels": levels}
    assert strategy(intro, {"verdict": "Proficient"}) == LOCATABLE
    assert strategy(intro, {"proposed_verdict": "Proficient"}) == LOCATABLE
    assert strategy(intro, {"verdict": "Acceptable"}) == SINGLE_PASSAGE
    guarded = {"verdict": "Acceptable", "support_check": {"scope_guard": "x"}}
    assert strategy(intro, guarded) == WHOLE_DOCUMENT


def test_mixed_level_scales_are_refused_before_any_repeat():
    from tools.accuracy.grading_repeat_scores import common_scale

    four = [{"name": n, "points": p} for p, n in enumerate(SCALE, start=1)]
    three = four[:3]
    assert common_scale([{"levels": four}, {"levels": four}, {"levels": None}]) == tuple(SCALE)
    with pytest.raises(ValueError, match="2 level scales"):
        common_scale([{"levels": four}, {"levels": three}])
