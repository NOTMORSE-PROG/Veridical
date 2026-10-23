"""Pure reference measurement over canonical source and retained rows (F3)."""

from app.checks.rules import CitationLike, ReferenceInventory
from app.ingest.patterns import HeadingPatterns
from app.ingest.reference_inventory import (
    reference_row_binding_issues,
    reference_source_scope_issues,
)
from app.ingest.references import (
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
    scope_issues = reference_source_scope_issues(extraction, selection)
    if scope_issues:
        status = (
            "unknown"
            if scope_issues[0]
            in {
                "reference_heading_unavailable",
                "reference_source_positions_unavailable",
                "reference_scope_boundary_unavailable",
            }
            else "partial"
        )
        return limited(status, *scope_issues)
    binding_issues = reference_row_binding_issues(selection.groups, citations)
    if binding_issues:
        return limited("stale", *binding_issues)
    return ReferenceInventory(
        status="complete",
        count=len(citations),
        anchor=anchor,
        selected_group_count=len(selection.groups),
        retained_row_count=len(citations),
    )
