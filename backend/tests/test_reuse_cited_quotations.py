"""BUG-632: a quotation marked and cited in the text is left out of the
shared-wording test, so quoting a source honestly is not reported as reuse.

The citation forms below are the ones real theses use (Harvard and APA
parentheticals, a narrative author and year with a page, IEEE numbers), and
the false quotation marks are the kinds found in real theses: button labels,
titles and code."""

from app.checks.reuse.lexical import wording_evidence
from app.checks.reuse.quotations import (
    cited_quotation_spans,
    cited_quotations_in_passage,
    load_quotation_rules,
)
from app.config import Settings

RULES = load_quotation_rules(Settings().reuse_cited_quotation_rules_file)

QUOTED = (
    "all electronically mediated information exchanges between an organisation and its "
    "external stakeholders"
)


def _quoted_texts(text: str) -> list[str]:
    return [text[start:end] for start, end in cited_quotation_spans(text, RULES)]


def test_a_harvard_citation_after_curly_quotes_marks_the_quotation():
    text = f"E-business can be defined as “{QUOTED}” (Chaffey 2004, p. 9). It grew."
    assert _quoted_texts(text) == [f"“{QUOTED}”"]


def test_an_apa_parenthetical_with_a_group_author_marks_the_quotation():
    text = (
        'Usability is "the degree to which a product can be used by specified users to '
        'achieve specified goals" (International Organization for Standardization [ISO], '
        "2018, p. 3)."
    )
    assert len(_quoted_texts(text)) == 1


def test_a_narrative_citation_before_and_a_page_after_mark_the_quotation():
    text = (
        'Nielsen (1993) observed that "users learn a simple system quickly and remember it '
        'after a long break" in most of the studies he reviewed.'
    )
    assert len(_quoted_texts(text)) == 1
    paged = (
        'As Cruz and Reyes argued, "the queue moved faster once patients booked online" (p. 41).'
    )
    assert len(_quoted_texts(paged)) == 1


def test_ieee_numbers_before_or_after_mark_the_quotation():
    after = 'The study found that "students reported fewer scheduling conflicts each term" [7].'
    before = 'As noted in [12], "the model overfits when the dataset has fewer than ten classes".'
    assert len(_quoted_texts(after)) == 1
    assert len(_quoted_texts(before)) == 1


def test_quotation_marks_without_a_citation_are_not_a_quotation():
    texts = [
        'After the customer presses the "Reserve" button, the "Reservation information" '
        "screen shows the selected frame (Santos, 2019).",
        "In the journal “Food finder: a mobile food ordering application” the authors "
        "describe a menu display.",
        '{ "code": "ADHAAR_NUMBER", "osx": 1111, "osy": 802 } Following the same pattern',
        'The card moves from the "To do" field to the "In Progress" field when work starts.',
    ]
    assert [_quoted_texts(t) for t in texts] == [[], [], [], []]


def test_a_citation_far_from_the_quotation_does_not_count():
    filler = " ".join(["the system was tested again with more users"] * 4)
    text = f'Santos (2019) studied queues. {filler} and "patients booked their slot online".'
    assert _quoted_texts(text) == []


def test_a_quotation_longer_than_the_limit_is_not_marked():
    long = " ".join(["word"] * (RULES.max_words + 1))
    assert _quoted_texts(f'He wrote "{long}" (Santos, 2019).') == []


def test_a_quotation_that_starts_before_the_passage_is_found_through_its_context():
    context = f"It can be defined as “{QUOTED}” (Chaffey 2004, p. 9). Our study differs."
    passage = context[context.index("information") :]
    spans = cited_quotations_in_passage(passage, context, RULES)
    assert spans and passage[spans[0][0] : spans[0][1]].startswith("information")


SETTINGS = Settings()
SOURCE = (
    "The clinic queue prototype sent each patient a text message when their turn was near, "
    "and the nurses reported that the waiting room was calmer during the trial weeks than "
    "in the month before it started at the barangay health centre."
)


def _left_out(text: str):
    return cited_quotation_spans(text, RULES)


def test_wording_shared_only_inside_a_cited_quotation_does_not_confirm():
    own = (
        f'Garcia (2023) reported that "{SOURCE}" (p. 14). Our study instead asks how '
        "students choose an elective when two sections clash in the timetable."
    )
    assert wording_evidence(own, SOURCE, SETTINGS)["confirmed"]
    assert not wording_evidence(own, SOURCE, SETTINGS, a_left_out=_left_out(own))["confirmed"]


def test_copied_prose_beside_a_cited_quotation_still_confirms():
    copied = (
        "Students who registered online spent less time waiting at the registrar, and the "
        "staff said the queue was shorter during enrolment week than in the previous term."
    )
    source = SOURCE + " " + copied
    own = f'Garcia (2023) reported that "{SOURCE}" (p. 14). {copied}'
    assert wording_evidence(own, source, SETTINGS, a_left_out=_left_out(own))["confirmed"]


def test_a_left_out_span_breaks_a_run_instead_of_joining_its_neighbours():
    own = "alpha beta gamma delta [left out] epsilon zeta eta theta"
    start = own.index("[")
    span = [(start, own.index("]") + 1)]
    other = "alpha beta gamma delta epsilon zeta eta theta"
    evidence = wording_evidence(own, other, SETTINGS, a_left_out=span)
    assert evidence["longest_run"] == 4
