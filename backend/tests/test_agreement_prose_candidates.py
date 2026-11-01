"""F4: ordinary prose is a candidate, while unknown recall never proves absence."""

from dataclasses import replace

import pytest

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import extract_statements
from app.config import Settings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock


def source(texts):
    return ExtractionResult(
        page_count=2,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(map(len, texts)),
        blocks=[
            TextBlock(page=1 if i < 2 else 2, text=t, max_font_size=12, bold_ratio=0)
            for i, t in enumerate(texts)
        ],
        section_tree=SectionTree(source="none", nodes=[]),
        images=[],
    )


@pytest.mark.parametrize(
    "noun,verb,subject",
    [("objective", "was", "Irrigation"), ("goal", "is", "Inventory"), ("aim", "was", "Astronomy")],
)
def test_nominal_intent_and_unnumbered_results_independent_of_domain(noun, verb, subject):
    intent = f"Its {noun} {verb} to reduce processing time while preserving output quality."
    result = f"The {subject.lower()} controller reduced measured processing time in the trials."
    d = source(
        [
            "Overview",
            intent,
            "Results",
            result,
            "References",
            "Example, A. (2020). Synthetic control.",
        ]
    )
    out = extract_statements(d, settings=Settings(_env_file=None))
    assert any(s.text == intent and s.page == 1 for s in out.intents)
    assert any(s.text == result and s.page == 2 for s in out.outcomes)
    assert not any("Synthetic control" in s.text for s in out.outcomes)


def test_unnumbered_results_keep_future_scope_and_source_guards():
    d = source(
        [
            "Results",
            "Future researchers could later implement biometric login.",
            "The new sensor is outside the scope of this study.",
        ]
    )
    out = extract_statements(d, settings=Settings(_env_file=None))
    assert out.outcomes == []


def test_outcome_heading_and_constructions_are_configurable_data():
    cues = replace(
        load_cues(),
        outcome_heading_synonyms=frozenset({"observations"}),
        intent_construction_patterns=(),
        outcome_prose_enabled=True,
    )
    out = extract_statements(
        source(["Observations", "The detector measured a stable current."]),
        cues=cues,
        settings=Settings(_env_file=None),
    )
    assert len(out.outcomes) == 1


def test_bibliography_fragments_and_prior_results_are_not_current_outcome_prose():
    d = source(
        [
            "Results",
            "Smith, A. (2020). Improved Sensor Results. Research Press.",
            "Prior Results",
            "Earlier sensors reduced processing time.",
        ]
    )
    assert extract_statements(d, settings=Settings(_env_file=None)).outcomes == []


def test_a_result_sentence_wrapped_over_line_blocks_is_read_whole():
    # A PDF can give every printed line as its own block; the sentence must
    # not be cut at the line ends.
    lines = [
        "Results and Discussion",
        "The staff portal achieved a weighted mean of 4.70. The staff member strongly",
        "agreed that the portal allowed patient records to be searched and managed,",
        "supported the review and processing of customer reservations, and made",
        "frame information easy to manage.",
    ]
    out = extract_statements(source(lines), settings=Settings(_env_file=None))
    texts = [s.text for s in out.outcomes]
    assert "The staff portal achieved a weighted mean of 4.70." in texts
    assert (
        "The staff member strongly agreed that the portal allowed patient records to be "
        "searched and managed, supported the review and processing of customer "
        "reservations, and made frame information easy to manage."
    ) in texts
    assert not any(t.startswith("supported the review") for t in texts)


def test_a_result_fragment_that_never_finishes_is_not_offered():
    # The run is cut off by the next heading before the sentence ends.
    lines = ["Results", "The kiosk reduced the average queue in the second week by", "Discussion"]
    out = extract_statements(source(lines), settings=Settings(_env_file=None))
    assert out.outcomes == []


def lines_on_pages(rows):
    """Blocks with real line geometry: (page, text, width)."""
    blocks = [
        TextBlock(
            page=page,
            text=text,
            max_font_size=12,
            bold_ratio=0,
            bbox=(72.0, 100.0, 72.0 + width, 112.0),
        )
        for page, text, width in rows
    ]
    return ExtractionResult(
        page_count=3,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(t) for _p, t, _w in rows),
        blocks=blocks,
        section_tree=SectionTree(source="none", nodes=[]),
        images=[],
    )


def _outcomes(extraction):
    return [
        (s.anchor, s.text)
        for s in extract_statements(extraction, settings=Settings(_env_file=None)).outcomes
    ]


def test_a_sentence_running_onto_the_next_page_is_anchored_where_it_starts():
    rows = [
        (1, "Results", 60),
        (1, "The kiosk reduced the average queue time for walk-in patients across", 460),
        (2, "all three clinics during the second week of testing.", 300),
    ]
    assert _outcomes(lines_on_pages(rows)) == [
        (
            "p. 1",
            "The kiosk reduced the average queue time for walk-in patients across all three "
            "clinics during the second week of testing.",
        )
    ]


def test_a_run_that_starts_mid_sentence_is_not_offered():
    rows = [(1, "Results", 60), (1, "reduced the average queue time in every clinic.", 300)]
    assert _outcomes(lines_on_pages(rows)) == []


def test_a_heading_or_list_item_ends_the_sentence_before_it():
    # Joined across the break, each would read as a whole result sentence that
    # the manuscript never wrote.
    after_heading = [
        (1, "Results", 60),
        (1, "The kiosk reduced the average queue time by", 460),
        (1, "Conclusions", 80),
        (1, "patients waiting in every clinic.", 300),
    ]
    assert _outcomes(lines_on_pages(after_heading)) == []
    around_list = [
        (1, "Results", 60),
        (1, "The portal was used by", 460),
        (1, "1. The kiosk improved booking accuracy.", 300),
        (1, "staff who also improved booking accuracy.", 300),
    ]
    assert all(
        not text.startswith("The portal was used by")
        for _a, text in _outcomes(lines_on_pages(around_list))
    )


def test_lead_ins_captions_table_numbers_and_subheadings_are_not_glued_onto_a_result():
    result = "The kiosk achieved a weighted mean of 4.52 from the respondents."
    for before in (
        (1, "The results of the evaluation are shown below:", 400),
        (1, "Table 4.3 weighted mean of the respondents' evaluation", 460),
        (1, "4.56 4.44", 60),
        (1, "Evaluation of the kiosk by the clinic staff and the patients", 300),
    ):
        rows = [(1, "Results", 60), before, (1, result, 460)]
        assert ("p. 1", result) in _outcomes(lines_on_pages(rows)), before[1]


def test_a_stray_page_number_does_not_enter_a_result():
    rows = [
        (1, "Results", 60),
        (1, "The kiosk achieved a weighted mean of", 460),
        (1, "45", 20),
        (2, "4.52 from the respondents.", 200),
    ]
    assert all("45 4.52" not in text for _a, text in _outcomes(lines_on_pages(rows)))


def test_a_note_closing_the_previous_sentence_is_not_part_of_the_next():
    rows = [
        (1, "Results", 60),
        (1, "The database was read with a crafted command. (Appendix 5) The path", 460),
        (1, "traversal attack yielded the confirmation that the file was accessible.", 460),
    ]
    texts = [text for _a, text in _outcomes(lines_on_pages(rows))]
    assert (
        "The path traversal attack yielded the confirmation that the file was accessible." in texts
    )


def test_a_sentence_that_opens_with_a_table_number_is_a_result_not_a_caption():
    sentence = "Table 4.2 shows that the kiosk reduced the average queue time by 30%."
    rows = [(1, "Results", 60), (1, sentence, 460)]
    assert ("p. 1", sentence) in _outcomes(lines_on_pages(rows))
    # Wrapped, its first line has no closing punctuation, as a caption has not.
    wrapped = [
        (1, "Results", 60),
        (1, "Table 4.2 shows that the kiosk reduced the average queue time", 460),
        (1, "by 30% in every clinic.", 300),
    ]
    assert _outcomes(lines_on_pages(wrapped)) == [
        (
            "p. 1",
            "Table 4.2 shows that the kiosk reduced the average queue time by 30% in every clinic.",
        )
    ]


def test_a_last_line_that_is_only_a_number_ends_the_sentence():
    rows = [
        (1, "Results", 60),
        (1, "The kiosk reduced the average waiting time to", 460),
        (1, "4.52.", 30),
    ]
    assert _outcomes(lines_on_pages(rows)) == [
        ("p. 1", "The kiosk reduced the average waiting time to 4.52.")
    ]


def test_a_page_number_between_pages_does_not_split_the_sentence():
    rows = [
        (1, "Results", 60),
        (1, "The kiosk achieved a weighted mean of", 460),
        (1, "45", 20),
        (2, "4.52 from the respondents in every clinic.", 300),
    ]
    assert _outcomes(lines_on_pages(rows)) == [
        ("p. 1", "The kiosk achieved a weighted mean of 4.52 from the respondents in every clinic.")
    ]


def test_a_sentence_after_a_trimmed_note_is_anchored_on_its_own_page():
    rows = [
        (1, "Results", 60),
        (1, "The database was read with a crafted command. (Appendix 5)", 460),
        (
            2,
            "The path traversal attack yielded the confirmation that the file was accessible.",
            460,
        ),
    ]
    assert (
        "p. 2",
        "The path traversal attack yielded the confirmation that the file was accessible.",
    ) in _outcomes(lines_on_pages(rows))


def test_a_reference_entry_or_a_caption_ends_the_sentence_before_it():
    for between in (
        (1, "Cruz, M. (2019). Queues at clinics. Journal of Examples, 4(2), 10-20.", 460),
        (1, "Table 4.3 weighted mean of the respondents' evaluation", 460),
    ):
        rows = [
            (1, "Results", 60),
            (1, "The portal was used by", 460),
            between,
            (1, "staff who also improved booking accuracy.", 300),
        ]
        assert all(
            not text.startswith("The portal was used by")
            for _a, text in _outcomes(lines_on_pages(rows))
        ), between[1]


def test_without_line_geometry_a_capital_line_does_not_continue_a_sentence():
    # No widths to tell a wrapped line from a new one: break rather than guess.
    lines = [
        "Results",
        "The kiosk reduced the average queue time for",
        "Walk-in patients improved their waiting time in every clinic.",
    ]
    texts = [
        s.text
        for s in extract_statements(source(lines), settings=Settings(_env_file=None)).outcomes
    ]
    assert not any(
        t.startswith("The kiosk reduced the average queue time for Walk-in") for t in texts
    )
