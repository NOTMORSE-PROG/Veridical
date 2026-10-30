"""V-092: citation recency note. Known-answer tests for reading a source's
publication year, the window boundary, the manuscript's own year, and the
summary. Pure functions, no database."""

from types import SimpleNamespace

import pytest

from app.checks.citations.recency import (
    FINDING_KIND,
    assess_recency,
    choose_reference_year,
    effective_settings,
    load_grammar,
    read_publication_year,
    resolve_policy,
    title_page_year,
)
from app.config import Settings
from app.findings.policy import exclusion_reason
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.enums import CitationParseStatus, FlagSeverity
from app.report.citation_recency import recency_sentences, recency_summary

PARSED = CitationParseStatus.parsed
FAILED = CitationParseStatus.parse_failed
CHECK_YEAR = 2026


@pytest.fixture(scope="module")
def grammar():
    return load_grammar()


def _settings(**overrides) -> Settings:
    # The note is off by default; these tests exercise it switched on.
    return Settings(_env_file=None, **{"citation_recency_enabled": True, **overrides})


def test_the_note_is_off_unless_turned_on(monkeypatch):
    monkeypatch.delenv("CITATION_RECENCY_ENABLED", raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.citation_recency_enabled is False
    assert resolve_policy(None, defaults).enabled is False
    assert resolve_policy({"enabled": True}, defaults).enabled is True


def _read(grammar, raw, year=None, status=FAILED):
    return read_publication_year(raw, year, status, grammar, CHECK_YEAR)


def _citation(index, raw, year=None, status=FAILED, authors=None):
    return SimpleNamespace(
        order_index=index, raw_text=raw, year=year, parse_status=status, authors=authors or []
    )


def _extraction(*front_lines) -> ExtractionResult:
    blocks = [TextBlock(page=1, text=line, max_font_size=12, bold_ratio=0) for line in front_lines]
    blocks.append(
        TextBlock(page=2, text="Chapter 1 Introduction 2010", max_font_size=12, bold_ratio=0)
    )
    return ExtractionResult(
        anchor_kind="page",
        page_count=2,
        image_only=False,
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
        text_chars=sum(len(b.text) for b in blocks),
    )


# ---------------------------------------------------------------------------
# Reading the publication year


@pytest.mark.parametrize(
    ("raw", "year", "status", "expected"),
    [
        (
            "Cruz, M. A. (2019). Mobile apps in schools. Journal of IT, 4(2), 1-9.",
            2019,
            PARSED,
            2019,
        ),
        ("Cruz, M. A. (2019a). Mobile apps. Journal of IT.", 2019, PARSED, 2019),
        ("Cruz, M. A. (2019, May 3). Mobile apps. Manila Bulletin.", 2019, PARSED, 2019),
        ("[4] A. Reyes, “A study,” J. Comp., vol. 3, 2017, pp. 1-10.", 2017, PARSED, 2017),
        ("[1] M. Rampioni. “Analysis of human behavior.” In: 2017. url: x", None, FAILED, 2017),
        ("Smith, J. 2018. Thermal comfort. Building Journal 12: 3-9.", None, FAILED, 2018),
    ],
)
def test_reads_the_year_in_a_date_position(grammar, raw, year, status, expected):
    reading = _read(grammar, raw, year, status)
    assert reading.status == "dated"
    assert reading.year == expected


def test_a_year_inside_a_title_is_not_the_publication_year(grammar):
    apa = "Santos, R. (2024). Lessons from the 2008 financial crisis. Econ Review, 3, 1-5."
    assert _read(grammar, apa, 2024, PARSED).year == 2024
    ieee = "[3] A. Lim, “The 2010 census revisited,” Phil. Stat. J., 2023."
    assert _read(grammar, ieee, 2023, PARSED).year == 2023
    # An unquoted title year next to a missing publication year: no date
    # position holds a year, so nothing is read.
    bare = "Department of Health. Health statistics 2009 handbook. Manila."
    assert _read(grammar, bare).status == "not_readable"


def test_a_newer_year_elsewhere_in_the_entry_makes_it_unreadable(grammar):
    # The parser's year (2015) is contradicted by a newer year that is not in
    # a date position, so the entry is not judged at all.
    raw = "Tan, B. (2015). Planning for 2020 and beyond. Manila Press."
    reading = _read(grammar, raw, 2015, PARSED)
    assert reading.status == "not_readable"
    assert reading.reason == "conflicting_years"


def test_a_future_year_in_a_title_does_not_block_the_reading(grammar):
    raw = "Tan, B. (2015). Vision 2040 for Philippine schools. Manila Press."
    assert _read(grammar, raw, 2015, PARSED).year == 2015


def test_two_dates_in_date_positions_are_conflicting(grammar):
    raw = "Black, S. (2017). Title. Journal, 2018, 12(3), 1-4."
    assert _read(grammar, raw, 2017, PARSED).reason == "conflicting_years"


def test_merged_entries_with_two_years_are_not_read(grammar):
    raw = "Lim, K. (2016). Itineraries. Retrieved from x. BusinessWorld. (2021). AirAsia survey."
    assert _read(grammar, raw, 2016, PARSED).reason == "conflicting_years"


@pytest.mark.parametrize(
    "raw",
    [
        "Reyes, J. (n.d.). Barangay profile. Retrieved from https://example.ph",
        "Reyes, J. (n. d.). Barangay profile.",
        "Reyes, J. (in press). Barangay profile. Journal of Local Studies.",
        "Reyes, J. (forthcoming). Barangay profile. Journal of Local Studies.",
        "Reyes, J. Barangay profile. Journal of Local Studies, in press.",
    ],
)
def test_undated_and_in_press_entries_are_never_dated(grammar, raw):
    reading = _read(grammar, raw)
    assert reading.status == "not_readable"
    assert reading.reason == "no_date"


def test_author_initials_n_d_are_not_an_undated_marker(grammar):
    raw = "[84] N. D. Lane and F. Kawsar. “Squeezing deep learning.” IEEE PC, 16(), 2017."
    assert _read(grammar, raw).year == 2017


def test_parsed_entry_without_a_year_counts_as_no_date(grammar):
    reading = _read(grammar, "Reyes, J. (s.f.). Profile.", None, PARSED)
    assert (reading.status, reading.reason) == ("not_readable", "no_date")


@pytest.mark.parametrize(
    ("raw", "expected", "printed"),
    [
        ("Cruz, A. (2019-2020). Annual report. DepEd.", 2020, "2019-2020"),
        ("Cruz, A. (2019–2020). Annual report. DepEd.", 2020, "2019–2020"),
        ("Cruz, A. (2019—20). Annual report. DepEd.", 2020, "2019—20"),
        ("Cruz, A. (2019/2020). Annual report. DepEd.", 2020, "2019/2020"),
    ],
)
def test_a_year_range_is_read_by_its_latest_year(grammar, raw, expected, printed):
    reading = _read(grammar, raw)
    assert (reading.status, reading.year, reading.year_text) == ("dated", expected, printed)


def test_a_reversed_range_is_not_read(grammar):
    assert _read(grammar, "Cruz, A. (2020-2019). Report.").status == "not_readable"


def test_page_ranges_and_identifiers_are_not_years(grammar):
    pages = "[132] N. Miller. “Functional mismatch.” Science, 349(), 2015, pp. 1541–1544."
    assert _read(grammar, pages).year == 2015
    standard = "[7] ASHRAE. Standard 55-2020: Thermal conditions. ASHRAE, 2021. url: https://a.org/"
    assert _read(grammar, standard).year == 2021
    arxiv = "[3] P. Chwalek. “Urban comfort.” ArXiv, abs/2012.08323(), 2024."
    assert _read(grammar, arxiv).year == 2024
    doi = "Ng, T. (2018). Title. Journal, 5, 1-2. https://doi.org/10.1016/j.2012.01.003"
    assert _read(grammar, doi, 2018, PARSED).year == 2018


def test_access_dates_are_not_publication_dates(grammar):
    retrieved = "Black, S. (2017). Vacation planning. Retrieved July 23, 2023 from https://x.com/a"
    assert _read(grammar, retrieved, 2017, PARSED).year == 2017
    # "Accessed: <date>. <year>." keeps the publication year after the note.
    accessed = "[116] Google Earth. Google Earth. Accessed: 2023-07-04. 2023. url: https://g.com/"
    assert _read(grammar, accessed).year == 2023
    linked = "[95] P. Chwalek. A dataset. figshare https://doi.org/10.6084/m9.fig.27625509. 2024."
    assert _read(grammar, linked).year == 2024


def test_entries_with_no_year_are_counted_as_no_year(grammar):
    raw = "[91] “Role of IoT in biodiversity monitoring.” In: url: https://x.org/1"
    reading = _read(grammar, raw)
    assert (reading.status, reading.reason) == ("not_readable", "no_year")
    assert _read(grammar, "   ").reason == "no_text"


def test_the_parser_year_must_agree_with_the_date_found(grammar):
    # The parser reported 2011, but the only date in the entry is 2019.
    raw = "Cruz, A. (2019). Report. DepEd."
    assert _read(grammar, raw, 2011, PARSED).reason == "conflicting_years"


# ---------------------------------------------------------------------------
# The window boundary


def _assess(citations, *, window=5, basis="check_date", extraction=None, **settings):
    policy = resolve_policy(
        {"window_years": window, "reference_year_basis": basis}, _settings(**settings)
    )
    return assess_recency(citations, extraction, policy, load_grammar(), CHECK_YEAR)


def test_boundary_years_against_a_five_year_window():
    cites = [
        _citation(0, "A, B. (2021). Five years old. J.", 2021, PARSED),
        _citation(1, "A, B. (2020). Six years old. J.", 2020, PARSED),
        _citation(2, "A, B. (2026). Same year. J.", 2026, PARSED),
        _citation(3, "A, B. (2027). Future year. J.", 2027, PARSED),
        _citation(4, "A, B. (n.d.). Undated. J.", None, PARSED),
        _citation(5, "A, B. (in press). Forthcoming. J.", None, FAILED),
        _citation(6, "A, B. (2019-2020). Range. J.", None, FAILED),
        _citation(7, "Unparseable fragment of a reference", None, FAILED),
    ]
    assessment = _assess(cites)
    statuses = {s["reference_order_index"]: s["status"] for s in assessment.summary["sources"]}
    assert statuses == {
        0: "within_window",
        1: "outside_window",
        2: "within_window",
        3: "within_window",
        4: "year_not_readable",
        5: "year_not_readable",
        # A range is judged by its latest year: 2020 is six years before 2026.
        6: "outside_window",
        7: "year_not_readable",
    }
    summary = assessment.summary
    assert summary["reference_year"] == 2026
    assert summary["oldest_year_in_window"] == 2021
    assert (summary["n_dated"], summary["n_outside_window"], summary["n_within_window"]) == (
        5,
        2,
        3,
    )
    assert summary["n_year_not_readable"] == 3
    assert summary["year_not_readable_reasons"] == {"no_date": 2, "no_year": 1}
    assert summary["n_dated_after_reference_year"] == 1
    assert [f.detail["order_index"] for f in assessment.flags] == [1, 6]


def test_a_range_ending_inside_the_window_is_not_flagged_but_one_ending_outside_is():
    cites = [
        _citation(0, "A, B. (2020-2021). Range. J.", None, FAILED),
        _citation(1, "A, B. (2019-2020). Range. J.", None, FAILED),
    ]
    # Window 5 from 2026 starts at 2021: the first range ends inside it.
    flags = _assess(cites).flags
    assert [f.detail["order_index"] for f in flags] == [1]
    assert flags[0].detail["year_text"] == "2019-2020"
    assert flags[0].detail["year"] == 2020


def test_changing_the_window_changes_results_without_code_changes():
    cites = [_citation(0, "A, B. (2023). Three years old. J.", 2023, PARSED)]
    assert _assess(cites, window=5).flags == []
    assert len(_assess(cites, window=2).flags) == 1
    # The configured default applies when the format sets nothing.
    policy = resolve_policy(None, _settings(citation_recency_window_years=2))
    assert policy.window_years == 2
    assert policy.sources["window_years"] == "default"


def test_flag_shape_and_honest_wording():
    flag = _assess([_citation(0, "A, B. (2018). Old. J.", 2018, PARSED, ["A, B."])]).flags[0]
    assert flag.severity == FlagSeverity.low
    assert flag.detail["kind"] == FINDING_KIND
    assert flag.detail["affects_readiness"] is False
    assert exclusion_reason("citation_integrity", flag.detail) == "recency_note_not_counted"
    reason = flag.detail["reason"]
    assert reason.startswith("Published 2018, which is older than the 5-year recency window")
    assert "this format sets" in reason
    assert "dismiss this note with a reason" in reason
    assert "%" not in reason
    for accusing in ("outdated", "invalid", "fail", "unacceptable"):
        assert accusing not in reason.lower()


def test_default_window_wording_does_not_claim_the_format_set_it():
    policy = resolve_policy({"reference_year_basis": "check_date"}, _settings())
    flag = assess_recency(
        [_citation(0, "A, B. (2010). Old. J.", 2010, PARSED)], None, policy, load_grammar(), 2026
    ).flags[0]
    assert "the default 5-year recency window" in flag.detail["reason"]


def test_counting_toward_readiness_is_a_format_choice():
    policy = resolve_policy(
        {"counts_toward_readiness": True, "reference_year_basis": "check_date"}, _settings()
    )
    flag = assess_recency(
        [_citation(0, "A, B. (2010). Old. J.", 2010, PARSED)], None, policy, load_grammar(), 2026
    ).flags[0]
    assert "affects_readiness" not in flag.detail
    assert exclusion_reason("citation_integrity", flag.detail) is None


def test_turning_the_note_off_reads_nothing():
    policy = resolve_policy({"enabled": False}, _settings())
    assessment = assess_recency(
        [_citation(0, "A, B. (2010). Old. J.", 2010, PARSED)], None, policy, load_grammar(), 2026
    )
    assert assessment.flags == []
    assert assessment.summary["status"] == "off"


def test_an_invalid_saved_setting_is_refused_not_replaced_by_defaults():
    for stored in ({"window_years": 0}, {"window_years": "5"}, {"unknown": 1}, ["x"]):
        with pytest.raises(ValueError):
            resolve_policy(stored, _settings())


def test_effective_settings_name_where_each_value_came_from():
    shown = effective_settings({"window_years": 3}, _settings())
    assert shown["window_years"] == 3
    assert shown["setting_sources"]["window_years"] == "format"
    assert shown["setting_sources"]["reference_year_basis"] == "default"


# ---------------------------------------------------------------------------
# The manuscript's own year


def test_title_page_year_is_read_from_a_date_only_line(grammar):
    extraction = _extraction("A Mobile App", "In Partial Fulfillment", "December 2023")
    year, text, anchor, issue = title_page_year(extraction, grammar)
    assert (year, text, anchor, issue) == (2023, "December 2023", "p. 1", None)
    assert title_page_year(_extraction("Title", "May, 2024"), grammar)[0] == 2024
    assert title_page_year(_extraction("Title", "August 22, 2025"), grammar)[0] == 2025
    assert title_page_year(_extraction("Title", "2022"), grammar)[0] == 2022


def test_title_page_year_needs_exactly_one_year(grammar):
    assert title_page_year(_extraction("Title", "S.Y. 2024-2025"), grammar)[3] == (
        "manuscript_year_not_found"
    )
    assert title_page_year(_extraction("Title", "May 2023", "June 2024"), grammar)[3] == (
        "manuscript_year_ambiguous"
    )
    # A year in running text on page 2 is never the manuscript's year.
    assert title_page_year(_extraction("Title"), grammar)[3] == "manuscript_year_not_found"
    assert title_page_year(None, grammar)[3] == "manuscript_year_not_found"


def test_manuscript_year_is_the_reference_year_when_clear(grammar):
    policy = resolve_policy(None, _settings())
    chosen = choose_reference_year(policy, _extraction("T", "October 2019"), grammar, 2026, 2019)
    assert (chosen.year, chosen.basis, chosen.fallback_reason) == (2019, "manuscript_year", None)
    assert chosen.evidence_text == "October 2019"


@pytest.mark.parametrize(
    ("lines", "newest", "reason"),
    [
        (("T",), 2020, "manuscript_year_not_found"),
        (("T", "May 2030"), 2020, "manuscript_year_after_check_date"),
        (("T", "May 2019"), 2022, "manuscript_year_precedes_cited_source"),
    ],
)
def test_check_date_is_used_when_the_manuscript_year_is_not_clear(grammar, lines, newest, reason):
    policy = resolve_policy(None, _settings())
    chosen = choose_reference_year(policy, _extraction(*lines), grammar, 2026, newest)
    assert (chosen.year, chosen.basis, chosen.fallback_reason) == (2026, "check_date", reason)


def test_check_date_basis_ignores_the_title_page(grammar):
    policy = resolve_policy({"reference_year_basis": "check_date"}, _settings())
    chosen = choose_reference_year(policy, _extraction("T", "May 2019"), grammar, 2026, 2019)
    assert (chosen.year, chosen.basis) == (2026, "check_date")


def test_manuscript_year_shifts_the_window():
    cites = [
        _citation(0, "A, B. (2016). Seven years before the manuscript. J.", 2016, PARSED),
        _citation(1, "A, B. (2018). Five years before the manuscript. J.", 2018, PARSED),
        _citation(2, "A, B. (2023). Same year. J.", 2023, PARSED),
    ]
    assessment = _assess(
        cites, basis="manuscript_year", extraction=_extraction("T", "December 2023")
    )
    assert assessment.summary["reference_year"] == 2023
    assert assessment.summary["reference_year_basis"] == "manuscript_year"
    assert assessment.summary["reference_year_evidence"] == {
        "text": "December 2023",
        "anchor": "p. 1",
    }
    assert [f.detail["order_index"] for f in assessment.flags] == [0]
    assert "the year on the manuscript's title page" in assessment.flags[0].detail["reason"]


# ---------------------------------------------------------------------------
# Report projection


def test_report_summary_uses_counts_and_lists_unreadable_references():
    cites = [
        _citation(0, "A, B. (2010). Old. J.", 2010, PARSED),
        _citation(1, "A, B. (2025). New. J.", 2025, PARSED),
        _citation(2, "A, B. (n.d.). Undated. J.", None, PARSED),
    ]
    summary = recency_summary({"citation_recency": _assess(cites).summary})
    assert summary.status == "assessed"
    assert (summary.n_dated, summary.n_outside_window, summary.n_year_not_readable) == (2, 1, 1)
    assert summary.year_not_readable_reference_numbers == [3]
    assert summary.window_source == "format"
    sentences = recency_sentences(summary)
    assert sentences[0].startswith("Source recency: 1 of 2 sources with a readable year is older")
    assert "1 source has no readable year and was not judged" in sentences[1]
    assert sentences[-1] == "These notes do not count toward readiness."
    assert not any("%" in sentence for sentence in sentences)


def test_report_summary_is_absent_for_older_runs_and_malformed_records():
    assert recency_summary(None) is None
    assert recency_summary({}) is None
    assert recency_summary({"citation_recency": {"status": "weird"}}) is None
    assert (
        recency_summary({"citation_recency": {"status": "assessed", "n_dated": "x"}}).n_dated == 0
    )
    unassessed = recency_summary(
        {"citation_recency": {"status": "unassessed", "unassessed_reason": "no_references"}}
    )
    assert unassessed.unassessed_reason == "no_references"
    assert recency_sentences(unassessed)[0].startswith("Source recency: not assessed")


# ---------------------------------------------------------------------------
# Entries with no real date must not pick up a page, title or edition year


@pytest.mark.parametrize(
    "raw",
    [
        # A page range after a volume and issue, with no publication year.
        "Hochreiter, S. Long short-term memory. Neural Computation, 9(8), 1735–1780.",
        "Hochreiter, S. Long short-term memory. Neural Computation, 9(8), 1735-1780.",
        # A single page or article number after a volume and issue.
        "Hochreiter, S. Long short-term memory. Neural Computation, 9(8), 1735.",
    ],
)
def test_pages_after_volume_and_issue_are_never_a_year(grammar, raw):
    assert _read(grammar, raw).status == "not_readable"


@pytest.mark.parametrize(
    "raw",
    [
        "Reyes, J. (n.d). Barangay health report, 2008. Iloilo City.",
        "Reyes, J. (nd). Barangay health report, 2008. Iloilo City.",
        "Reyes, J. n.d. Barangay health report, 2008. Iloilo City.",
        "Reyes, J. (In-press). Barangay health report, 2008. Iloilo City.",
        "Reyes, J. (s.f.). Informe de salud, 2008. Iloilo City.",
        "Reyes, J. (N.D.). Barangay health report, 2008. Iloilo City.",
    ],
)
def test_loose_undated_markers_keep_a_title_year_from_being_read(grammar, raw):
    reading = _read(grammar, raw)
    assert (reading.status, reading.reason) == ("not_readable", "no_date")


@pytest.mark.parametrize(
    "raw",
    [
        "Gay, L. R. (2016, 15th ed. 2022). Educational research. Pearson.",
        "Cruz, A. (2012, updated 2024). Barangay profile. Iloilo City.",
        "Cruz, A. (2012). Barangay profile. Iloilo City, c2021.",
        "Cruz, A. (2012). Barangay profile. Iloilo City, © 2021.",
    ],
)
def test_a_newer_edition_or_copyright_year_makes_the_entry_unreadable(grammar, raw):
    reading = _read(grammar, raw, 2016 if raw.startswith("Gay") else 2012, PARSED)
    assert (reading.status, reading.reason) == ("not_readable", "conflicting_years")


def test_initials_in_capitals_are_still_names(grammar):
    raw = "Bibby, C. J., N. D. Burgess, and D. A. Hill. (1992). Bird census techniques. Academic."
    assert _read(grammar, raw, 1992, PARSED).year == 1992


def test_export_names_the_real_cause_of_a_missing_assessment():
    by_default = recency_summary(
        {"citation_recency": {"status": "off", "setting_sources": {"enabled": "default"}}}
    )
    assert recency_sentences(by_default) == []
    by_format = recency_summary(
        {"citation_recency": {"status": "off", "setting_sources": {"enabled": "format"}}}
    )
    assert recency_sentences(by_format) == [
        "Source recency: not checked; this format turns the recency note off."
    ]
    invalid = recency_summary(
        {
            "citation_recency": {
                "status": "unassessed",
                "unassessed_reason": "format_setting_invalid",
            }
        }
    )
    assert recency_sentences(invalid) == [
        "Source recency: not assessed, because the format's saved recency setting is not valid."
    ]


@pytest.mark.parametrize(
    "raw",
    [
        "PSA. (n. d). Census, 2015.",
        "Smith, J. Title. Publisher, 1998. Ed.2022.",
    ],
)
def test_spacing_variants_and_abbreviated_editions_are_not_read(grammar, raw):
    assert _read(grammar, raw).status == "not_readable"


def test_counted_recency_notes_make_one_low_deduction_however_many_sources():
    from app.models.enums import ReadinessStatus, ResultOutcome
    from app.report.scoring import ScorableFlag, ScorableResult, score_check_run

    settings = _settings()
    results = [ScorableResult(1, 1.0, ResultOutcome.passed, 95.0)]
    notes = [
        ScorableFlag(
            severity=FlagSeverity.low,
            overridden=False,
            id=index,
            check_kind="citation_integrity",
            problem_kind=FINDING_KIND,
            evidence_excerpt=f"Author {index}. (2010). Old source {index}. J.",
        )
        for index in range(14)
    ]
    scored = score_check_run(results, notes, settings)
    assert scored.flag_deduction == settings.scoring_flag_deduction_low
    assert scored.composite_score == 95.0 - settings.scoring_flag_deduction_low
    assert scored.status == ReadinessStatus.ready
    # A single note deducts the same as fourteen.
    assert score_check_run(results, notes[:1], settings).flag_deduction == scored.flag_deduction
