"""BUG-632: a quotation marked and cited in the text is left out of the
shared-wording test, so quoting a source honestly is not reported as reuse.

The citation forms below are the ones real theses use (Harvard and APA
parentheticals, a narrative author and year with a page, IEEE numbers), and
the false quotation marks are the kinds found in real theses: button labels,
titles and code."""

from app.checks.reuse.lexical import wording_evidence
from app.checks.reuse.quotations import (
    cited_quotation_spans,
    load_quotation_rules,
    pair_wording,
    quotations_in_passage,
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
    text = f"E-business can be defined as \u201c{QUOTED}\u201d (Chaffey 2004, p. 9). It grew."
    assert _quoted_texts(text) == [f"\u201c{QUOTED}\u201d"]


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
        "In the journal \u201cFood finder: a mobile food ordering application\u201d the authors "
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
    context = f"It can be defined as \u201c{QUOTED}\u201d (Chaffey 2004, p. 9). Our study differs."
    passage = context[context.index("information") :]
    quotes = [q for q in quotations_in_passage(passage, context, RULES) if q.cited]
    assert quotes and passage[quotes[0].start : quotes[0].end].startswith("information")


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


COPY_65 = (
    "Students who registered online spent less time waiting at the registrar, and the staff "
    "said the queue was shorter during enrolment week than in the previous term, when every "
    "form was filed by hand at a single window and checked twice by the clerk before it was "
    "stamped, so many students came back the next morning to finish the process."
)


def test_a_stray_quotation_mark_does_not_hide_a_copy_after_it():
    """An unclosed mark, or a curly quotation that lost its closing mark,
    must not pair with a later quotation's closing mark and leave out the
    unquoted copy between them."""
    later = 'As Reyes (2020) put it, "the queue is the first thing a student sees" (p. 3).'
    for stray in ('Click "Enroll to start. ', "The form said \u201cEnroll now. "):
        own = stray + COPY_65 + " " + later
        evidence = pair_wording(own, own, COPY_65, COPY_65, SETTINGS)
        assert evidence["confirmed"], stray
        assert not evidence["cited_quotation_only"]


def test_an_array_index_is_not_a_citation():
    code = 'value = date3[0] if row["the start date of the term"] else date3[1]'
    assert _quoted_texts(code) == []


def test_an_unquoted_copy_of_a_library_quotation_is_still_compared():
    """The Library thesis quoted and cited these words; the student copied
    them without quotation marks. That is still compared."""
    library = f'Garcia (2023) reported that "{SOURCE}" (p. 14).'
    evidence = pair_wording(SOURCE, SOURCE, library, library, SETTINGS)
    assert evidence["confirmed"]


def test_the_same_quotation_in_both_theses_is_not_compared():
    """Both theses quote one source; the student's quotation marks have no
    citation the rules know, the Library thesis's quotation is cited."""
    own = f'One report said that "{SOURCE}" according to the clinic staff.'
    library = f'Garcia (2023) reported that "{SOURCE}" (p. 14).'
    evidence = pair_wording(own, own, library, library, SETTINGS)
    assert not evidence["confirmed"]
    assert evidence["cited_quotation_only"]


def _filler(prefix: str, n: int) -> str:
    """`n` distinct words, so no shingle repeats."""
    letters = "bcdfghjklmnpqrstvwxz"
    return " ".join(prefix + letters[i // 20] + letters[i % 20] for i in range(n))


def test_leaving_a_quotation_out_never_creates_a_match():
    """Comparing less can only remove evidence: a remainder whose share of
    shingles clears the bar alone, inside a pair that does not, stays
    unconfirmed."""
    first = "the attendance sheet was collected at the end of each class"
    second = "the instructor entered every mark into the spreadsheet on friday afternoon"
    quote = _filler("q", 60)
    own = f'Reyes (2019) wrote "{quote}" (p. 3). {first} and later {second}'
    own += " for all of us that term."
    other = f"{_filler('a', 50)} {first} {_filler('b', 50)} {second} {_filler('c', 50)}"
    spans = cited_quotation_spans(own, RULES)
    assert spans
    assert wording_evidence(own, other, SETTINGS, a_left_out=spans)["confirmed"]
    assert not wording_evidence(own, other, SETTINGS)["confirmed"]
    assert not pair_wording(own, own, other, other, SETTINGS)["confirmed"]


def test_a_citation_seen_only_in_the_context_still_counts():
    """The passage ends at the closing quotation mark; its citation is in the
    words after the passage."""
    passage = f'Garcia reported that "{SOURCE}"'
    context = f"{passage} (Garcia, 2023, p. 14). Our study differs."
    assert not pair_wording(passage, context, SOURCE, SOURCE, SETTINGS)["confirmed"]
    assert pair_wording(passage, passage, SOURCE, SOURCE, SETTINGS)["confirmed"]


def test_a_quoted_term_inside_a_cited_quotation_keeps_it_a_quotation():
    text = (
        'Lopez (2021) wrote that \u201cthe team called the prototype "the queue board" and '
        "used it in every clinic visit after the first week\u201d (p. 8)."
    )
    quoted = _quoted_texts(text)
    assert quoted[0].startswith("\u201cthe team") and quoted[0].endswith("first week\u201d")


def test_an_index_after_a_call_is_not_a_citation():
    code = 'first = line.split("the separator text here")[0] and the rest is kept'
    assert _quoted_texts(code) == []
