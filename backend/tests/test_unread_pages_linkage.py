"""Which uncited claims a page left unread withholds: every entry an unread
page could be citing, judged with the rules the readable text is read with."""

from types import SimpleNamespace

from app.checks.citations.unread_pages import split_by_unread_pages, unread_page_leads
from app.config import Settings
from app.ingest.schemas import ExtractionResult, SectionTree

SETTINGS = Settings()


def _ref(index, raw, authors=(), year=2020):
    return SimpleNamespace(order_index=index, raw_text=raw, authors=list(authors), year=year)


def _source(unread_text: dict[int, str]) -> ExtractionResult:
    return ExtractionResult(
        page_count=20,
        anchor_kind="page",
        image_only=False,
        text_chars=0,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[],
        images=[],
    ).model_copy(update={"unread_pages": sorted(unread_text), "unread_text": unread_text})


BLACK = _ref(0, "Black, S. (2017). Planning a family vacation.", ["Black, S."], 2017)
CRUZ_A = _ref(1, "Cruz, M. (2019). Queues at clinics.", ["Cruz, M."], 2019)
CRUZ_B = _ref(2, "Cruz, J. (2021). Booking kiosks.", ["Cruz, J."], 2021)
NAMELESS = _ref(3, "(2018). Annual report.", [], 2018)
SCHMITT = _ref(
    4,
    "Schmitt, L. A., Delbosc, A., & Currie, G. (2018). Learning to use transit.",
    ["Schmitt, L. A.", "Delbosc, A.", "Currie, G."],
    2018,
)
DEPED = _ref(
    5, "Department of Education. (2022). Basic education report.", ["Department of Education"], 2022
)


def _split(candidates, unread_text, *, numbered=False, citing_pages=frozenset({3, 4, 5, 6})):
    return split_by_unread_pages(
        candidates,
        _source(unread_text),
        numbered=numbered,
        citing_pages=set(citing_pages),
        settings=SETTINGS,
    )


def _withheld(result):
    return [(ref.order_index, pages) for ref, pages, _why, _found in result[1]]


def test_an_entry_no_unread_page_names_is_still_reported():
    released, withheld = _split([BLACK], {18: "Table 4. Mean scores 4.1 4.3 3.9"})
    assert released == [BLACK] and withheld == []


def test_an_entry_an_unread_page_names_is_withheld_with_that_page_and_the_name():
    released, withheld = _split([BLACK], {5: "as Black (2017) found", 18: "Table 4."})
    assert released == []
    assert withheld == [(BLACK, [5], "named", "black")]


def test_the_name_is_found_in_the_forms_the_linkage_rules_accept():
    # Every author, any case, accents, a hyphen at a line break, a known alias.
    for text in (
        "as Delbosc et al. (2018) showed",
        "SOURCE: SCHMITT (2018)",
        "Schmi-\ntt (2018)",
    ):
        assert _withheld(_split([SCHMITT], {5: text})) == [(4, [5])], text
    pena = _ref(6, "Peña, R. (2020). Kiosks.", ["Peña, R."])
    assert _withheld(_split([pena], {5: "Pena (2020)"})) == [(6, [5])]
    assert _withheld(_split([DEPED], {18: "Source: DepEd (2022)"})) == [(5, [18])]


def test_a_surname_shared_by_two_entries_withholds_both():
    # The page cannot say which Cruz it cites, so neither is called uncited.
    released, withheld = _split([CRUZ_A, CRUZ_B], {4: "according to Cruz"})
    assert released == []
    assert [ref for ref, _pages, _why, _found in withheld] == [CRUZ_A, CRUZ_B]


def test_a_short_surname_is_withheld_whatever_its_case():
    ho = _ref(7, "Ho, R.C. & Muslim, A. (2019). Smartphone adoption.", ["Ho, R.C.", "Muslim, A."])
    lim = _ref(8, "Lim, K. (2016). Trip itineraries.", ["Lim, K."], 2016)
    for text in ("As Ho (2019) and Lim noted", "HO (2019); LIM (2016)"):
        assert _withheld(_split([ho, lim], {18: text})) == [(7, [18]), (8, [18])], text


def test_an_entry_without_a_usable_name_falls_back_to_where_the_citing_text_is():
    inside = _split([NAMELESS], {4: "some text"})
    assert _withheld(inside) == [(3, [4])] and inside[1][0][2] == "within_citing_text"
    assert _split([NAMELESS], {18: "some text"}) == ([NAMELESS], [])
    # With no citing pages found, any unread page might be citing it.
    assert _withheld(_split([NAMELESS], {18: "some text"}, citing_pages=())) == [(3, [18])]


def _numbered(n):
    return _ref(n - 1, f"[{n}] Lee, K. Sensor networks.", ["Lee, K."])


def test_a_numbered_entry_inside_a_cited_range_is_withheld():
    refs = [_numbered(n) for n in (7, 8, 9, 10, 11)]
    for text in ("as shown in [7-10]", "as shown in [7–10]", "as shown in [7]-[10]"):
        released, withheld = _split(refs, {5: text}, numbered=True)
        assert [r.order_index + 1 for r, *_ in withheld] == [7, 8, 9, 10], text
        assert [r.order_index + 1 for r in released] == [11], text


def test_a_bare_digit_or_another_number_does_not_withhold_a_numbered_entry():
    ref = _numbered(7)
    released, withheld = _split([ref], {5: "values 7 and 70 in the table, see [17]"}, numbered=True)
    assert released == [ref] and withheld == []


def test_an_unreadable_bracket_mark_falls_back_to_the_citing_pages():
    ref = _numbered(7)
    # A list too long to read could hide any number.
    marks = "[" + ", ".join(str(n) for n in range(30, 80)) + "]"
    released, withheld = _split([ref], {5: marks}, numbered=True)
    assert released == [] and withheld[0][2] == "within_citing_text"


def test_the_lead_names_the_pages_and_what_was_found_and_does_not_count():
    [lead] = unread_page_leads(
        [(BLACK, [63, 66], "named", "black")], numbered=False, settings=SETTINGS
    )
    detail = lead.detail
    assert detail["kind"] == "uncited_reference"
    assert detail["affects_readiness"] is False
    assert detail["unread_pages"] == [63, 66]
    assert detail["found_on_unread_pages"] == "black"
    assert "pages 63 and 66" in detail["reason"]
    assert '"black"' in detail["reason"]
    assert "does not change readiness" in detail["reason"]
    assert lead.severity.value == "low"


def test_the_lead_prints_n_d_for_an_entry_that_says_it_has_no_date():
    undated = _ref(9, "Google. (n.d.). Face landmark detection guide.", ["Google"], None)
    [lead] = unread_page_leads(
        [(undated, [18], "named", "google")], numbered=False, settings=SETTINGS
    )
    assert "'Google' (n.d.)" in lead.detail["reason"]


def test_glued_and_page_locator_marks_withhold_numbered_entries():
    # The readable path counts these as possible citations too.
    refs = [_numbered(n) for n in (7, 8, 9, 10, 11)]
    for text in (
        "compared[7-10] across clinics.",
        "compared[7],[8],[9],[10] across clinics.",
        "reported in [7, p. 3], [8, p. 4], [9, p. 5] and [10, p. 6].",
    ):
        released, withheld = _split(refs, {5: text}, numbered=True)
        assert [r.order_index + 1 for r, *_ in withheld] == [7, 8, 9, 10], text
        assert [r.order_index + 1 for r in released] == [11], text


def test_an_unreadable_glued_mark_falls_back_to_the_citing_pages():
    # A range past the limit cannot be read, so any entry might be in it.
    refs = [_numbered(n) for n in (7, 8)]
    released, withheld = _split(refs, {5: "compared[7-40] across clinics."}, numbered=True)
    assert released == [] and {why for _r, _p, why, _f in withheld} == {"within_citing_text"}
