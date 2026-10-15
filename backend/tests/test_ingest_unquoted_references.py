"""F1/F5 supported numbered metadata; ambiguity is not external verification."""

import pytest

from app.checks.citations.extract import cross_match, extract_in_text_citations
from app.ingest.references import parse_reference
from app.ingest.schemas import TextBlock
from app.models.enums import CitationParseStatus


@pytest.mark.parametrize(
    ("raw", "authors", "title", "year"),
    [
        (
            "[1] Alice Morgan. Clear example title. Example Journal, 2024.",
            ["Alice Morgan"],
            "Clear example title",
            2024,
        ),
        (
            "[2] Alice Morgan. lowercase thesis title. Master's thesis, Example University, "
            "July 2024. Proceedings, July 7, 2024.",
            ["Alice Morgan"],
            "lowercase thesis title",
            2024,
        ),
        (
            "[3] ExampleHub. Example assistant. https://example.test/tool, 2025. "
            "Accessed: August 7, 2025.",
            ["ExampleHub"],
            "Example assistant",
            2025,
        ),
        (
            "[4] Alice Morgan and Bob Chen. Useful system: A study. Example Journal, 2025. "
            "doi:10.1234/example.",
            ["Alice Morgan", "Bob Chen"],
            "Useful system: A study",
            2025,
        ),
        (
            "[5] Alice Morgan and Bob Chen. Example model: A review. In S. Editor (Ed.), "
            "Example Book, 2025. URL: https://example.test/book. Accessed: 2025-08-10.",
            ["Alice Morgan", "Bob Chen"],
            "Example model: A review",
            2025,
        ),
        (
            "[6] Alice Maria Morgan, Bob Chen, and Carla Reyes. A proceedings title. "
            "In EXAMPLE 2023: Conference proceedings. Example Publisher, 2023.",
            ["Alice Maria Morgan", "Bob Chen", "Carla Reyes"],
            "A proceedings title",
            2023,
        ),
        (
            "[7] V. Morgan, Ch. Reyes, and Tatiana V. Rajani Kanth. A notation study. "
            "Example Journal, 11(2):2908-2917, 2025.",
            ["Morgan, V.", "Reyes, Ch.", "Tatiana V. Rajani Kanth"],
            "A notation study",
            2025,
        ),
        (
            "[8] Alice Morgan. lowercase-tool: Repository feedback. "
            "https://example.test/tool, 2023. Accessed: April 28, 2025.",
            ["Alice Morgan"],
            "lowercase-tool: Repository feedback",
            2023,
        ),
        (
            "[9] Alice van Morgan, Bob Chen, and Carla Reyes. Guide 8 - style guidance. "
            "https://example.test/guide, 2001. Accessed: [Insert date here].",
            ["Alice van Morgan", "Bob Chen", "Carla Reyes"],
            "Guide 8 - style guidance",
            2001,
        ),
        (
            "[10] Research and Development Council. Example guidance. Example Press, 2024.",
            ["Research and Development Council"],
            "Example guidance",
            2024,
        ),
    ],
)
def test_supported_unquoted_numbered_metadata(raw, authors, title, year):
    draft = parse_reference(raw, 0)
    assert draft.raw_text == raw
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.authors == authors
    assert draft.title == title
    assert draft.year == year


@pytest.mark.parametrize(
    "raw",
    [
        "Alice Morgan. Unlabelled prose. Example Journal, 2024.",
        "[1] Example development team, 2025. https://example.test. Accessed: April 28, 2025.",
        "[2] Alice Morgan. A work. https://example.test. Accessed: April 28, 2025.",
        "[3] Alice Morgan. A work. Example Journal, 2023, 2024.",
        "[4] Alice Morgan. A work. 2 simple exercises. Example Journal, 2024.",
    ],
)
def test_unsupported_metadata_is_not_completed_from_access_dates_or_ambiguous_text(raw):
    draft = parse_reference(raw, 0)
    assert draft.raw_text == raw
    assert draft.parse_status == CitationParseStatus.parse_failed


def test_merged_author_names_keep_partial_fields_without_invented_delimiters():
    raw = (
        "[1] Alice Morgan Bob Chen Carla Reyes, David Santos. Example work. "
        "Example Journal, August 2023. Accessed: 2025-06-13."
    )
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.authors is None
    assert draft.title == "Example work"
    assert draft.year == 2023
    assert draft.raw_text == raw


def test_original_doi_url_and_raw_text_survive_new_parser():
    raw = "[4] Alice Morgan. Example work. Example Journal, 2024. doi:10.1234/abc. https://example.test/item"
    draft = parse_reference(raw, 0)
    assert draft.doi == "10.1234/abc"
    assert draft.url == "https://example.test/item"
    assert draft.raw_text == raw


@pytest.mark.parametrize("title", ["Reports from May 2024", "Annual Report, 2024"])
def test_title_dates_are_retained_and_cannot_supply_publication_year(title):
    raw = f"[1] Alice Morgan. {title}. Example Journal, 2023."
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.title == title
    assert draft.year == 2023
    assert draft.raw_text == raw
    missing_date = parse_reference(f"[1] Alice Morgan. {title}. Example Journal.", 0)
    assert missing_date.parse_status == CitationParseStatus.parse_failed
    assert missing_date.year is None


@pytest.mark.parametrize(
    "note",
    ["Accessed April 28, 2025", "Retrieved April 28, 2025 from", "Accessed 28 April 2025"],
)
def test_access_notes_without_colons_are_not_publication_dates(note):
    raw = f"[1] Alice Morgan. A useful study. https://example.test/study. {note}."
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.year is None
    assert draft.raw_text == raw


@pytest.mark.parametrize(
    "author_title",
    ["Alice Morgan Resilience", "Alice Morgan Shared Team Methods", "Morgan, A. A useful study"],
)
def test_missing_independent_author_title_boundary_is_not_completed(author_title):
    raw = f"[1] {author_title}. Example Journal, 2024."
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.title is None
    assert draft.raw_text == raw


@pytest.mark.parametrize(
    "locator",
    [
        "https://example.test/ team/tool, 2023.",
        "https://example. test/study, 2024.",
        "https://example.test/study. part/html, 2024.",
        "https://example.test/path; part,2024.",
        "https:// example.test/study, 2024.",
        "doi:10.1234/abc /2024, 2023.",
        "doi:10.1234/ source,2024.",
        "doi:10.1234/item. part,2024.",
    ],
)
def test_whitespace_split_locator_is_not_absorbed_into_title_or_completed(locator):
    raw = (
        f"[1] Alice Morgan. Example tool: Repository feedback. {locator} Accessed: April 28, 2025."
    )
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.title is None
    assert draft.year is None
    assert draft.raw_text == raw


@pytest.mark.parametrize(
    "body",
    [
        "Prior work (Morgan, 2024) supports this.",
        "Morgan (2024) reports a result.",
        "Recently Morgan (2024) reports a result.",
        "Recently Morgan et al. (2024) reports a result.",
    ],
)
def test_natural_order_names_cannot_create_false_orphan_or_unused_findings(body):
    reference = parse_reference("[1] Alice Morgan. Example work. Example Journal, 2024.", 0)
    blocks = [TextBlock(page=1, text=body, max_font_size=12, bold_ratio=0)]
    cross = cross_match(extract_in_text_citations(blocks), [reference])
    assert cross.orphans == []
    assert cross.linked == []
    assert len(cross.ambiguous) == 1
    assert cross.possibly_cited == [reference]
    assert cross.confidently_uncited == []


def test_natural_name_ambiguity_does_not_hide_another_matching_source():
    references = [
        parse_reference("Morgan, B. (2024). One source. Example Journal.", 0),
        parse_reference("[2] Alice Morgan. Another source. Example Journal, 2024.", 1),
    ]
    blocks = [TextBlock(page=1, text="A result (Morgan, 2024).", max_font_size=12, bold_ratio=0)]
    cross = cross_match(extract_in_text_citations(blocks), references)
    assert cross.linked == cross.orphans == cross.confidently_uncited == []
    assert len(cross.ambiguous) == 1
    assert cross.possibly_cited == references
