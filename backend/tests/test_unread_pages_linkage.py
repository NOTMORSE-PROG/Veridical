"""Which uncited claims a page left unread withholds: only the entries an unread
page names, or could be citing when the entry has no name to look for."""

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


def _split(candidates, unread_text, *, numbered=False, citing_pages=frozenset({3, 4, 5, 6})):
    return split_by_unread_pages(
        candidates,
        _source(unread_text),
        numbered=numbered,
        citing_pages=set(citing_pages),
        settings=SETTINGS,
    )


def test_an_entry_no_unread_page_names_is_still_reported():
    released, withheld = _split([BLACK], {18: "Table 4. Mean scores 4.1 4.3 3.9"})
    assert released == [BLACK] and withheld == []


def test_an_entry_an_unread_page_names_is_withheld_with_that_page():
    released, withheld = _split([BLACK], {5: "as Black (2017) found", 18: "Table 4."})
    assert released == []
    assert withheld == [(BLACK, [5], "named")]


def test_a_surname_shared_by_two_entries_withholds_both():
    # The page cannot say which Cruz it cites, so neither is called uncited.
    released, withheld = _split([CRUZ_A, CRUZ_B], {4: "according to Cruz"})
    assert released == []
    assert [ref for ref, _pages, _why in withheld] == [CRUZ_A, CRUZ_B]


def test_an_entry_without_a_usable_name_falls_back_to_where_the_citing_text_is():
    inside = _split([NAMELESS], {4: "some text"})
    assert inside[1] == [(NAMELESS, [4], "within_citing_text")]
    outside = _split([NAMELESS], {18: "some text"})
    assert outside == ([NAMELESS], [])
    # With no citing pages found, any unread page might be citing it.
    unknown = _split([NAMELESS], {18: "some text"}, citing_pages=())
    assert unknown[1] == [(NAMELESS, [18], "within_citing_text")]


def test_a_numbered_entry_is_withheld_only_where_its_bracket_number_appears():
    ref = _ref(6, "[7] Lee, K. Sensor networks.", ["Lee, K."])
    released, withheld = _split([ref], {5: "as shown in [3, 7-9] and [12]"}, numbered=True)
    assert withheld == [(ref, [5], "named")] and released == []
    released, withheld = _split([ref], {5: "values 7 and 70 in the table, see [17]"}, numbered=True)
    assert released == [ref] and withheld == []


def test_the_lead_names_the_pages_and_does_not_count():
    [lead] = unread_page_leads([(BLACK, [63, 66], "named")], numbered=False, settings=SETTINGS)
    detail = lead.detail
    assert detail["kind"] == "uncited_reference"
    assert detail["affects_readiness"] is False
    assert detail["unread_pages"] == [63, 66]
    assert "pages 63 and 66" in detail["reason"]
    assert "does not change readiness" in detail["reason"]
    assert lead.severity.value == "low"
