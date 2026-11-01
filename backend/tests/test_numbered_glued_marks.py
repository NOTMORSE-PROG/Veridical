"""A numbered citation glued to the word before it ("compared[7]-[10]") is read
like any other mark: its bridge covers the numbers between, and one that cannot
be read leaves every entry possibly cited."""

from types import SimpleNamespace

from app.checks.citations.numbered import analyze_numbered_citations
from app.ingest.schemas import TextBlock


def _refs(count):
    return [
        SimpleNamespace(
            order_index=index,
            raw_text=f"[{index + 1}] Author, A. (2020). Source {index + 1}.",
            authors=["Author"],
            year=2020,
        )
        for index in range(count)
    ]


def _block(text):
    return TextBlock(text=text, page=1, max_font_size=11.0, bold_ratio=0.0)


# Entries 1 to 6 and 11 are cited plainly, so the list reads as cited in brackets.
CITED = " ".join(f"Prior work [{n}] supports this point." for n in (1, 2, 3, 4, 5, 6, 11))


def _uncited(sentence):
    assessed = analyze_numbered_citations(
        [_block(f"{CITED} {sentence}")], _refs(12), max_range_span=20
    )
    return sorted(ref.order_index + 1 for ref in assessed.confidently_uncited)


def test_a_bridge_started_by_a_glued_mark_covers_the_numbers_between():
    assert _uncited("The kiosk was compared[7]-[10] across clinics.") == [12]


def test_an_unreadable_glued_mark_leaves_every_entry_possibly_cited():
    # A range past the limit cannot be read, so it could cite any entry.
    assert _uncited("The kiosk was compared[7-40] across clinics.") == []


def test_a_control_without_the_glued_marks_still_finds_the_unused_entries():
    assert _uncited("The kiosk was compared across clinics.") == [7, 8, 9, 10, 12]


def test_an_unreadable_locator_mark_leaves_every_entry_possibly_cited():
    # A number too long to be a reference number cannot be read.
    assert _uncited("The kiosk was compared [1234567890, p. 3] across clinics.") == []


def test_an_unreadable_glued_or_body_mark_in_a_table_context_withholds_nothing_new():
    # Not a citation the scan missed, so the unused entries are still found.
    assert _uncited("See Table[1-99] for the values.") == [7, 8, 9, 10, 12]
    assert _uncited("See Table [1-99] for the values.") == [7, 8, 9, 10, 12]


def test_a_bridge_never_spans_further_than_the_range_limit():
    # [7] and [60] are 53 apart, past the limit of 20, so 8 to 59 are not
    # covered; 7 is still cited by its own mark.
    assert _uncited("The kiosk was compared[7]-[60] across clinics.") == [8, 9, 10, 12]


def test_a_range_before_a_page_locator_is_read():
    for mark in ("[7-10, pp. 3-9]", "[7-10; p. 3]", "[7, 8-10, p. 12]"):
        assert _uncited(f"The kiosk was compared {mark} across clinics.") == [12], mark
