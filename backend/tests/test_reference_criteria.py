"""Criteria judged from the reference list after the citation check (V-098)."""

import asyncio
from types import SimpleNamespace

import pytest

from app.checks import reference_criteria as rc
from app.config import get_settings
from app.models.enums import ResultOutcome


def levels(*descriptors):
    names = ("BEGINNER", "ACCEPTABLE", "PROFICIENT", "EXEMPLARY")
    return [
        {"name": names[i], "level": i + 1, "points": float(i + 1), "descriptor": d}
        for i, d in enumerate(descriptors)
    ]


# The format's own wording (Rubric for Oral Presentation, T.I.P.).
BIBLIOGRAPHY = SimpleNamespace(
    id=9,
    text="Bibliography",
    levels=levels(
        "The bibliography is done in the correct format but with many errors. (Includes three "
        "major references e.g. science journal articles, books, but no more than two internet sites.)",
        "The bibliography is done in the correct format with some errors. (Includes four major "
        "references e.g. science journal articles, books but no more than two internet sites.)",
        "The bibliography is done in the correct format with few errors. (Includes five major "
        "references e.g. science journal articles, books, but no more than two internet sites.)",
        "The bibliography is done in the correct format with no errors. (Includes more than five "
        "major references e.g. science journal articles, books, but no more than two internet sites.)",
    ),
)
QUALITY = SimpleNamespace(
    id=8,
    text="Quality of References",
    levels=levels(
        "There are virtually no sources that are professionally reliable.",
        "Most of the references are from sources that are not peer-reviewed and have uncertain "
        "reliability.",
        "Although most of the references are professionally legitimate, a few are questionable.",
        "References are primarily peer-reviewed professional journals or other approved sources.",
    ),
)
USE_OF_REFERENCES = SimpleNamespace(
    id=7,
    text="Use of References",
    levels=levels(
        "References are seldom cited to support statements.",
        "Although attributions are occasionally given, many statements seem unsubstantiated.",
        "Professionally legitimate sources that support claims are generally present.",
        "Compelling evidence from professionally legitimate sources is given to support claims.",
    ),
)
INTRODUCTION = SimpleNamespace(
    id=1,
    text="Introduction",
    levels=levels(
        "There is no clear introduction of main topic.",
        "The introduction states the main topic but does not adequately preview the structure.",
        "The introduction states the main topic and previews the structure of the paper.",
        "The introduction is engaging, states the main topic and previews the structure.",
    ),
)


@pytest.fixture(scope="module")
def data():
    return rc.load_reference_data(get_settings().reference_criteria_file)


def test_strategies_follow_the_descriptors_not_the_names(data):
    assert rc.strategy_for(BIBLIOGRAPHY, data) == rc.INVENTORY
    assert rc.strategy_for(QUALITY, data) == rc.QUALITY
    assert rc.strategy_for(USE_OF_REFERENCES, data) is None
    assert rc.strategy_for(INTRODUCTION, data) is None
    renamed = SimpleNamespace(**{**vars(BIBLIOGRAPHY), "text": "Works cited"})
    assert rc.strategy_for(renamed, data) == rc.INVENTORY


def test_count_conditions_are_read_from_each_level(data):
    conditions = rc.count_conditions(BIBLIOGRAPHY, data)
    assert conditions[1] == [("major_at_least", 3), ("internet_at_most", 2)]
    assert conditions[4] == [("major_more_than", 5), ("internet_at_most", 2)]


def test_a_scale_that_only_counts_on_some_levels_is_not_read_as_counts(data):
    partial = SimpleNamespace(
        text="x", levels=levels("Includes three major references.", "Good.", "Better.", "Best.")
    )
    assert rc.count_conditions(partial, data) is None


def profile(**counts):
    base = dict(entries=0, peer_reviewed=0, book=0, other_identified=0, web=0, unclassified=0)
    base.update(counts)
    base["entries"] = base["entries"] or sum(v for k, v in base.items() if k != "entries")
    return rc.Profile(**base)


@pytest.mark.parametrize(
    ("counts", "bounds"),
    [
        ({"peer_reviewed": 4, "book": 3}, (4, 4)),
        ({"peer_reviewed": 5}, (3, 3)),
        ({"peer_reviewed": 7, "web": 3}, (None, None)),
        ({"peer_reviewed": 7, "unclassified": 2}, (4, 4)),
        ({"peer_reviewed": 7, "unclassified": 3}, (None, 4)),
        ({"peer_reviewed": 4, "unclassified": 1}, (2, 3)),
    ],
)
def test_count_level_bounds_cover_every_reading_of_the_unidentified_entries(data, counts, bounds):
    conditions = rc.count_conditions(BIBLIOGRAPHY, data)
    assert rc.inventory_bounds(conditions, profile(**counts)) == bounds


def graded_row(level="EXEMPLARY", outcome=ResultOutcome.passed):
    return SimpleNamespace(
        criterion_id=9,
        outcome=outcome,
        score=100.0,
        detail={"verdict": level, "level": {"name": level}, "reasoning": "grader", "evidence": []},
    )


def test_counts_lower_a_grade_they_do_not_allow(data):
    row = graded_row("EXEMPLARY")
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=4), data)
    assert row.outcome == ResultOutcome.passed
    assert row.detail["verdict"] == "ACCEPTABLE"
    assert row.detail["decision_basis"] == "reference_counts"
    assert row.detail["reference_strategy"]["grader"]["verdict"] == "EXEMPLARY"
    assert "lowered" in row.detail["reasoning"]


def test_counts_never_raise_the_formatting_grade(data):
    row = graded_row("ACCEPTABLE")
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=20), data)
    assert row.detail["verdict"] == "ACCEPTABLE"


def test_counts_no_level_allows_go_to_the_instructor_with_the_counts(data):
    row = graded_row("EXEMPLARY")
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=30, web=9), data)
    assert row.outcome == ResultOutcome.escalated
    assert row.score is None
    assert "9 web sources" in row.detail["reason"]
    assert row.detail["proposed_verdict"] == "EXEMPLARY"
    assert "verdict" not in row.detail


def test_unidentified_entries_that_could_change_the_level_escalate(data):
    row = graded_row("EXEMPLARY")
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=4, unclassified=1), data)
    assert row.outcome == ResultOutcome.escalated
    assert "unidentified" in row.detail["reason"]


def test_an_ungraded_formatting_part_is_not_decided_by_counts_alone(data):
    row = graded_row("EXEMPLARY", outcome=ResultOutcome.escalated)
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=20), data)
    assert row.outcome == ResultOutcome.escalated


def readings(*levels_chosen):
    return [{"reading": i + 1, "level": level} for i, level in enumerate(levels_chosen)]


def test_two_matching_readings_decide_quality():
    row = graded_row("PROFICIENT")
    rc.decide_quality_from(
        row, QUALITY, profile(peer_reviewed=30, book=5), readings("EXEMPLARY", "EXEMPLARY"), None
    )
    assert row.outcome == ResultOutcome.passed
    assert row.detail["verdict"] == "EXEMPLARY"
    assert row.detail["decision_basis"] == "reference_profile"


@pytest.mark.parametrize(
    "chosen",
    [
        ("EXEMPLARY", "PROFICIENT"),
        ("EXEMPLARY", rc.CANNOT_DETERMINE),
        ("EXEMPLARY", "Excellent"),
        (),
    ],
)
def test_anything_but_two_matching_readings_escalates(chosen):
    row = graded_row("PROFICIENT")
    rc.decide_quality_from(row, QUALITY, profile(peer_reviewed=3), readings(*chosen), None)
    assert row.outcome == ResultOutcome.escalated
    assert row.score is None


def test_a_failed_reading_escalates_rather_than_deciding_on_one():
    row = graded_row("PROFICIENT")
    failed = [{"reading": 1, "level": "EXEMPLARY"}, {"reading": 2, "level": None, "error": "x"}]
    rc.decide_quality_from(row, QUALITY, profile(peer_reviewed=3), failed, None)
    assert row.outcome == ResultOutcome.escalated


def test_ranges_set_by_the_format_decide_without_a_model():
    ranges = {"1": [0, 0.1], "2": [0.1, 0.5], "3": [0.5, 0.8], "4": [0.8, 1.0]}
    assert rc.quality_from_ranges(QUALITY, profile(peer_reviewed=9, web=1), ranges) == (4, "ranges")
    assert rc.quality_from_ranges(QUALITY, profile(peer_reviewed=7, unclassified=3), ranges) == (
        None,
        "ranges_inconclusive",
    )
    assert rc.quality_from_ranges(QUALITY, profile(peer_reviewed=9), None) is None


def test_entries_are_classified_only_from_confirmed_lookups_or_their_own_text(data):
    def kind(record, raw="", url=None):
        return rc.classify_entry(record, raw, url, data)

    confirmed = {"status": "existence_confirmed"}
    assert kind({**confirmed, "work_types": ["crossref:journal-article"]}) == "peer_reviewed"
    assert kind({**confirmed, "work_types": ["openlibrary:book"]}) == "book"
    assert kind({**confirmed, "work_types": ["crossref:dataset"]}) == "other_identified"
    # A mismatched record is not this entry's identity.
    assert kind({"status": "metadata_mismatch", "work_types": ["crossref:journal-article"]}) == (
        "unclassified"
    )
    web = "Smith (2020). Shop. Retrieved from https://example.com"
    assert kind({"status": "not_found"}, web, "https://example.com") == "web"
    assert kind({"status": "not_found"}, "Smith (2020). A paper. https://x.org", "https://x.org") == (
        "unclassified"
    )


def test_reference_list_problems_are_named():
    assert "did not finish" in rc.reference_list_problem(None)
    assert "separate" in rc.reference_list_problem({"reference_selection_issues": ["x"]})
    assert "No reference list" in rc.reference_list_problem({"reference_metadata_coverage": []})
    assert rc.reference_list_problem({"reference_metadata_coverage": [{}]}) is None


class ScriptedLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def complete(self, prompt_type, prompt, **context):
        self.calls.append((prompt_type, prompt, context))
        return self.answers.pop(0)


def test_quality_readings_are_separate_numbered_calls(data):
    llm = ScriptedLLM({"level": "EXEMPLARY"}, {"level": "EXEMPLARY"})
    result = asyncio.run(rc.quality_readings(llm, QUALITY, profile(peer_reviewed=3), data, 5))
    assert [r["level"] for r in result] == ["EXEMPLARY", "EXEMPLARY"]
    prompts = [call[1] for call in llm.calls]
    assert len(set(prompts)) == 2, "identical prompts could be served from one cache entry"
    assert [call[2]["reference_reading"] for call in llm.calls] == [1, 2]
    assert all(call[0] == rc.QUALITY_PROMPT_TYPE for call in llm.calls)
