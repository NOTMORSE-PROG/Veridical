"""F1/F5: independently readable bibliography, with full provenance safeguards."""

import copy

import pytest

from app.ingest.patterns import load_patterns
from app.ingest.quality import (
    SOURCE_READING_ORDER_INGEST_NOTE,
    SOURCE_READING_ORDER_WITH_REFERENCES_NOTE,
    reading_order_ingest_note,
    reading_order_issues,
)
from app.ingest.references import (
    extract_references,
    reference_entry_locations,
    reference_reading_order_issues,
    select_references,
)
from app.ingest.schemas import (
    ExtractionResult,
    PageReadingOrder,
    SectionTree,
    SourceLineSpan,
    TextBlock,
)


def source(*, heading_page=2, entry_page=2, page_count=2):
    contents = [
        (1, "Unresolved chart"),
        (heading_page, "REFERENCES"),
        (entry_page, "Reyes, J. (2024). Example source. Journal of Examples."),
    ]
    blocks = [
        TextBlock(
            page=page,
            text=text,
            bbox=(50, 100 + index * 30, 500, 120 + index * 30),
            max_font_size=12,
            bold_ratio=1 if text == "REFERENCES" else 0,
            source_line_ids=[f"{page}:{index}:0"],
        )
        for index, (page, text) in enumerate(contents)
    ]
    return ExtractionResult(
        page_count=page_count,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(block.text) for block in blocks),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
        reading_order=[
            PageReadingOrder(
                page=page,
                status="unresolved" if page == 1 else "ordered",
                reason="table_regions_unresolved" if page == 1 else None,
                source_inventory_sha256="a" * 64,
                source_line_count=sum(block.page == page for block in blocks),
                ordered_source_line_ids=[
                    identifier
                    for block in blocks
                    if block.page == page
                    for identifier in block.source_line_ids
                ],
            )
            for page in range(1, page_count + 1)
        ],
    )


def test_unrelated_chart_does_not_discard_ordered_bibliography_or_location():
    extraction = source()
    before = copy.deepcopy(extraction.model_dump())
    patterns = load_patterns()
    drafts = extract_references(extraction, patterns)
    locations = reference_entry_locations(extraction, patterns)
    assert len(drafts) == len(locations) == 1
    assert drafts[0].title == "Example source"
    assert locations[0].page == 2
    assert locations[0].raw_text == drafts[0].raw_text
    assert locations[0].bboxes == (extraction.blocks[-1].bbox,)
    assert reading_order_issues(extraction) == [{"page": 1, "reason": "table_regions_unresolved"}]
    assert extraction.model_dump() == before


@pytest.mark.parametrize("failure_page", [2, 3, 4])
def test_unresolved_heading_intervening_or_entry_page_stays_blocked(failure_page):
    extraction = source(heading_page=2, entry_page=4, page_count=4)
    extraction.reading_order[failure_page - 1].status = "unresolved"
    extraction.reading_order[failure_page - 1].reason = "overlapping_text"
    patterns = load_patterns()
    assert extract_references(extraction, patterns) == []
    assert reference_entry_locations(extraction, patterns) == []


@pytest.mark.parametrize(
    "corruption", ["record_missing", "ids_missing", "ids_mismatch", "hash_missing"]
)
def test_page_scope_never_hides_unrelated_provenance_damage(corruption):
    extraction = source()
    if corruption == "record_missing":
        extraction.reading_order.pop(0)
    elif corruption == "ids_missing":
        extraction.blocks[0].source_line_ids = None
    elif corruption == "ids_mismatch":
        extraction.reading_order[0].ordered_source_line_ids = ["different-source"]
    else:
        extraction.reading_order[0].status = "ordered"
        extraction.reading_order[0].reason = None
        extraction.reading_order[0].source_inventory_sha256 = None
    patterns = load_patterns()
    assert reading_order_issues(extraction, pages={2})
    assert extract_references(extraction, patterns) == []
    assert reference_entry_locations(extraction, patterns) == []


def test_no_reference_selection_does_not_claim_complete_body_coverage():
    extraction = source()
    extraction.blocks[1].text = "Discussion"
    patterns = load_patterns()
    selection = select_references(extraction, patterns)
    assert selection.span == []
    assert reference_reading_order_issues(extraction, selection) == reading_order_issues(extraction)


def test_serialized_reload_retains_scope_and_document_limitations():
    extraction = ExtractionResult.model_validate_json(source().model_dump_json())
    assert len(extract_references(extraction, load_patterns())) == 1
    assert reading_order_issues(extraction)


@pytest.mark.parametrize("position", ["prefix", "suffix"])
def test_stripped_page_furniture_is_not_a_supported_bibliography_boundary(position):
    extraction = source(heading_page=1 if position == "prefix" else 2, page_count=3)
    block = extraction.blocks[-1]
    parts = ["2", block.text] if position == "prefix" else [block.text, "2"]
    block.text = "\n".join(parts)
    block.bbox = (50, 100, 500, 160)
    offset = 0
    block.source_lines = []
    for index, text in enumerate(parts):
        block.source_lines.append(
            SourceLineSpan(
                start=offset,
                end=offset + len(text),
                bbox=(50, 100 + index * 30, 500, 120 + index * 30),
                max_font_size=12,
                bold_ratio=0,
                is_furniture=text == "2",
                source_id=f"2:2:{index}",
            )
        )
        offset += len(text) + 1
    block.source_line_ids = [line.source_id for line in block.source_lines]
    extraction.reading_order[1].ordered_source_line_ids = [
        identifier
        for item in extraction.blocks
        if item.page == 2
        for identifier in item.source_line_ids
    ]
    extraction.reading_order[1].source_line_count = len(
        extraction.reading_order[1].ordered_source_line_ids
    )
    if position == "suffix":
        extraction.reading_order[2].status = "unresolved"
        extraction.reading_order[2].reason = "line_budget_exhausted"
        extraction.reading_order[2].source_inventory_sha256 = None
    selection = select_references(extraction, load_patterns())
    assert not selection.issues
    assert reference_reading_order_issues(extraction, selection)
    assert extract_references(extraction, load_patterns()) == []
    assert reference_entry_locations(extraction, load_patterns()) == []


@pytest.mark.parametrize("furniture_only", [False, True])
def test_unreadable_tail_cannot_hide_a_continued_bibliography(furniture_only):
    extraction = source(page_count=3)
    if furniture_only:
        extraction.blocks.append(
            TextBlock(
                page=3,
                text="3",
                is_furniture=True,
                max_font_size=10,
                bold_ratio=0,
                source_line_ids=["3:3:0"],
            )
        )
        extraction.reading_order[2].ordered_source_line_ids = ["3:3:0"]
        extraction.reading_order[2].source_line_count = 1
    extraction.reading_order[2].status = "unresolved"
    extraction.reading_order[2].reason = "line_budget_exhausted"
    extraction.reading_order[2].source_inventory_sha256 = None
    assert extract_references(extraction, load_patterns()) == []
    assert reference_entry_locations(extraction, load_patterns()) == []


def test_explicit_readable_closing_heading_bounds_bibliography_before_unsupported_tail():
    extraction = source(page_count=3)
    extraction.blocks.append(
        TextBlock(
            page=2,
            text="Appendix",
            max_font_size=16,
            bold_ratio=1,
            bbox=(50, 300, 500, 320),
            source_line_ids=["2:3:0"],
        )
    )
    extraction.reading_order[1].ordered_source_line_ids.append("2:3:0")
    extraction.reading_order[1].source_line_count += 1
    extraction.reading_order[2].status = "unresolved"
    extraction.reading_order[2].reason = "line_budget_exhausted"
    extraction.reading_order[2].source_inventory_sha256 = None
    assert len(extract_references(extraction, load_patterns())) == 1
    assert len(reading_order_issues(extraction)) == 2


@pytest.mark.parametrize("reference_count", [1, 11])
def test_upload_note_does_not_claim_retained_references_were_discarded(reference_count):
    assert (
        reading_order_ingest_note(source(), reference_count)
        == SOURCE_READING_ORDER_WITH_REFERENCES_NOTE
    )
    assert "Extraction does not verify" in SOURCE_READING_ORDER_WITH_REFERENCES_NOTE


def test_no_extracted_references_does_not_mean_none_exist():
    assert reading_order_ingest_note(source(), 0) == SOURCE_READING_ORDER_INGEST_NOTE


@pytest.mark.parametrize("reference_count", [0, 1])
def test_fully_ordered_source_has_no_reading_warning(reference_count):
    extraction = source()
    extraction.reading_order[0].status = "ordered"
    extraction.reading_order[0].reason = None
    assert reading_order_ingest_note(extraction, reference_count) is None


@pytest.mark.parametrize("pages", [set(), {0}, {3}, {1, 3}])
def test_invalid_requested_scope_is_not_success(pages):
    assert reading_order_issues(source(), pages=pages) == [
        {"reason": "reading_order_scope_invalid"}
    ]
