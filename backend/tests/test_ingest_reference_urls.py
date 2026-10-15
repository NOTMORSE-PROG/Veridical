"""Source-bound URL controls; no network or real bibliography fixtures."""

import pytest

from app.ingest.patterns import load_patterns
from app.ingest.references import extract_references, parse_reference, reference_entry_locations
from app.ingest.schemas import ExtractionResult
from app.models.citation import Citation


def source(*, uri="https://example.org/path", second_uri=None, sliced=False, complete=True):
    first = "[1] Author A. (2024). A title. URL https://example.\norg/path."
    second = "[2] Author B. (2023). Another title."
    blocks = [
        dict(page=1, bbox=(10, 0, 300, 15), text="References", max_font_size=16, bold_ratio=1),
        dict(page=1, bbox=(10, 20, 300, 50), text=first, max_font_size=10, bold_ratio=0),
        dict(page=1, bbox=(10, 60, 300, 75), text=second, max_font_size=10, bold_ratio=0),
    ]
    if sliced:
        blocks[1]["text"] += "\n" + second
        blocks.pop()
    return ExtractionResult.model_validate(
        dict(
            page_count=1,
            anchor_kind="page",
            image_only=False,
            text_chars=200,
            section_tree=dict(source="none", nodes=[]),
            blocks=blocks,
            images=[],
            uri_links_complete=complete,
            uri_links=[
                dict(page=1, bbox=(190, 21, 295, 33), uri=uri, text="https://example."),
                dict(page=1, bbox=(10, 36, 75, 48), uri=second_uri or uri, text="org/path."),
            ],
        )
    )


def test_wrapped_hostname_uses_matching_source_annotations_without_changing_raw_text():
    result = source()
    refs = extract_references(result, load_patterns())
    assert refs[0].url == "https://example.org/path"
    assert "https://example. org/path." in refs[0].raw_text
    assert refs[0].raw_text == reference_entry_locations(result, load_patterns())[0].raw_text


@pytest.mark.parametrize(
    "mutation", ["mismatch", "conflict", "unsafe", "wrong_page", "sliced", "partial", "duplicate"]
)
def test_annotation_recovery_refuses_unbound_or_ambiguous_source(mutation):
    result = source(
        uri="javascript:alert(1)" if mutation == "unsafe" else "https://example.org/path",
        second_uri="https://other.example/path" if mutation == "conflict" else None,
        sliced=mutation == "sliced",
        complete=mutation != "partial",
    )
    if mutation == "mismatch":
        result.uri_links[1].text = "org/another."
    elif mutation == "wrong_page":
        result.uri_links[1].page = 2
    elif mutation == "duplicate":
        result.blocks[1].text += " Another https://example.\norg/path."
    assert extract_references(result, load_patterns())[0].url is None


@pytest.mark.parametrize(
    "following", ["Accessed: 2025-01-02.", "Retrieved: today.", "Further prose.", "path-tail"]
)
def test_unannotated_end_of_line_url_never_absorbs_or_guesses_next_line(following):
    raw = f"Author A. (2024). Title. https://example.org/path\n{following}"
    # Without an annotation this boundary could be prose OR a path continuation.
    assert parse_reference(raw, 0).url is None


def test_complete_single_line_url_is_not_dependent_on_annotation_inventory():
    raw = "Author A. (2024). Title. https://example.org/path. Accessed: today."
    assert parse_reference(raw, 0).url == "https://example.org/path"


def test_old_extraction_without_annotation_inventory_loads_and_abstains_on_wrap():
    raw = source().model_dump()
    raw.pop("uri_links", None)
    raw.pop("uri_links_complete", None)
    result = ExtractionResult.model_validate(raw)
    assert extract_references(result, load_patterns())[0].url is None


def test_annotation_cannot_substitute_a_prefix_of_a_longer_visible_url():
    result = source()
    result.blocks[1].text = result.blocks[1].text.replace("org/path.", "org/path_extra.")
    assert extract_references(result, load_patterns())[0].url is None


@pytest.mark.parametrize(
    "suffix", ["\n_extra.", ".\n_extra.", ".evil/path", ";params", ":12", ",tail", ")tail"]
)
def test_annotation_prefix_cannot_hide_newline_or_punctuation_suffix(suffix):
    result = source()
    result.blocks[1].text = result.blocks[1].text.replace("org/path.", "org/path" + suffix)
    result.uri_links[1].text = "org/path"
    assert extract_references(result, load_patterns())[0].url is None


def test_link_rectangle_cannot_span_competing_reference_entries():
    result = source()
    # Simulated overlapping source blocks make the ownership ambiguous.
    result.blocks[2].bbox = result.blocks[1].bbox
    assert extract_references(result, load_patterns())[0].url is None


def test_printed_url_exceeding_persisted_column_capacity_is_not_truncated():
    raw = "https://example.org/" + "a" * Citation.__table__.c.url.type.length
    draft = parse_reference(raw, 0)
    assert draft.url is None
    assert draft.raw_text == raw


@pytest.mark.parametrize(
    "fragments",
    [
        ("https://", "example.org/path."),
        ("https://example.org/", "path."),
        ("https://example.org/pa", "th."),
    ],
)
def test_annotation_recovers_scheme_and_path_wraps_without_host_rules(fragments):
    result = source()
    result.blocks[1].text = "[1] Author A. (2024). Title. " + "\n".join(fragments)
    for link, fragment in zip(result.uri_links, fragments, strict=True):
        link.text = fragment
    assert extract_references(result, load_patterns())[0].url == "https://example.org/path"
