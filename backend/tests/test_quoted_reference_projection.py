"""F1.5/F5 source-bound quoted parsing and historical-row protection."""

import copy
import json
from pathlib import Path

import pytest

from app.checks.citations.projection import project_quoted_metadata
from app.ingest.patterns import load_patterns
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns
from app.ingest.references import extract_references, parse_reference, quoted_title_span
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.citation import Citation
from app.models.enums import CitationParseStatus
from tests.native_math_fixtures import record_empty_native_inventory
from tests.test_citations_partial_source import run

RAW = 'Smith, J., & Cruz, A. (2024), "A source title," Proceedings of Examples, pp. 1-8.'


def source(raw=RAW):
    return record_empty_native_inventory(
        ExtractionResult(
            page_count=1,
            anchor_kind="paragraph",
            image_only=False,
            text_chars=len(raw) + 10,
            section_tree=SectionTree(source="none", nodes=[]),
            blocks=[
                TextBlock(text="References", paragraph=1, max_font_size=12, bold_ratio=1),
                TextBlock(text=raw, paragraph=2, max_font_size=12, bold_ratio=0),
            ],
            images=[],
        )
    )


def old_rows(extraction):
    return [
        Citation(id=i + 10, manuscript_id=1, **vars(draft))
        for i, draft in enumerate(
            extract_references(extraction, load_patterns(), _legacy_quoted_titles=True)
        )
    ]


@pytest.mark.parametrize("opening,closing", [('"', '"'), ("“", "”")])
@pytest.mark.parametrize("separator", [". ", ", ", " "])
def test_hybrid_author_date_prefix_and_exact_title(opening, closing, separator):
    title = "Why? mW/MW, 2:1 and COVID (2020)!"
    raw = f"Smith, J., & Cruz, A. (2024){separator}{opening}{title},{closing} Venue, 1-8."
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.authors == ["Smith, J.", "Cruz, A."]
    assert draft.year == 2024
    assert draft.title == title
    assert draft.doi is None
    span = quoted_title_span(raw, raw.index("2024") + len("2024)"))
    assert raw[span.start : span.end] == draft.title
    assert draft.venue == "Venue, 1-8"


@pytest.mark.parametrize(
    "title", ["A", "Why?", "Wow!", "U.S. policy", "10.1234/example", "https://example.test/a"]
)
def test_quoted_title_content_is_not_locator_or_sentence_tail(title):
    draft = parse_reference(f'Smith, J. (2024). "{title}," Example Journal.', 0)
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.title == title


@pytest.mark.parametrize(
    "remainder",
    [
        '"Missing close. Venue.',
        '“Wrong close". Venue.',
        "”Wrong opener”. Venue.",
        '"". Venue.',
        '" , ". Venue.',
        '"An "inner" title," Venue.',
        '"First" "Second". Venue.',
        '"Title"Venue.',
        '"An " inner " title," Journal, 1, 2-3.',
        '"The " My Title " Effect," Journal.',
        '"A title," in "Proceedings", 2024.',
    ],
)
def test_malformed_leading_quote_never_falls_through(remainder):
    raw = f"Smith, J. (2024). {remainder}"
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.raw_text == raw


def test_inline_quote_is_not_promoted_to_full_title():
    draft = parse_reference('Smith, J. (2024). A study of "quoted text". Example Journal.', 0)
    assert draft.title == 'A study of "quoted text"'


@pytest.mark.parametrize("tail", ["Journal, 2023.", "Journal, 2023. Accessed: 2025."])
def test_ieee_title_date_and_access_date_do_not_become_publication_year(tail):
    draft = parse_reference(f'J. Smith, "COVID (2020) recovery," {tail}', 0)
    assert draft.parse_status == CitationParseStatus.parsed
    assert draft.authors == ["Smith, J."]
    assert draft.title == "COVID (2020) recovery"
    assert draft.year == 2023


def test_conflicting_publication_years_remain_unassessed():
    draft = parse_reference('J. Smith, "A study," Journal, 2023, 2024.', 0)
    assert draft.parse_status == CitationParseStatus.parse_failed


def test_detached_projection_changes_only_new_run_view():
    extraction = source()
    citations = old_rows(extraction)
    before = copy.deepcopy(
        {key: value for key, value in vars(citations[0]).items() if key != "_sa_instance_state"}
    )
    source_before = extraction.model_dump_json()
    views, records = project_quoted_metadata(citations, extraction, load_patterns())
    assert citations[0].title != "A source title"
    assert views[0].title == "A source title"
    assert views[0].authors == citations[0].authors
    assert views[0].id == citations[0].id
    assert {
        key: value for key, value in vars(citations[0]).items() if key != "_sa_instance_state"
    } == before
    assert extraction.model_dump_json() == source_before
    record = records[0]
    assert record["status"] == "applied"
    assert record["original_fields"]["title"] == citations[0].title
    assert record["effective_fields"]["title"] == views[0].title
    assert len(record["parser_config_sha256"]) == len(record["raw_text_sha256"]) == 64
    assert RAW[record["title_start"] : record["title_end"]] == views[0].title


@pytest.mark.parametrize(
    "damage",
    [
        "year",
        "authors",
        "doi",
        "venue",
        "url",
        "raw_text",
        "missing",
        "extra",
        "order",
        "source",
    ],
)
async def test_projection_never_licenses_tampered_metadata_or_inventory(monkeypatch, damage):
    extraction = source()
    citations = old_rows(extraction)
    if damage == "missing":
        extraction.blocks.append(
            TextBlock(
                text="Cruz, J. (2020). Another study. Venue.",
                paragraph=3,
                max_font_size=12,
                bold_ratio=0,
            )
        )
    elif damage == "extra":
        citations.append(Citation(id=99, manuscript_id=1, **vars(parse_reference(RAW, 1))))
    elif damage == "order":
        citations[0].order_index = 8
    elif damage == "source":
        extraction.blocks[-1].text = RAW.replace("source", "unrelated")
    elif damage == "year":
        citations[0].year = 1999
    elif damage == "authors":
        citations[0].authors = ["Unrelated, J."]
    else:
        setattr(citations[0], damage, "Unrelated stored metadata")
    result, _, provider, support = await run(monkeypatch, extraction, citations)
    provider.assert_not_called()
    support.assert_not_called()
    assert result.detail["reference_metadata_coverage"]
    assert all(not row["lookup_attempted"] for row in result.detail["reference_metadata_coverage"])


async def test_service_saves_run_local_provenance_without_changing_citation(monkeypatch):
    extraction = source()
    citations = old_rows(extraction)
    old_title = citations[0].title
    result, _, provider, _ = await run(monkeypatch, extraction, citations)
    effective = provider.call_args.args[2]
    assert effective.title == "A source title"
    record = result.detail["reference_metadata_coverage"][0]["source_projection"]
    assert record["original_fields"]["title"] == old_title
    assert record["effective_fields"]["title"] == effective.title
    assert citations[0].title == old_title


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"quoted_title_pairs": [["A", "B"]]},
        {"quoted_title_pairs": [["”", "“"]]},
        {"quoted_title_terminal_delimiters": "e"},
        {"quoted_title_separators": "word"},
        {"standard_identifier_pattern": "["},
        {"standard_identifier_pattern": None},
    ],
)
async def test_invalid_parser_policy_does_not_call_provider_or_claim_pass(
    monkeypatch, tmp_path, bad
):
    extraction = source()
    citations = old_rows(extraction)
    policy = tmp_path / "invalid.json"
    policy.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setenv("REFERENCE_METADATA_PATTERNS_FILE", str(policy))
    from app.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ValueError):
        load_reference_metadata_patterns(policy)
    result, _, provider, support = await run(monkeypatch, extraction, citations)
    provider.assert_not_called()
    support.assert_not_called()
    assert result.detail["coverage_reason"] == "reference_metadata_policy_unavailable"
    assert (
        result.detail["reference_metadata_coverage"][0]["reason"]
        == "reference_metadata_policy_unavailable"
    )


@pytest.mark.parametrize("damage", ["reordered", "duplicate"])
async def test_entire_source_inventory_order_must_agree(monkeypatch, damage):
    extraction = source()
    extraction.blocks.append(
        TextBlock(
            text=RAW.replace("source title", "other title"),
            paragraph=3,
            max_font_size=12,
            bold_ratio=0,
        )
    )
    citations = old_rows(extraction)
    if damage == "reordered":
        citations.reverse()
    else:
        citations[1].order_index = citations[0].order_index
    result, _, provider, _ = await run(monkeypatch, extraction, citations)
    provider.assert_not_called()
    assert result.detail["coverage_reason"] == "reference_source_projection_unbound"


async def test_partial_source_repair_keeps_full_field_gate(monkeypatch):
    from tests.test_ingest_reference_scope import source as partial_source

    extraction = partial_source()
    extraction.blocks[-1].text = RAW
    citations = old_rows(extraction)
    original = citations[0].title
    result, _, provider, support = await run(monkeypatch, extraction, citations)
    provider.assert_awaited_once()
    support.assert_not_called()
    assert result.detail["assessment_scope"] == "reference_metadata_only"
    assert provider.call_args.args[2].title == "A source title"
    assert citations[0].title == original
    citations[0].year = 1999
    _, _, provider, support = await run(monkeypatch, extraction, citations)
    provider.assert_not_called()
    support.assert_not_called()


def test_divergent_caller_policy_is_not_misattributed(tmp_path):
    from app.config import Settings
    from app.ingest import reference_metadata_patterns

    extraction = source()
    citations = old_rows(extraction)
    packaged = (
        Path(reference_metadata_patterns.__file__).parent
        / "data"
        / "reference_metadata_patterns.json"
    )
    custom = json.loads(packaged.read_text(encoding="utf-8"))
    custom.update(version="custom-dot-only", quoted_title_terminal_delimiters=".")
    policy = tmp_path / "custom.json"
    policy.write_text(json.dumps(custom), encoding="utf-8")
    views, records = project_quoted_metadata(
        citations,
        extraction,
        load_patterns(),
        settings=Settings(reference_metadata_patterns_file=policy),
    )
    assert views[0].parse_status == CitationParseStatus.parse_failed
    assert records[0]["reason"] == "reference_metadata_policy_unavailable"
    assert "parser_config_sha256" not in records[0]


def test_invalid_policy_with_split_doi_still_never_throws(monkeypatch, tmp_path):
    from app.config import get_settings

    policy = tmp_path / "broken.json"
    policy.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("REFERENCE_METADATA_PATTERNS_FILE", str(policy))
    get_settings.cache_clear()
    raw = 'Smith, J. (2024). "Study," Journal. https://doi.org/10.1234/abc More.'
    draft = parse_reference(raw, 0)
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.raw_text == raw
