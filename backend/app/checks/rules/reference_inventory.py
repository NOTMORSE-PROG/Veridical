"""Pure reference measurement over canonical source and retained rows (F3)."""

from app.checks.rules import CitationLike, ReferenceInventory
from app.ingest.normalize import normalize
from app.ingest.patterns import HeadingPatterns
from app.ingest.references import (
    _has_body_text,
    reference_reading_order_issues,
    select_references,
)
from app.ingest.schemas import ExtractionResult
from app.ingest.source_slices import SourceFragment


def measure_reference_inventory(
    extraction: ExtractionResult, citations: list[CitationLike], patterns: HeadingPatterns
) -> ReferenceInventory:
    """Do not infer completeness from a parser's retained entry count alone."""
    selection = select_references(extraction, patterns)
    heading = selection.heading
    anchor = (
        f"paragraph {heading.paragraph}"
        if heading is not None and heading.paragraph is not None
        else f"page {heading.page}"
        if heading is not None and heading.page is not None
        else "reference list"
    )

    def limited(status, *reasons):
        return ReferenceInventory(
            status=status,
            count=None,
            reasons=tuple(reasons),
            anchor=anchor,
            selected_group_count=len(selection.groups),
            retained_row_count=len(citations),
        )

    if selection.issues:
        return limited("partial", *selection.issues)
    if not isinstance(heading, SourceFragment):
        return limited("unknown", "reference_heading_unavailable")
    if extraction.anchor_kind == "page" and extraction.reading_order is None:
        return limited("unknown", "reference_order_metadata_unavailable")
    if reference_reading_order_issues(extraction, selection):
        return limited("partial", "reference_source_reading_unresolved")
    fragments = [heading, *selection.span]
    if not all(isinstance(fragment, SourceFragment) for fragment in fragments):
        return limited("unknown", "reference_source_positions_unavailable")
    endpoint = max(fragments, key=lambda item: (item.source_block_index, item.source_end))
    # The selector already established membership. Its first subsequent body
    # interval is the closing boundary; EOF includes rich content after the
    # last recovered text. Furniture cannot establish a closing boundary.
    closing = next(
        (
            block
            for index, block in enumerate(extraction.blocks)
            if index >= endpoint.source_block_index
            and not block.is_furniture
            and _has_body_text(
                block,
                endpoint.source_end if index == endpoint.source_block_index else 0,
                len(block.text),
            )
        ),
        None,
    )
    if extraction.anchor_kind == "paragraph":
        positions = [block.paragraph for block in extraction.blocks if not block.is_furniture]
        if any(position is None or position < 0 for position in positions) or positions != sorted(
            set(positions)
        ):
            return limited("unknown", "reference_source_positions_unavailable")
        start, end = heading.paragraph, closing.paragraph if closing is not None else None
        if start is None or (end is not None and end <= endpoint.paragraph):
            return limited("unknown", "reference_scope_boundary_unavailable")
        coordinate = "paragraph"
    else:
        start, end = heading.page, closing.page if closing is not None else extraction.page_count
        if start is None or end is None or not 1 <= start <= end <= extraction.page_count:
            return limited("unknown", "reference_scope_boundary_unavailable")
        coordinate = "page"
    if extraction.docx_omitted_image_occurrences:
        return limited("partial", "reference_rich_content_unlocated")
    for item in [*extraction.images, *extraction.tables, *extraction.equations]:
        position = getattr(item, coordinate)
        if position is None:
            return limited("partial", "reference_rich_content_unlocated")
        if position < 0 or (coordinate == "page" and not 1 <= position <= extraction.page_count):
            return limited("partial", "reference_rich_content_unlocated")
        before_end = end is None or position < end if coordinate == "paragraph" else position <= end
        inside = position >= start and before_end
        if inside:
            return limited("partial", "reference_rich_content_unassessed")
    if len(selection.groups) != len(citations):
        return limited("stale", "reference_inventory_cardinality_mismatch")
    for index, (group, citation) in enumerate(zip(selection.groups, citations, strict=True)):
        if type(citation.order_index) is not int or citation.order_index != index:
            return limited("stale", "reference_inventory_order_mismatch")
        if normalize(citation.raw_text) != normalize("\n".join(block.text for block in group)):
            return limited("stale", "reference_inventory_text_mismatch")
    return ReferenceInventory(
        status="complete",
        count=len(citations),
        anchor=anchor,
        selected_group_count=len(selection.groups),
        retained_row_count=len(citations),
    )
