"""Criteria judged from the reference list after the citation check (V-098)."""

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
        "major references e.g. science journal articles, books, but no more than two internet "
        "sites.)",
        "The bibliography is done in the correct format with some errors. (Includes four major "
        "references e.g. science journal articles, books but no more than two internet sites.)",
        "The bibliography is done in the correct format with few errors. (Includes five major "
        "references e.g. science journal articles, books, but no more than two internet sites.)",
        "The bibliography is done in the correct format with no errors. (Includes more than five "
        "major references e.g. science journal articles, books, but no more than two internet "
        "sites.)",
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
        # Apparent web sources might still be major references: only a
        # catalogue's confirmation counts against the list.
        ({"peer_reviewed": 7, "web": 3}, (None, 4)),
        ({"peer_reviewed": 4, "repository_text": 2}, (2, 4)),
        ({"peer_reviewed": 4, "other_identified": 2}, (2, 4)),
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


def test_apparent_web_sources_are_named_but_never_decide_the_level(data):
    row = graded_row("EXEMPLARY")
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=30, web=9), data)
    assert row.outcome == ResultOutcome.escalated
    assert row.score is None
    assert row.detail["proposed_verdict"] == "EXEMPLARY"
    assert "verdict" not in row.detail
    # Information only: they may be journal articles retrieved online.
    reason = row.detail["reason"]
    assert "allows at most 2 internet sites, and 9 entries appear to be web sources" in reason
    assert "Decide whether they count as internet sites under this format" in reason
    # The counts allow anything from no level to the top.
    assert "anything from no level to EXEMPLARY" in reason


@pytest.mark.parametrize(
    "counts",
    [
        {"peer_reviewed": 4, "repository_text": 2},
        {"peer_reviewed": 4, "other_identified": 2},
        {"peer_reviewed": 4, "web": 2},
    ],
)
def test_an_entry_no_catalogue_ruled_out_never_lowers_a_grade(data, counts):
    # 4 journals plus 2 copies of journal articles on a repository: the counts
    # must not lower an EXEMPLARY formatting grade on that.
    row = graded_row("EXEMPLARY")
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(**counts), data)
    assert row.outcome == ResultOutcome.escalated
    assert row.detail.get("verdict") is None


def test_a_list_short_of_major_references_even_at_best_goes_to_the_instructor(data):
    row = graded_row("EXEMPLARY")
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=1, web=1), data)
    assert row.outcome == ResultOutcome.escalated
    assert "appear to be web sources. Decide" not in row.detail["reason"]
    assert "Even counting every entry a catalogue did not confirm" in row.detail["reason"]


def test_unidentified_entries_that_could_change_the_level_escalate(data):
    row = graded_row("EXEMPLARY")
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=4, unclassified=1), data)
    assert row.outcome == ResultOutcome.escalated
    assert "anything from ACCEPTABLE to PROFICIENT" in row.detail["reason"]
    assert "formatting errors" in row.detail["reason"]


def test_the_formatting_grade_keeps_its_caution_where_it_sets_the_level(data):
    row = graded_row("ACCEPTABLE")
    row.detail["support_caution"] = "Quotes cannot confirm this grade."
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=20), data)
    assert row.detail["verdict"] == "ACCEPTABLE"
    assert row.detail["support_caution"] == "Quotes cannot confirm this grade."
    lowered = graded_row("EXEMPLARY")
    lowered.detail["support_caution"] = "Quotes cannot confirm this grade."
    rc.decide_inventory(lowered, BIBLIOGRAPHY, profile(peer_reviewed=4), data)
    assert lowered.detail["verdict"] == "ACCEPTABLE"
    assert "support_caution" not in lowered.detail


@pytest.mark.parametrize(
    "unread",
    ["Includes at least five major references.", "Includes 5-7 major references."],
)
def test_a_level_whose_major_count_is_not_read_skips_the_counts(data, unread):
    scale = SimpleNamespace(
        id=12,
        text="Bibliography",
        levels=levels(
            "Includes three major references and no more than two internet sites.",
            "Includes four major references and no more than two internet sites.",
            unread + " No more than two internet sites.",
            "Includes more than five major references and no more than two internet sites.",
        ),
    )
    assert rc.count_conditions(scale, data) is None


def test_an_ungraded_formatting_part_is_not_decided_by_counts_alone(data):
    row = graded_row("EXEMPLARY", outcome=ResultOutcome.escalated)
    rc.decide_inventory(row, BIBLIOGRAPHY, profile(peer_reviewed=20), data)
    assert row.outcome == ResultOutcome.escalated


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
    assert kind(
        {"status": "not_found"}, "Smith (2020). A paper. https://x.org", "https://x.org"
    ) == ("unclassified")


def test_reference_list_problems_are_named(data):
    def problem(detail):
        return rc.reference_list_problem(detail, data)

    listed = {"reference_metadata_coverage": [{}]}
    assert "did not finish" in problem(None)
    assert "separate" in problem({"reference_selection_issues": ["x"]})
    assert "No reference list" in problem({"reference_metadata_coverage": []})
    assert problem({**listed, "reference_membership_status": "complete"}) is None
    assert problem(listed) is None


@pytest.mark.parametrize(
    ("extra", "said"),
    [
        ({"unread_pages": [61, 62]}, "pages 61, 62"),
        ({"reference_membership_status": "partial"}, "starts and ends"),
        ({"reference_membership_status": "not_established"}, "starts and ends"),
        ({"coverage_reason": "reference_source_projection_unbound"}, "whole reference list"),
    ],
)
def test_a_partly_read_list_is_never_counted(data, extra, said):
    # Entries on an unread page or past an unconfirmed boundary would be
    # missing from the counts, and fewer major references can lower a grade.
    problem = rc.reference_list_problem({"reference_metadata_coverage": [{}], **extra}, data)
    assert problem is not None and said in problem


# --- unconfirmed entries typed from their own citation text ---------------------


def text_kind(data, raw, url=None, isbn=None, status="not_found"):
    return rc.classify_entry({"status": status}, raw, url, data, isbn)


@pytest.mark.parametrize(
    "raw",
    [
        "Wang, R. Y., & Strong, D. M. (1996). Beyond accuracy. Journal of Management Information "
        "Systems, 12(4), 5–33.",
        "Marelli, D. (2022). Designing a try-on application. *Sensors, 22*(10), Article 3832.",
        "Biggs, J. (1996). Enhancing teaching. Higher Education, 32, 347–364.",
        "[11] J. F. Nicol. “Adaptive thermal comfort.” Energy and Buildings, 34(), 2002, "
        "pp. 563–572.",
        "[18] M. Schweiker. “Review of approaches.” Building and Environment, (), 2020.",
        "Loukil, M. (2023). Face shape classification. 2023 IEEE Conference on Artificial "
        "Intelligence, 1–7.",
        "Li, J. (2024). ContraDoc. In Proceedings of the 2024 Annual Meeting, 6509–6523.",
    ],
)
def test_a_journal_or_conference_citation_is_typed_from_its_details(data, raw):
    assert text_kind(data, raw) == "journal_or_conference_text"


@pytest.mark.parametrize(
    "raw",
    [
        "Chen, H. (2026). Rubrics across LLMs (arXiv:2606.08625). arXiv. https://arxiv.org/abs/2606",
        # Looks like a journal volume, but it is a repository copy.
        "Celestin, M. (2024). AR in retail. *Zenodo, 9*(2). https://doi.org/10.5281/zenodo.13879960",
        "Codera, M. (2023). AR in history. Social Science Research Network. https://papers.ssrn.com",
        "Mehta (2022). Face shape classification. ResearchGate. https://www.researchgate.net/x",
        "Nguyen, A. (2019). Trippit. Retrieved July 30, 2023 from https://scholarcommons.scu.edu/x",
        "Reyes, P. (2021). A study of apps. Unpublished master's thesis, Example University.",
    ],
)
def test_a_preprint_repository_copy_or_thesis_is_never_a_major_reference(data, raw):
    assert text_kind(data, raw) == "repository_text"


def test_a_book_is_typed_from_its_isbn_or_edition(data):
    assert text_kind(data, "Kendall, K. (2019). Systems analysis.", isbn="9780134785554") == (
        "book_text"
    )
    assert text_kind(data, "[98] J. Bradbury. Animal Communication. 2nd ed. Sinauer, 2011.") == (
        "book_text"
    )
    # A repository cue outranks an ISBN.
    assert text_kind(data, "A thesis. Unpublished dissertation.", isbn="9780134785554") == (
        "repository_text"
    )


def test_a_web_page_whose_address_the_parser_lost_is_still_a_web_source(data):
    # The address wrapped across lines, so the parser recorded no URL.
    raw = (
        "Silva, S. (2018). 5 Reasons. Retrieved July 27, 2023 from "
        "https://www.northsouthtrav el.com/x"
    )
    assert text_kind(data, raw, url=None) == "web"


@pytest.mark.parametrize(
    "raw",
    [
        # A journal article retrieved from its publisher or DOI address: counting it as an
        # internet site could lower a grade, so it stays unidentified.
        "Coves, A. (2023). Travel-app use. Retrieved July 27, 2023 from https://www.sciencedirect.com/x",
        "Ho, R. (2019). Smart travel apps. Retrieved July 28, 2023 from https://knepublishing.com/x",
        "Sia, P. (2022). Travel apps review. Retrieved July 26, 2023 from https://doi.org/10.1108/JHTI",
        # No address at all.
        "Smith (2020). A report. Retrieved July 2, 2023 from the company office.",
    ],
)
def test_web_typing_stays_narrow_because_it_can_lower_a_grade(data, raw):
    assert text_kind(data, raw) == "unclassified"


def test_a_catalogue_confirmation_outranks_the_citation_text(data):
    record = {"status": "existence_confirmed", "work_types": ["crossref:dataset"]}
    raw = "Smith (2020). A dataset. Journal of Data, 3(1), 1–2."
    assert rc.classify_entry(record, raw, None, data) == "other_identified"


def test_the_profile_and_counts_say_which_entries_were_typed_from_their_text(data):
    records = [{"reference_order_index": i, "status": "not_found"} for i in range(4)]
    citations = {
        0: SimpleNamespace(raw_text="A. Journal of Things, 3(1), 1–9.", url=None, isbn=None),
        1: SimpleNamespace(raw_text="B. arXiv preprint.", url=None, isbn=None),
        2: SimpleNamespace(
            raw_text="C. Retrieved May 1, 2023 from https://shop.example", url=None, isbn=None
        ),
        3: SimpleNamespace(raw_text="D. Untitled note.", url=None, isbn=None),
    }
    profile = rc.reference_profile(records, citations, data)
    assert (profile.major, profile.major_from_text, profile.repository_text) == (1, 1, 1)
    assert (profile.web, profile.unclassified, profile.unclassified_entries) == (1, 1, [4])
    detail = profile.as_detail()
    assert detail["journal_or_conference_papers_by_citation_text"] == 1
    assert detail["entry_numbers_typed_by_citation_text"] == {
        "journal_or_conference_text": [1],
        "repository_text": [2],
        "web": [3],
    }
    sentence = rc._counts_sentence(profile, data)
    assert sentence.startswith("Reference list of 4 entries: 1 journal or conference papers")
    assert "(1 of them classified from how the entry is written)" in sentence
    assert "1 preprints, repository copies or theses" in sentence
    assert "and 1 of unknown type." in sentence
    # Nothing in the wording doubts that a source exists.
    assert "catalogue" not in sentence and "identified" not in sentence
    assert profile.uncertain_entries == [2, 3, 4]


def test_majors_read_from_citation_text_count_toward_the_inventory(data):
    conditions = rc.count_conditions(BIBLIOGRAPHY, data)
    typed = profile(journal_or_conference_text=4, book_text=2, web=1)
    assert rc.inventory_bounds(conditions, typed) == (4, 4)


# --- quality: decided only where the counts prove one level --------------------


def test_on_the_format_the_quality_levels_are_never_proven_by_counts_alone(data):
    # Every entry a catalogue did not confirm could be a paper, so the share can
    # always reach the top: "most ... not peer-reviewed" is never proven, and a
    # mostly-paper list fits both "most legitimate" and "primarily peer-reviewed".
    for counts in (
        {"journal_or_conference_text": 2, "web": 11, "repository_text": 2, "unclassified": 4},
        {"peer_reviewed": 103, "web": 3, "unclassified": 24},
        {"journal_or_conference_text": 11, "repository_text": 3, "unclassified": 7},
        {"peer_reviewed": 8, "web": 12},
    ):
        row = graded_row("EXEMPLARY")
        rc.decide_quality(row, QUALITY, profile(**counts), data)
        assert row.outcome == ResultOutcome.escalated, counts
        assert row.detail.get("verdict") is None


def test_counts_that_fit_two_levels_go_to_the_instructor_naming_both(data):
    row = graded_row("EXEMPLARY")
    rc.decide_quality(row, QUALITY, profile(peer_reviewed=103, web=3, unclassified=24), data)
    assert "PROFICIENT and EXEMPLARY" in row.detail["reason"]
    checks = row.detail["reference_strategy"]["level_checks"]
    assert [ordinal for ordinal, check in checks.items() if check["fits"]] == ["3", "4"]


def test_counts_that_confirm_no_level_go_to_the_instructor(data):
    row = graded_row("PROFICIENT")
    rc.decide_quality(row, QUALITY, profile(peer_reviewed=4, web=4, unclassified=12), data)
    assert row.outcome == ResultOutcome.escalated
    assert "do not confirm any one level" in row.detail["reason"]


def test_counts_that_fit_exactly_one_level_decide_it(data):
    # A scale where only one level's words imply a share.
    scale = SimpleNamespace(
        id=13,
        text="Sources",
        levels=levels(
            "Weak sources.",
            "Fair sources.",
            "Most of the references are professionally legitimate.",
            "Excellent sources.",
        ),
    )
    row = graded_row("EXEMPLARY")
    rc.decide_quality(row, scale, profile(peer_reviewed=15, unclassified=5), data)
    assert row.detail["verdict"] == "PROFICIENT"
    assert row.detail["decision_basis"] == "reference_profile"
    assert row.detail["reference_strategy"]["strategy"] == rc.QUALITY
    assert "Only this level's wording fits these counts" in row.detail["reasoning"]


def test_a_scale_whose_words_imply_no_share_is_never_decided(data):
    vague = SimpleNamespace(
        id=11,
        text="Sources",
        levels=levels("Weak sources.", "Fair sources.", "Good sources.", "Excellent sources."),
    )
    row = graded_row("PROFICIENT")
    rc.decide_quality(row, vague, profile(peer_reviewed=30), data)
    assert row.outcome == ResultOutcome.escalated


def test_a_share_of_exactly_one_half_proves_neither_side(data):
    # 10 papers and 10 unconfirmed entries: "more than half" is not proven.
    ordinal = next(int(lv["level"]) for lv in QUALITY.levels if lv["name"] == "PROFICIENT")
    check = rc.quality_level_fits(QUALITY, ordinal, profile(peer_reviewed=10, web=10), data)
    assert check["fits"] is False and check["share_bounds"] == [0.5, 1.0]


def test_quality_never_calls_a_model(data):
    # No model client reaches this strategy: it decides from counts alone.
    import inspect

    assert "llm" not in inspect.signature(rc.apply_reference_strategies).parameters
    assert not hasattr(rc, "quality_readings")


@pytest.mark.parametrize(
    ("level", "counts", "fits"),
    [
        # Apparent web pages could still be papers, so "virtually no" is unproven.
        ("BEGINNER", {"web": 19, "journal_or_conference_text": 1}, False),
        ("EXEMPLARY", {"peer_reviewed": 15, "unclassified": 5}, True),
        ("EXEMPLARY", {"peer_reviewed": 8, "unclassified": 12}, False),
        ("ACCEPTABLE", {"web": 8, "other_identified": 4, "peer_reviewed": 8}, False),
    ],
)
def test_quality_level_checks_follow_the_levels_own_words(data, level, counts, fits):
    ordinal = next(int(lv["level"]) for lv in QUALITY.levels if lv["name"] == level)
    assert rc.quality_level_fits(QUALITY, ordinal, profile(**counts), data)["fits"] is fits


@pytest.mark.parametrize("decided", [True, False])
def test_a_stored_rows_decimal_score_can_be_saved_with_the_record(data, decided):
    # Rows read from the database carry a Decimal score; the record kept on
    # the row and in the audit event is JSON, which cannot hold one.
    import json
    from decimal import Decimal

    row = graded_row("EXEMPLARY")
    row.score = Decimal("3.00")
    counts = profile(peer_reviewed=4) if decided else profile(peer_reviewed=30, web=9)
    rc.decide_inventory(row, BIBLIOGRAPHY, counts, data)
    json.dumps(row.detail)
    assert row.detail["reference_strategy"]["grader"]["score"] == 3.0


def test_entry_numbers_run_together_without_dashes():
    assert rc._numbers([3, 5, 6, 7, 9, 12, 13]) == "3, 5 to 7, 9, 12 to 13"


def test_every_quality_message_shows_the_range_it_used(data):
    row = graded_row("EXEMPLARY")
    rc.decide_quality(row, QUALITY, profile(peer_reviewed=103, web=3, unclassified=24), data)
    assert (
        "Between 103 and 130 of the 130 entries can be journal or conference papers"
        in (row.detail["reason"])
    )


def test_an_escalated_row_does_not_show_the_old_passes_as_if_they_graded_it(data):
    row = graded_row("PROFICIENT")
    row.detail["votes"] = ["PROFICIENT", "PROFICIENT"]
    row.detail["agreement"] = 1.0
    rc.decide_quality(row, QUALITY, profile(peer_reviewed=103, web=3, unclassified=24), data)
    assert "votes" not in row.detail and "agreement" not in row.detail
    assert row.detail["reference_strategy"]["grader"]["votes"] == ["PROFICIENT", "PROFICIENT"]
