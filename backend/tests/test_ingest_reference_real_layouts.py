"""F1.5/F5: reference lists laid out the way real capstones print them.

Every fixture is synthetic: invented entries in the shapes found on real
theses (first-line indents, export markup, a labels column read before its
heading, numbered appendices after the list), never copied thesis text.
"""

import pytest

from app.checks.citations import verify
from app.ingest.patterns import load_patterns
from app.ingest.reference_inventory import reference_source_scope_issues
from app.ingest.references import (
    _PREVIOUS_READER,
    extract_references,
    non_reference_blocks,
    parse_reference,
    previous_reference_reader,
    select_references,
)
from app.ingest.schemas import ExtractionResult, ImageBlock, SectionTree, TextBlock
from app.models.citation import Citation
from app.models.enums import CitationParseStatus

FLUSH, INDENT = 108.0, 144.0


def _extraction(lines, *, images=()):
    """lines: (page, x0, text, bold) in reading order; one block per line."""
    blocks, y = [], {}
    for page, x0, text, bold in lines:
        top = y.get(page, 72.0)
        y[page] = top + 21
        blocks.append(
            TextBlock(
                page=page,
                text=text,
                bbox=(x0, top, 540.0, top + 14),
                max_font_size=12,
                bold_ratio=1.0 if bold else 0.0,
            )
        )
    pages = max(page for page, *_ in lines)
    return ExtractionResult(
        page_count=pages,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        blocks=blocks,
        images=list(images),
        section_tree=SectionTree(source="none", nodes=[]),
    )


def _first_line_indent_list(*, appendix=True, wrapped_author=True):
    body = [(1, FLUSH, "The system was evaluated with clinic staff (Cruz, 2024).", False)]
    entries = [
        (2, INDENT, "Cruz, A. B., & Reyes, M. (2024). Frame fitting with face landmarks in", False),
        (2, FLUSH, "optical clinics. *Journal of Example Computing, 8*(1), 10–20.", False),
        (
            2,
            FLUSH,
            "[https://doi.org/10.5555/jec.2024.001](https://doi.org/10.5555/jec.2024.001)",
            False,
        ),
        (
            2,
            INDENT,
            "Dela Rosa, P. (2023). *Augmented reality in retail eyewear*. Example Press.",
            False,
        ),
        (
            2,
            FLUSH,
            "[https://example.org/ar-eyewear](https://www.google.com/search?q=https%3A",
            False,
        ),
        (2, FLUSH, "%2F%2Fexample.org%2Far-eyewear)", False),
        (
            2,
            INDENT,
            "Santos, L., et al. (2022). Recommending frames from face shape. *Example",
            False,
        ),
        (2, FLUSH, "Conference on Vision*, 1–7.", False),
        (
            2,
            FLUSH,
            "[https://doi.org/10.5555/ecv.2022.7](https://www.google.com/search?q=https%3A",
            False,
        ),
        (2, FLUSH, "%2F%2Fdoi.org%2F10.5555%2Fecv.2022.7)", False),
        (2, INDENT, "Tan, R. (2020). Fitting rooms online. Journal of Examples, 2(4), 1–5.", False),
    ]
    if wrapped_author:
        entries += [
            (2, INDENT, "Lim, J., Tan, K., Uy, R., Go, S., Sy, H., & Chua, M.", False),
            (
                2,
                FLUSH,
                "D. (2021). A booking app for clinics. Journal of Examples, 3(2), 5–9.",
                False,
            ),
        ]
    lines = [*body, (2, FLUSH, "REFERENCES", True), *entries]
    if appendix:
        lines += [
            (3, 290.0, "APPENDIX A", True),
            (3, FLUSH, "1. How do you usually choose a frame for a client?", False),
            (3, FLUSH, "2. What makes the choice difficult?", False),
        ]
    return _extraction(lines)


def test_first_line_indent_list_reads_entries_through_export_markup():
    extraction = _first_line_indent_list()
    patterns = load_patterns()
    selection = select_references(extraction, patterns)
    assert not selection.issues
    assert selection.membership_issues == ("reference_tail_membership_unresolved",)
    drafts = extract_references(extraction, patterns)
    assert [d.authors[0] for d in drafts] == [
        "Cruz, A. B.",
        "Dela Rosa, P.",
        "Santos, L.",
        "Tan, R.",
        "Lim, J.",
    ]
    cruz, rosa, santos, _tan, lim = drafts
    assert cruz.title == "Frame fitting with face landmarks in optical clinics"
    assert cruz.venue.startswith("Journal of Example Computing, 8(1)")
    assert cruz.doi == "10.5555/jec.2024.001"
    assert cruz.url == "https://doi.org/10.5555/jec.2024.001"
    assert rosa.title == "Augmented reality in retail eyewear"
    # The shown address, never the search redirect it was linked to.
    assert rosa.url == "https://example.org/ar-eyewear"
    assert santos.doi == "10.5555/ecv.2022.7"
    assert "google" not in (santos.url or "")
    assert lim.year == 2021 and lim.authors[-1] == "Chua, M. D."
    # Raw text is kept exactly as printed, markup included.
    assert "*Journal of Example Computing" in cruz.raw_text
    # The interview appendix is neither an entry nor body text.
    body = non_reference_blocks(extraction, patterns, exclude_heading=True)
    assert [b.text for b in body] == [extraction.blocks[0].text]


def test_hanging_indent_list_with_a_wrapped_author_line_keeps_hanging_indent():
    # A hanging-indent list always starts flush left. Continuation lines that
    # happen to read as openings ("D. (2021). Title") must not flip the layout.
    lines = [(1, 72.0, "REFERENCES", True)]
    for name in ("Lim", "Tan", "Uy"):
        lines += [
            (1, 72.0, f"{name}, J., Abad, K., Bello, R., Cruz, S., Diaz, H., & Chua, M.", False),
            (
                1,
                108.0,
                "D. (2021). A booking app for clinics. Journal of Examples, 3(2), 5–9.",
                False,
            ),
        ]
    drafts = extract_references(_extraction(lines), load_patterns())
    assert [d.authors[0] for d in drafts] == ["Lim, J.", "Tan, J.", "Uy, J."]
    assert all(d.parse_status == CitationParseStatus.parsed for d in drafts)


def test_indented_first_entry_without_enough_indented_openings_keeps_hanging_indent():
    lines = [
        (1, 72.0, "REFERENCES", True),
        (1, 108.0, "Abad, K. (2020). First title. Journal of Examples, 1, 1–2.", False),
        (1, 72.0, "Bello, R. (2021). Second title. Journal of Examples, 2, 3–4.", False),
        (1, 72.0, "Cruz, S. (2022). Third title. Journal of Examples, 3, 5–6.", False),
    ]
    drafts = extract_references(_extraction(lines), load_patterns())
    # One indented opening is not a layout: every flush-left opening still
    # starts its own entry.
    assert [d.authors[0] for d in drafts] == ["Abad, K.", "Bello, R.", "Cruz, S."]


@pytest.mark.parametrize(
    ("text", "doi", "url"),
    [
        (
            "Cruz, A. (2020). A title. The Lancet, 1(2), 3–4. "
            "[https://doi.org/10.1016/S0140-6736(20)30185-9]"
            "(https://doi.org/10.1016/S0140-6736(20)30185-9)",
            "10.1016/S0140-6736(20)30185-9",
            "https://doi.org/10.1016/S0140-6736(20)30185-9",
        ),
        (
            "Cruz, A. (2020). A title. Journal of Examples, 1(2), 3–4. "
            "[doi](https://doi.org/10.5555/abc.1)",
            "10.5555/abc.1",
            "https://doi.org/10.5555/abc.1",
        ),
        (
            "Cruz, A. (2020). A title. Journal of Examples, 1(2), 3–4. "
            "[Article](https://www.google.com/search?q=https%3A%2F%2Fexample.org%2Fa)",
            None,
            None,
        ),
    ],
)
def test_link_markup_yields_the_identifier_it_shows(text, doi, url):
    extraction = _extraction([(1, 72.0, "REFERENCES", True), (1, 72.0, text, False)])
    (draft,) = extract_references(extraction, load_patterns())
    assert draft.doi == doi
    assert draft.url == url
    assert draft.title == "A title"


@pytest.mark.parametrize(
    "text",
    [
        "Cruz, A. (2020). Search with A* and B* heuristics. Journal of Examples, 1, 2–3.",
        "Cruz, A. (2020). Reading [WHO](2020) guidance. Journal of Examples, 1, 2–3.",
    ],
)
def test_plain_text_that_only_looks_like_markup_is_untouched(text):
    extraction = _extraction([(1, 72.0, "REFERENCES", True), (1, 72.0, text, False)])
    (draft,) = extract_references(extraction, load_patterns())
    assert draft.title == text.split("). ", 1)[1].split(". Journal")[0]


def test_comma_after_the_date_is_not_part_of_the_title():
    draft = parse_reference(
        "Cruz, A., (2021), A comma-dated title. Journal of Examples, 2(1), 4–5.", 0
    )
    assert draft.title == "A comma-dated title"


def test_text_after_a_wrapped_address_is_not_read_as_a_title():
    draft = parse_reference(
        "Client challenge. (n.d.). https://example.org/books/a-long-address- "
        "continued-part#content=query:layered",
        0,
    )
    assert draft.title is None
    assert draft.parse_status == CitationParseStatus.parse_failed


def test_title_printed_before_the_date_is_not_read_as_authors():
    draft = parse_reference(
        "Analysis of recommender systems for clinics. (2024). Example Library. "
        "https://example.org/document/1",
        0,
    )
    assert draft.authors is None and draft.title is None
    assert draft.parse_status == CitationParseStatus.parse_failed


@pytest.mark.parametrize(
    "authors",
    [
        "Example Infotech Services Limited",
        "Department of Science and Technology",
        "Google",
        "Republic of the Philippines",
        "Bangko Sentral ng Pilipinas",
        "Pamantasan ng Lungsod ng Maynila",
    ],
)
@pytest.mark.parametrize("venue", [" Example Press.", ""])
def test_organisation_authors_stay_authors(authors, venue):
    draft = parse_reference(f"{authors}. (2023). A guide to the apps.{venue}", 0)
    assert draft.authors == [authors]
    assert draft.title == "A guide to the apps"


def test_long_lowercase_author_with_a_title_after_the_date_stays_an_author():
    # Two parts after the date: the first is the title, so the text before the
    # date is not a title.
    draft = parse_reference(
        "Analysis of recommender systems group. (2024). A guide. Example Press.", 0
    )
    assert draft.authors == ["Analysis of recommender systems group"]


def test_author_list_closed_by_a_period_before_a_quoted_title():
    draft = parse_reference(
        "[3] A. B. Cruz, C. Reyes, and D. E. Santos. “Sensing comfort in rooms.” "
        "Journal of Examples, 12(), 2020, pp. 1–10.",
        2,
    )
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.authors == ["Cruz, A. B.", "Reyes, C.", "Santos, D. E."]
    assert draft.year == 2020
    assert draft.title.startswith("Sensing comfort in rooms")


def test_period_before_a_quote_still_needs_initials_first_names():
    draft = parse_reference(
        "[3] The Example Group. “Sensing comfort in rooms.” Journal, 12(), 2020.", 2
    )
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.authors is None


def test_combined_heading_name_is_a_reference_list():
    extraction = _extraction(
        [
            (1, 72.0, "References and bibliography", True),
            (1, 72.0, "Cruz, A. (2020). A title. Journal of Examples, 1, 2–3.", False),
        ]
    )
    assert len(extract_references(extraction, load_patterns())) == 1


def _labels_column(*, closing="APPENDIX A: RESEARCH PLAN"):
    """A labels column read before the heading on the list's first page."""
    blocks = [
        TextBlock(
            page=1,
            text="Earlier chapter text.",
            bbox=(85, 60, 540, 70),
            max_font_size=12,
            bold_ratio=0,
        ),
        TextBlock(page=2, text="[1]", bbox=(89, 107, 106, 121), max_font_size=12, bold_ratio=0),
        TextBlock(page=2, text="[2]", bbox=(89, 161, 106, 174), max_font_size=12, bold_ratio=0),
        TextBlock(
            page=2, text="REFERENCES", bbox=(265, 72, 362, 87), max_font_size=14, bold_ratio=1
        ),
        TextBlock(
            page=2,
            text="Abad, K., and Bello, R., (2021), A first study of sensors. Journal of",
            bbox=(121, 108, 542, 121),
            max_font_size=12,
            bold_ratio=0,
        ),
        TextBlock(
            page=2, text="Examples.", bbox=(121, 128, 300, 142), max_font_size=12, bold_ratio=0
        ),
        TextBlock(
            page=2,
            text="Cruz, S., (2022), Learning to read documents. Example University.",
            bbox=(121, 161, 542, 174),
            max_font_size=12,
            bold_ratio=0,
        ),
        TextBlock(page=3, text="[3]", bbox=(83, 71, 106, 85), max_font_size=12, bold_ratio=0),
        TextBlock(
            page=3,
            text="Diaz, H., (2023), A third study of sensors. Journal of Examples.",
            bbox=(121, 71, 542, 85),
            max_font_size=12,
            bold_ratio=0,
        ),
        TextBlock(page=4, text=closing, bbox=(205, 72, 422, 87), max_font_size=14, bold_ratio=1),
        TextBlock(
            page=4, text="1. Background", bbox=(85, 100, 300, 112), max_font_size=12, bold_ratio=0
        ),
        TextBlock(
            page=4, text="2. Related Work", bbox=(85, 120, 300, 132), max_font_size=12, bold_ratio=0
        ),
    ]
    return ExtractionResult(
        page_count=4,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        blocks=blocks,
        images=[ImageBlock(page=4, bbox=(85, 140, 538, 360))],
        section_tree=SectionTree(source="none", nodes=[]),
    )


def test_labels_read_before_the_heading_rejoin_their_entries():
    extraction = _labels_column()
    patterns = load_patterns()
    selection = select_references(extraction, patterns)
    assert not selection.issues
    assert selection.membership_issues == ("reference_tail_membership_unresolved",)
    drafts = extract_references(extraction, patterns)
    assert [d.raw_text[:3] for d in drafts] == ["[1]", "[2]", "[3]"]
    assert [d.year for d in drafts] == [2021, 2022, 2023]
    assert drafts[0].title == "A first study of sensors"
    # An image set below the closing heading follows the list.
    assert not reference_source_scope_issues(extraction, selection, require_complete=False)


def test_image_above_the_closing_heading_still_blocks_a_complete_list():
    extraction = _labels_column()
    extraction.images = [ImageBlock(page=4, bbox=(85, 20, 538, 60))]
    selection = select_references(extraction, load_patterns())
    assert reference_source_scope_issues(extraction, selection, require_complete=False) == (
        "reference_rich_content_unassessed",
    )


def test_entry_cut_short_by_the_closing_heading_keeps_the_end_unclear():
    lines = [
        (1, FLUSH, "REFERENCES", True),
        (1, FLUSH, "Abad, K. (2020). First title. Journal of Examples, 1, 1–2.", False),
        (1, FLUSH, "Cruz, A. (2022", False),
        (2, 290.0, "APPENDIX A", True),
        (2, FLUSH, "1. How do you usually choose a frame for a client?", False),
    ]
    extraction = _extraction(lines)
    assert select_references(extraction, load_patterns()).issues == ("ambiguous_reference_end",)
    assert not extract_references(extraction, load_patterns())


def test_complete_but_unparsed_entry_does_not_block_the_list():
    lines = [
        (1, FLUSH, "REFERENCES", True),
        (1, FLUSH, "Abad, K. (2020). First title. Journal of Examples, 1, 1–2.", False),
        (1, FLUSH, "Loo, M. (2023). Shape study. 2023 Example Conference (EC), 1–7.", False),
        (2, 290.0, "APPENDIX A", True),
        (2, FLUSH, "1. How do you usually choose a frame for a client?", False),
    ]
    extraction = _extraction(lines)
    drafts = extract_references(extraction, load_patterns())
    assert len(drafts) == 2
    assert drafts[1].parse_status == CitationParseStatus.parse_failed


def test_previous_reader_reproduces_rows_saved_before_this_reader():
    extraction = _first_line_indent_list(appendix=False, wrapped_author=False)
    patterns = load_patterns()
    with previous_reference_reader():
        previous = extract_references(extraction, patterns)
    current = extract_references(extraction, patterns)
    # Before, each flush-left line read as its own entry.
    assert len(previous) > len(current) == 4


@pytest.mark.asyncio
async def test_recheck_of_rows_saved_by_the_previous_reader_keeps_its_outcome(monkeypatch):
    from tests.test_citations_partial_source import run

    lines = [
        (1, 72.0, "The study follows earlier work (Cruz, 2021).", False),
        (2, 72.0, "REFERENCES", True),
        (2, 72.0, "Cruz, A., (2021), A comma-dated title. Journal of Examples, 2(1), 4–5.", False),
    ]
    extraction = _extraction(lines)
    patterns = load_patterns()
    with previous_reference_reader():
        saved = [
            Citation(manuscript_id=1, **vars(d)) for d in extract_references(extraction, patterns)
        ]
    assert saved[0].title.startswith(",")
    result, _, provider, _ = await run(monkeypatch, extraction, saved)
    assert result.detail["reference_inventory_status"] == "bound"
    assert result.detail["coverage_reason"] != "reference_source_projection_unbound"
    assert provider.await_count == 1
    # A changed saved field matches neither reader and stays unbound.
    saved[0].year = 1999
    result, _, provider, _ = await run(monkeypatch, extraction, saved)
    assert result.detail["coverage_reason"] == "reference_source_projection_unbound"
    provider.assert_not_called()


def _reader_in_use(saved, extraction, patterns):
    with verify._reader_for_saved_rows(saved, extraction, patterns):
        return _PREVIOUS_READER.get()


def test_saved_rows_choose_the_reader_that_saved_them():
    extraction = _first_line_indent_list(appendix=False)
    patterns = load_patterns()
    current = [
        Citation(manuscript_id=1, **vars(d)) for d in extract_references(extraction, patterns)
    ]
    assert _reader_in_use(current, extraction, patterns) is False
    with previous_reference_reader():
        old = [
            Citation(manuscript_id=1, **vars(d)) for d in extract_references(extraction, patterns)
        ]
    assert _reader_in_use(old, extraction, patterns) is True
    old[0].title = "Edited after upload"
    assert _reader_in_use(old, extraction, patterns) is False


def test_previous_reader_keeps_link_markup_in_saved_locators():
    # Rows saved before markup was read through kept the whole printed link;
    # the previous reader must reproduce that, or such rows would not bind.
    text = (
        "Cruz, A. (2020). A title. Journal of Examples, 1(2), 3–4. "
        "[https://doi.org/10.5555/abc.1](https://www.google.com/search?q=https%3A%2F%2Fdoi.org)"
    )
    extraction = _extraction([(1, 72.0, "REFERENCES", True), (1, 72.0, text, False)])
    with previous_reference_reader():
        (old,) = extract_references(extraction, load_patterns())
    (new,) = extract_references(extraction, load_patterns())
    assert "](" in old.doi and "](" in (old.url or "](")
    assert new.doi == "10.5555/abc.1" and new.url == "https://doi.org/10.5555/abc.1"


def test_unpaired_link_markup_cuts_identifiers_at_the_target():
    text = (
        "Cruz, A. (2020). A title. Journal of Examples, 1(2), 3–4. "
        "[https://doi.org/10.5555/abc.1](www.example.org/redirect)"
    )
    extraction = _extraction([(1, 72.0, "REFERENCES", True), (1, 72.0, text, False)])
    (draft,) = extract_references(extraction, load_patterns())
    assert draft.doi == "10.5555/abc.1"
    assert draft.url == "https://doi.org/10.5555/abc.1"


def test_boundary_decisions_parse_the_view_ingestion_stores():
    from app.ingest.references import _parse_group

    text = "Cruz, A. (2020). A title. *Journal of Examples*, 1(2), 3–4."
    extraction = _extraction([(1, 72.0, "REFERENCES", True), (1, 72.0, text, False)])
    (stored,) = extract_references(extraction, load_patterns())
    gate = _parse_group([text])
    assert (
        (gate.title, gate.venue)
        == (stored.title, stored.venue)
        == (
            "A title",
            "Journal of Examples, 1(2), 3–4",
        )
    )


@pytest.mark.parametrize(
    ("indented", "flush", "reads_indent"),
    [(2, 0, False), (3, 0, True), (4, 1, True), (3, 1, False)],
)
def test_first_line_indent_needs_enough_indented_openings(indented, flush, reads_indent):
    lines = [(1, FLUSH, "REFERENCES", True)]
    for index in range(indented):
        lines += [
            (1, INDENT, f"Abad{index}, K. (2020). A long title that wraps onto a", False),
            (1, FLUSH, "second line. Journal of Examples, 1, 1–2.", False),
        ]
    for _ in range(flush):
        lines += [(1, FLUSH, "D. (2021). A wrapped author tail. Journal, 1, 1–2.", False)]
    drafts = extract_references(_extraction(lines), load_patterns())
    titles = [d.title for d in drafts]
    whole = "A long title that wraps onto a second line"
    assert (titles.count(whole) == indented) is reads_indent


def test_full_flush_left_entry_in_a_first_line_indent_list_is_refused():
    lines = [(1, FLUSH, "REFERENCES", True)]
    for name in ("Abad", "Bello", "Cruz", "Diaz"):
        lines += [
            (1, INDENT, f"{name}, K. (2020). A long title that wraps onto a", False),
            (1, FLUSH, "second line. Journal of Examples, 1, 1–2.", False),
        ]
    lines += [(1, FLUSH, "Estrada, L. (2019). Typed flush left. Journal, 2, 3–4.", False)]
    extraction = _extraction(lines)
    selection = select_references(extraction, load_patterns())
    assert "ambiguous_reference_entry_boundaries" in selection.issues
    assert not extract_references(extraction, load_patterns())


@pytest.mark.parametrize(
    ("entry", "complete"),
    [
        ("Loo, M. (2023). Shape study. 2023 Example Conference (EC), 1–7.", True),
        ("Loo, M. (2023). Shape study. 2023 Example Conference (EC), 1–7", False),
        ("Loo, M. (2023). Shape study. 2023 Example Conference, https://example.org/a", True),
        ("Loo, M. (2023).", False),
        ("Loo, M. 2023 Example Conference, 1–7.", False),
        (
            "Loo, M. https://example.org/x (2023). Shape study. 2023 Example Conference (EC), 1–7",
            False,
        ),
    ],
)
def test_entry_completeness_before_an_unclassified_appendix(entry, complete):
    from app.ingest.references import _complete_entry

    assert _complete_entry([entry], 0) is complete


def _with_reading_order(extraction, unresolved):
    from app.ingest.schemas import PageReadingOrder

    for index, block in enumerate(extraction.blocks):
        block.source_line_ids = [f"{block.page}:{index}:0"]
    extraction.reading_order = [
        PageReadingOrder(
            page=page,
            status="unresolved" if page in unresolved else "ordered",
            reason="table_regions_unresolved" if page in unresolved else None,
            source_inventory_sha256="a" * 64,
            source_line_count=sum(block.page == page for block in extraction.blocks),
            ordered_source_line_ids=[
                identifier
                for block in extraction.blocks
                if block.page == page
                for identifier in block.source_line_ids
            ],
        )
        for page in range(1, extraction.page_count + 1)
    ]
    return extraction


def test_labels_read_first_do_not_pull_the_previous_page_into_the_list_scope():
    from app.ingest.references import reference_reading_order_issues

    extraction = _with_reading_order(_labels_column(), unresolved={1})
    selection = select_references(extraction, load_patterns())
    assert not reference_reading_order_issues(extraction, selection)
    assert len(extract_references(extraction, load_patterns())) == 3


@pytest.mark.parametrize(
    "image",
    [
        ImageBlock(page=4, bbox=(300, 20, 538, 60)),  # above the closing heading
        ImageBlock(page=3, bbox=(85, 140, 538, 360)),  # on a page inside the list
    ],
)
def test_images_that_may_hold_entries_still_block_a_complete_list(image):
    extraction = _labels_column()
    extraction.images = [image]
    selection = select_references(extraction, load_patterns())
    assert reference_source_scope_issues(extraction, selection, require_complete=False)


def test_image_beside_the_closing_heading_in_another_column_still_blocks():
    extraction = _labels_column()
    # Below the heading's top but not under it: another column of the page.
    extraction.images = [ImageBlock(page=4, bbox=(440, 90, 538, 300))]
    selection = select_references(extraction, load_patterns())
    assert reference_source_scope_issues(extraction, selection, require_complete=False)


def test_unreadable_reference_policy_does_not_break_list_selection(monkeypatch, tmp_path):
    from app.config import get_settings

    policy = tmp_path / "bad.json"
    policy.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("REFERENCE_METADATA_PATTERNS_FILE", str(policy))
    get_settings.cache_clear()
    extraction = _first_line_indent_list(appendix=False)
    patterns = load_patterns()
    selection = select_references(extraction, patterns)
    assert selection.span
    assert non_reference_blocks(extraction, patterns, ambiguous_entries_ok=True)


@pytest.mark.parametrize(
    "text",
    [
        "[4] A. Cruz. Smart Farming Systems. “Chapter 3: Sensors.” Example Press, 2020.",
        "[8] A. Cruz. Mobile app development. M.S. thesis, “TIP,” Manila, 2020.",
    ],
)
def test_a_sentence_inside_the_author_list_is_not_an_author(text):
    draft = parse_reference(text, 3)
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.authors is None


def test_a_label_above_the_heading_is_not_taken_into_the_list():
    extraction = _labels_column()
    stray = TextBlock(page=2, text="[9]", bbox=(89, 40, 106, 52), max_font_size=12, bold_ratio=0)
    extraction.blocks.insert(1, stray)
    selection = select_references(extraction, load_patterns())
    assert not selection.issues
    assert len(extract_references(extraction, load_patterns())) == 3


def test_flush_left_line_continuing_an_unfinished_line_is_not_a_contradiction():
    lines = [(1, FLUSH, "REFERENCES", True)]
    for name in ("Abad", "Bello", "Cruz", "Diaz"):
        lines += [
            (1, INDENT, f"{name}, K. (2020). A long title that wraps onto a", False),
            (1, FLUSH, "second line. Journal of Examples, 1, 1–2.", False),
        ]
    lines += [
        (1, INDENT, "View of an appointment system for the", False),
        (
            1,
            FLUSH,
            "Example State University. (n.d.). Journal of Examples. https://example.org/a",
            False,
        ),
    ]
    extraction = _extraction(lines)
    selection = select_references(extraction, load_patterns())
    assert "ambiguous_reference_entry_boundaries" not in selection.issues
    assert len(extract_references(extraction, load_patterns())) == 5


def test_numbered_list_with_period_closed_authors_reads_every_field():
    # The shape of a biblatex numeric list: labels, a period after the
    # authors, a quoted title, an empty issue "()" and an access address.
    lines = [(1, 84.0, "References", True)]
    entries = [
        "[1] R. Cruz and G. Reyes. “An adaptive model of thermal comfort.” Example "
        "Journal, 104(), 1998, pp. 145–167. url: https://example.org/CorpusID:1.",
        "[2] J. F. Santos, M. A. Lim, and P. de la Paz. “Comfort standards for "
        "buildings.” Energy and Examples, 34(), 2002, pp. 563–572.",
        "[3] S. Tan et al. “Sensing rooms in the wild.” Journal of Examples, 200(), "
        "2025, p. 103510. doi: 10.5555/je.2025.103510.",
    ]
    lines += [(1, 84.0, text, False) for text in entries]
    drafts = extract_references(_extraction(lines), load_patterns())
    assert [d.parse_status for d in drafts] == [CitationParseStatus.parsed] * 3
    assert [d.authors for d in drafts] == [
        ["Cruz, R.", "Reyes, G."],
        ["Santos, J. F.", "Lim, M. A.", "de la Paz, P."],
        ["Tan, S."],
    ]
    assert [d.year for d in drafts] == [1998, 2002, 2025]
    assert drafts[0].title.startswith("An adaptive model of thermal comfort")
    assert drafts[2].doi == "10.5555/je.2025.103510"
