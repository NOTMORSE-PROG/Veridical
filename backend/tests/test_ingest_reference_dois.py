"""Source-bound DOI wrap controls, not provider/existence or accuracy labels."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.checks.citations import verify
from app.config import Settings
from app.external.schemas import VerificationResult
from app.ingest.patterns import load_patterns
from app.ingest.reference_dois import _printed_span, _proxy_doi
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns
from app.ingest.references import extract_references, parse_reference, reference_entry_locations
from app.ingest.schemas import ExtractionResult, SectionTree, SourceLineSpan, TextBlock, UriLink
from app.ingest.source_slices import line_inventory_valid
from app.models.citation import Citation
from app.models.enums import CitationParseStatus, ResultOutcome
from tests.test_citations_partial_source import run


def wrapped_source(*, separate_blocks=False):
    lines = [
        "Reyes, J. (2024). A useful tool. Example Journal. doi:10.1234/archive.",
        "123456",
    ]
    offsets = [0, len(lines[0]) + 1]
    spans = [
        SourceLineSpan(
            start=offset,
            end=offset + len(line),
            bbox=(
                10 if index == 0 else 25,
                20 + index * 15,
                300 if index == 0 else 55,
                30 + index * 15,
            ),
            max_font_size=10,
            bold_ratio=0,
            source_id=f"1:1:{index}",
        )
        for index, (line, offset) in enumerate(zip(lines, offsets, strict=True))
    ]
    block = TextBlock(
        text="\n".join(lines),
        page=1,
        bbox=(10, 20, 300, 45),
        source_lines=spans,
        source_line_ids=[line.source_id for line in spans],
        max_font_size=10,
        bold_ratio=0,
    )
    blocks = [block]
    if separate_blocks:
        blocks = [
            TextBlock(
                text=line,
                page=1,
                bbox=span.bbox,
                source_lines=[span.model_copy(update={"start": 0, "end": len(line)})],
                source_line_ids=[span.source_id],
                max_font_size=10,
                bold_ratio=0,
            )
            for line, span in zip(lines, spans, strict=True)
        ]
    return ExtractionResult(
        anchor_kind="page",
        page_count=1,
        image_only=False,
        text_chars=sum(map(len, lines)),
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[
            TextBlock(
                text="References",
                page=1,
                bbox=(10, 0, 100, 10),
                max_font_size=16,
                bold_ratio=1,
            ),
            *blocks,
        ],
        images=[],
        uri_links_complete=True,
        uri_links=[
            UriLink(
                page=1,
                bbox=(210, 17, 300, 31),
                uri="https://doi.org/10.1234/archive.123456",
                text="10.1234/archive.",
            ),
            UriLink(
                page=1,
                bbox=(25, 32, 55, 46),
                uri="https://doi.org/10.1234/archive.123456",
                text="clipped neighbouring letters\n123456",
            ),
        ],
    )


@pytest.mark.parametrize("separate_blocks", [False, True])
def test_wrapped_doi_uses_exact_source_and_annotations_without_rewriting_evidence(separate_blocks):
    source = wrapped_source(separate_blocks=separate_blocks)
    before = source.model_dump_json()
    drafts = extract_references(source, load_patterns())
    assert len(drafts) == 1
    assert drafts[0].doi == "10.1234/archive.123456"
    assert drafts[0].parse_status == CitationParseStatus.parsed
    assert drafts[0].title == "A useful tool"
    assert drafts[0].venue == "Example Journal"
    assert "archive. 123456" in drafts[0].raw_text
    assert drafts[0].raw_text == reference_entry_locations(source, load_patterns())[0].raw_text
    assert source.model_dump_json() == before
    reloaded = ExtractionResult.model_validate_json(before)
    assert reloaded.model_dump_json() == before
    assert extract_references(reloaded, load_patterns()) == drafts


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_link",
        "incomplete_links",
        "missing_lines",
        "mismatched_destination",
        "mismatched_fragment",
        "wrong_page",
        "competing_line",
        "same_line_space",
        "longer_source_identifier",
        "extra_newline_tail",
        "unsafe_uri",
        "userinfo",
        "query",
        "unapproved_resolver",
    ],
)
def test_unproved_wrap_never_retains_a_lookupable_prefix(mutation):
    source = wrapped_source()
    if mutation == "missing_link":
        source.uri_links.pop()
    elif mutation == "incomplete_links":
        source.uri_links_complete = False
    elif mutation == "missing_lines":
        source.blocks[1].source_lines = source.blocks[1].source_line_ids = None
    elif mutation == "mismatched_destination":
        source.uri_links[1].uri = "https://doi.org/10.1234/archive.654321"
    elif mutation == "mismatched_fragment":
        source.uri_links[1].text = "654321"
    elif mutation == "wrong_page":
        source.uri_links[1].page = 2
    elif mutation == "competing_line":
        other = source.blocks[1].model_copy(deep=True)
        other.text = source.blocks[1].text.replace("Reyes", "Santo")
        for index, span in enumerate(other.source_lines):
            span.source_id = f"1:99:{index}"
        other.source_line_ids = [span.source_id for span in other.source_lines]
        assert line_inventory_valid(other)
        source.blocks.append(other)
    elif mutation == "same_line_space":
        source.blocks[1].text = source.blocks[1].text.replace("\n", " ")
        source.blocks[1].source_lines = source.blocks[1].source_line_ids = None
    elif mutation == "longer_source_identifier":
        block = source.blocks[1]
        block.text += "_extra"
        block.source_lines[1].end += len("_extra")
        block.source_lines[1].bbox = (25, 35, 85, 45)
        assert line_inventory_valid(block)
    elif mutation == "extra_newline_tail":
        block = source.blocks[1]
        start = len(block.text) + 1
        block.text += "\n_extra"
        block.bbox = (10, 20, 300, 60)
        block.source_lines.append(
            SourceLineSpan(
                start=start,
                end=len(block.text),
                bbox=(25, 50, 55, 60),
                max_font_size=10,
                bold_ratio=0,
                source_id="1:1:2",
            )
        )
        block.source_line_ids.append("1:1:2")
        assert line_inventory_valid(block)
    else:
        target = {
            "unsafe_uri": "javascript:10.1234/archive.123456",
            "userinfo": "https://secret@doi.org/10.1234/archive.123456",
            "query": "https://doi.org/10.1234/archive.123456?different=1",
            "unapproved_resolver": "https://other.example/10.1234/archive.123456",
        }[mutation]
        for link in source.uri_links:
            link.uri = target
    drafts = extract_references(source, load_patterns())
    assert drafts
    assert all(draft.doi is None for draft in drafts)


def test_complete_unannotated_doi_still_parses():
    draft = parse_reference(
        "Reyes, J. (2024). A useful tool. Example Journal. doi:10.1234/archive.123456", 0
    )
    assert draft.doi == "10.1234/archive.123456"
    assert draft.parse_status == CitationParseStatus.parsed


def test_complete_doi_followed_by_separately_printed_url_is_not_a_wrap():
    draft = parse_reference(
        "Reyes, J. (2024). A useful tool. doi:10.1234/archive.123456. https://example.org/article",
        0,
    )
    assert draft.doi == "10.1234/archive.123456"


def test_flattened_legacy_doi_prefix_is_not_a_complete_identifier():
    draft = parse_reference(
        "Reyes, J. (2024). A useful tool. Example Journal. doi:10.1234/archive. 123456", 0
    )
    assert draft.doi is None


@pytest.mark.parametrize("tail", ["_extra", "\n_extra", "other", "\nother"])
def test_printed_span_cannot_recover_a_prefix_of_a_longer_identifier(tail):
    assert _printed_span("doi:10.1234/archive.\n123456" + tail, "10.1234/archive.123456") is None


def test_printed_span_never_collapses_intraline_spaces_or_unicode_case():
    assert _printed_span("doi:10.1234/archive. 123456", "10.1234/archive.123456") is None
    assert _printed_span("doi:10.1234/straße.\n123456", "10.1234/STRASSE.123456") is None
    assert _printed_span("doi:10.1234/Ä.\n123456", "10.1234/ä.123456") is None
    assert _printed_span("doi:10.1234/ARCHIVE.\n123456", "10.1234/archive.123456").doi == (
        "10.1234/ARCHIVE.123456"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "https://doi.org.evil.test/10.1234/archive.123456",
        "https://doi.org:443/10.1234/archive.123456",
        "https://doi.org/10.1234/archive.123456#fragment",
        "https://doi.org/10.1234/archive.%31%32%33%34%35%36",
        "https://doi.org/10.1234/archive.123456\n",
        "https://doi.org\\@evil.test/10.1234/archive.123456",
        "https://doi.org/10.1234/a/../b",
        "https://doi.org/10.1234/./b",
    ],
)
def test_proxy_rejects_unsupported_or_deceptive_forms(uri):
    assert _proxy_doi(uri, frozenset({"doi.org", "dx.doi.org"})) is None


def test_legacy_resolver_and_ascii_case_recover_only_printed_spelling():
    source = wrapped_source()
    for link in source.uri_links:
        link.uri = "http://dx.doi.org/10.1234/ARCHIVE.123456"
    assert extract_references(source, load_patterns())[0].doi == "10.1234/archive.123456"


@pytest.mark.parametrize("suffix", [".", ")", ";", ":"])
def test_corroborated_identifier_terminal_punctuation_is_not_trimmed(suffix):
    source = wrapped_source()
    block = source.blocks[1]
    block.text += suffix
    block.source_lines[-1].end += len(suffix)
    block.source_lines[-1].bbox = (25, 35, 60, 45)
    assert line_inventory_valid(block)
    for link in source.uri_links:
        link.uri += suffix
    source.uri_links[-1].text += suffix
    source.uri_links[-1].bbox = (25, 32, 60, 46)
    assert extract_references(source, load_patterns())[0].doi == "10.1234/archive.123456" + suffix


def test_annotation_only_identifier_is_not_printed_evidence():
    source = wrapped_source()
    block = source.blocks[1]
    block.text = block.text.replace("10.1234/archive.", "Crossref".ljust(len("10.1234/archive.")))
    assert line_inventory_valid(block)
    assert extract_references(source, load_patterns())[0].doi is None


def legacy_citation(tail="123456"):
    return Citation(
        manuscript_id=1,
        order_index=0,
        parse_status=CitationParseStatus.parsed,
        raw_text=f"Reyes, J. (2024). A useful tool. doi:10.1234/archive. {tail}",
        authors=["Reyes, J."],
        title="A useful tool",
        year=2024,
        doi="10.1234/archive",
    )


@pytest.mark.parametrize("found", [True, False])
@pytest.mark.parametrize("tail", ["123456", "(123456)", ">123456", "?123456", "-/123456"])
async def test_stored_prefix_cannot_reach_existence_or_abstract_lookup(monkeypatch, found, tail):
    provider = AsyncMock(
        return_value=verify.CitationVerdict(
            verify.VerdictKind.existence_confirmed if found else verify.VerdictKind.not_found,
            VerificationResult(found=found, provider="crossref"),
        )
    )
    abstract = AsyncMock()
    monkeypatch.setattr(verify, "_verify_keyed", provider)
    monkeypatch.setattr(verify, "_fetch_supplementary_abstract", abstract)
    citation = legacy_citation(tail)
    verdict = await verify.verify_citation(None, None, citation, settings=Settings(_env_file=None))
    assert verdict.kind == verify.VerdictKind.unassessed
    assert verdict.result is None
    assert verify._verdict_flag_draft(citation, verdict) is None
    provider.assert_not_called()
    abstract.assert_not_called()


async def test_stored_prefix_assembly_records_unassessed_without_title_fallback(monkeypatch):
    source = wrapped_source()
    result, rows, provider, support = await run(monkeypatch, source, [legacy_citation()])
    provider.assert_not_called()
    support.assert_not_called()
    assert result.outcome == ResultOutcome.escalated
    assert result.detail["n_metadata_skipped_insufficient"] == 1
    # Whole-source admission now precedes the narrower per-identifier guard.
    # This legacy row also lacks the source's venue, so it is not the same entry.
    assert result.detail["reference_inventory_issues"] == ["reference_inventory_text_mismatch"]
    assert result.detail["claim_support_inventory_status"] == "not_captured"
    assert result.detail["n_flags"] == 0
    assert result.detail["reference_metadata_coverage"] == [
        {
            "reference_order_index": 0,
            "status": "unassessed",
            "reason": "reference_source_projection_unbound",
            "lookup_attempted": False,
            "metadata_complete": False,
            "source_projection": None,
        }
    ]


def test_partial_parser_exception_cannot_leave_lookupable_prefix(monkeypatch):
    from app.ingest import references

    def broken_parse(draft, raw):
        draft.doi = "10.1234/archive"
        raise ValueError("unsupported remaining metadata")

    monkeypatch.setattr(references, "_parse_into", broken_parse)
    draft = parse_reference("Reyes. doi:10.1234/archive. 123456", 0)
    assert draft.parse_status == CitationParseStatus.parse_failed
    assert draft.doi is None


@pytest.mark.parametrize("hosts", [None, [], ["doi.org"], ["DOI.ORG", "dx.doi.org"]])
def test_configured_resolver_hosts_remain_backwards_compatible(tmp_path, hosts):
    from app.ingest import references

    path = Path(references.__file__).parent / "data" / "reference_metadata_patterns.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if hosts is None:
        raw.pop("doi_resolver_hosts")
    else:
        raw["doi_resolver_hosts"] = hosts
    override = tmp_path / "patterns.json"
    override.write_text(json.dumps(raw), encoding="utf-8")
    expected = {"doi.org", "dx.doi.org"} if hosts is None else {host.lower() for host in hosts}
    assert load_reference_metadata_patterns(override).doi_resolver_hosts == expected


@pytest.mark.parametrize(
    "hosts", [["https://doi.org"], ["user@doi.org"], ["doi.org:80"], "doi.org"]
)
def test_invalid_resolver_configuration_fails_explicitly(tmp_path, hosts):
    from app.ingest import references

    path = Path(references.__file__).parent / "data" / "reference_metadata_patterns.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["doi_resolver_hosts"] = hosts
    override = tmp_path / "patterns.json"
    override.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="plain host"):
        load_reference_metadata_patterns(override)


def test_geometry_budget_abstains_before_any_ownership_work(monkeypatch):
    from app.ingest import reference_dois, references

    settings = Settings(_env_file=None, reference_doi_max_geometry_comparisons=1)
    monkeypatch.setattr(references, "get_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(reference_dois, "_owns", lambda *args: calls.append(args))
    assert extract_references(wrapped_source(), load_patterns())[0].doi is None
    assert calls == []


def test_unrelated_page_links_do_not_consume_ownership_budget(monkeypatch):
    from app.ingest import references

    source = wrapped_source()
    source.blocks.insert(
        0,
        TextBlock(
            text="Unrelated body",
            page=2,
            bbox=(10, 20, 300, 45),
            max_font_size=10,
            bold_ratio=0,
        ),
    )
    source.uri_links.extend(
        [
            UriLink(page=2, bbox=(25, 32, 55, 46), uri="https://example.test/", text="body")
            for _ in range(20)
        ]
    )
    settings = Settings(_env_file=None, reference_doi_max_geometry_comparisons=6)
    monkeypatch.setattr(references, "get_settings", lambda: settings)
    assert extract_references(source, load_patterns())[0].doi == "10.1234/archive.123456"
