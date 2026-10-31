"""Shared source membership and rich-content scope; no grading or metadata inference."""

from collections.abc import Sequence
from typing import Protocol

from app.ingest.analytical_source import analytical_source
from app.ingest.normalize import normalize
from app.ingest.references import ReferenceSelection, _has_body_text
from app.ingest.schemas import ExtractionResult, TextBlock
from app.ingest.source_slices import SourceFragment


class ReferenceRowLike(Protocol):
    order_index: int
    raw_text: str


def reference_row_binding_issues(
    groups: Sequence[Sequence[TextBlock]], citations: Sequence[ReferenceRowLike]
) -> tuple[str, ...]:
    """Membership only: callers must establish metadata authority separately."""
    if len(groups) != len(citations):
        return ("reference_inventory_cardinality_mismatch",)
    for index, (group, citation) in enumerate(zip(groups, citations, strict=True)):
        if type(citation.order_index) is not int or citation.order_index != index:
            return ("reference_inventory_order_mismatch",)
        if normalize(citation.raw_text) != normalize("\n".join(block.text for block in group)):
            return ("reference_inventory_text_mismatch",)
    return ()


def reference_source_scope_issues(
    extraction: ExtractionResult, selection: ReferenceSelection, *, require_complete: bool = True
) -> tuple[str, ...]:
    """Withhold completeness when selected text may omit bibliography content.

    This does not establish reading-order authority; consumers apply their own
    existing source-quality and legacy-compatibility policy.
    """
    if require_complete and selection.membership_issues:
        return selection.membership_issues
    heading = selection.heading
    if not isinstance(heading, SourceFragment):
        return ("reference_heading_unavailable",)
    fragments = [heading, *selection.span]
    if not all(isinstance(fragment, SourceFragment) for fragment in fragments):
        return ("reference_source_positions_unavailable",)
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
            return ("reference_source_positions_unavailable",)
        start, end = heading.paragraph, closing.paragraph if closing is not None else None
        if start is None or (end is not None and end <= endpoint.paragraph):
            return ("reference_scope_boundary_unavailable",)
        coordinate = "paragraph"
    else:
        start, end = heading.page, closing.page if closing is not None else extraction.page_count
        if start is None or end is None or not 1 <= start <= end <= extraction.page_count:
            return ("reference_scope_boundary_unavailable",)
        coordinate = "page"
    if extraction.docx_omitted_image_occurrences:
        return ("reference_rich_content_unlocated",)
    native = analytical_source(extraction)
    if native.state in {"not_recorded", "unavailable"} or native.unlocated_occurrences:
        return ("reference_native_math_scope_unavailable",)
    if native.inventory is not None:
        for container in native.inventory.containers:
            position = container.paragraph
            if position is not None and position >= start and (end is None or position < end):
                return ("reference_native_math_unassessed",)
    for item in [*extraction.images, *extraction.tables, *extraction.equations]:
        position = getattr(item, coordinate)
        if position is None:
            return ("reference_rich_content_unlocated",)
        if position < 0 or (coordinate == "page" and not 1 <= position <= extraction.page_count):
            return ("reference_rich_content_unlocated",)
        before_end = end is None or position < end if coordinate == "paragraph" else position <= end
        inside = position >= start and before_end
        if inside and coordinate == "page" and _below_closing(item, closing, end):
            # The list's last page can also start the next section. An image
            # set wholly below that section's heading, in its column, follows
            # the list and cannot hold any of its entries.
            inside = False
        if inside:
            return ("reference_rich_content_unassessed",)
    return ()


def _below_closing(item, closing: TextBlock | None, end_page: int) -> bool:
    box = getattr(item, "bbox", None)
    if closing is None or closing.bbox is None or box is None or item.page != end_page:
        return False
    if closing.page != end_page:
        return False
    left, _top, right, bottom = closing.bbox
    return box[1] >= bottom and box[0] < right and left < box[2]
