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
