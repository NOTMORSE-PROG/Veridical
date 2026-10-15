"""Persisted source-quality gates shared by ingestion and check services."""

from app.ingest.schemas import ExtractionResult

SOURCE_READING_ORDER_UNRESOLVED = "source_reading_order_unresolved"
SOURCE_READING_ORDER_NOTE = (
    "VERIDICAL could not reliably read the text in order on some pages. "
    "Order-dependent manuscript checks were not assessed. "
    "This is a reading limitation, not a finding against the manuscript."
)
SOURCE_READING_ORDER_INGEST_NOTE = (
    "References were not extracted because VERIDICAL could not read some pages "
    "in a reliable order; this does not mean the paper has no references."
)
SOURCE_REFERENCE_BOUNDARY_INGEST_NOTE = (
    "References were not extracted because VERIDICAL could not reliably separate the entries. "
    "This does not mean the paper has no references. You can still open the original paper."
)


def reading_order_issues(extraction: ExtractionResult) -> list[dict[str, int | str]]:
    """Legacy absence is unknown, not a newly asserted order result.

    New assessments must cover every PDF page and conserve their source IDs.
    Switching the current feature setting off cannot erase a persisted issue.
    This first gate is document-wide; page-local check coverage is not inferred.
    """
    records = extraction.reading_order
    if records is None:
        return []
    if extraction.anchor_kind != "page":
        return [{"reason": "reading_order_metadata_inconsistent"}]
    pages = [record.page for record in records]
    if sorted(pages) != list(range(1, extraction.page_count + 1)):
        return [{"reason": "reading_order_metadata_incomplete"}]
    by_page: dict[int, list[str]] = {}
    for block in extraction.blocks:
        if block.page not in pages or block.source_line_ids is None:
            return [{"reason": "reading_order_source_identity_missing"}]
        if block.text.strip() and not block.source_line_ids:
            return [{"page": block.page, "reason": "reading_order_source_identity_missing"}]
        by_page.setdefault(block.page, []).extend(block.source_line_ids)
    issues = []
    for record in records:
        ids = record.ordered_source_line_ids
        if record.status == "ordered" and (record.reason or not record.source_inventory_sha256):
            issues.append({"page": record.page, "reason": "reading_order_metadata_inconsistent"})
            continue
        if (
            ids != by_page.get(record.page, [])
            or len(ids) != record.source_line_count
            or len(set(ids)) != len(ids)
        ):
            issues.append({"page": record.page, "reason": "reading_order_source_identity_mismatch"})
        elif record.status == "unresolved":
            issues.append(
                {"page": record.page, "reason": record.reason or "reading_order_unresolved"}
            )
    return issues
