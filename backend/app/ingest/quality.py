"""Persisted source-quality gates shared by ingestion and check services."""

from app.ingest.native_table_order import saved_table_witness_valid
from app.ingest.schemas import ExtractionResult
from app.ingest.source_slices import line_inventory_valid

SOURCE_READING_ORDER_UNRESOLVED = "source_reading_order_unresolved"
SOURCE_READING_ORDER_NOTE = (
    "VERIDICAL could not reliably read the text in order on some pages. "
    "Order-dependent manuscript checks were not assessed. "
    "This is a reading limitation, not a finding against the manuscript."
)
SOURCE_READING_ORDER_INGEST_NOTE = (
    "Some pages could not be read in a reliable order. No references were extracted. "
    "This does not mean the paper has no references."
)
SOURCE_READING_ORDER_WITH_REFERENCES_NOTE = (
    "Some pages could not be read in a reliable order. "
    "Reference entries were retained from a readable section. "
    "Extraction does not verify their sources."
)
SOURCE_REFERENCE_BOUNDARY_INGEST_NOTE = (
    "References were not extracted because VERIDICAL could not reliably separate the entries. "
    "This does not mean the paper has no references. You can still open the original paper."
)
SOURCE_REFERENCE_PARTIAL_INGEST_NOTE = (
    "Entries from the primary reference list were extracted. Later bibliography-like text "
    "could not be safely classified and was kept outside body-text comparisons. "
    "Reference coverage is partial; extraction does not verify a source."
)


def reading_order_ingest_note(extraction: ExtractionResult, reference_count: int) -> str | None:
    """Report retained entries separately from source readability/verification."""
    if not reading_order_issues(extraction):
        return None
    return (
        SOURCE_READING_ORDER_WITH_REFERENCES_NOTE
        if reference_count > 0
        else SOURCE_READING_ORDER_INGEST_NOTE
    )


def reading_order_issues(
    extraction: ExtractionResult, *, pages: set[int] | None = None
) -> list[dict[str, int | str]]:
    """Legacy absence is unknown, not a newly asserted order result.

    New assessments must cover every PDF page and conserve their source IDs.
    Switching the current feature setting off cannot erase a persisted issue.
    A caller may name the complete page scope of a bounded operation. Layout
    uncertainty outside that scope does not invalidate its independently ordered
    source. Metadata and source-identity validation still covers the ENTIRE
    extraction; a scope can never hide damaged provenance or missing records.
    The default remains document-wide, and this never mutates the source ledger.
    """
    for block in extraction.blocks:
        if not line_inventory_valid(block):
            issue: dict[str, int | str] = {"reason": "invalid_source_line_inventory"}
            if block.page is not None:
                issue["page"] = block.page
            return [issue]
    records = extraction.reading_order
    if records is None:
        return []
    if extraction.anchor_kind != "page":
        return [{"reason": "reading_order_metadata_inconsistent"}]
    recorded_pages = [record.page for record in records]
    if sorted(recorded_pages) != list(range(1, extraction.page_count + 1)):
        return [{"reason": "reading_order_metadata_incomplete"}]
    if pages is not None and (not pages or not pages.issubset(recorded_pages)):
        return [{"reason": "reading_order_scope_invalid"}]
    by_page: dict[int, list[str]] = {}
    for block in extraction.blocks:
        if block.page not in recorded_pages or block.source_line_ids is None:
            return [{"reason": "reading_order_source_identity_missing"}]
        if block.text.strip() and not block.source_line_ids:
            return [{"page": block.page, "reason": "reading_order_source_identity_missing"}]
        by_page.setdefault(block.page, []).extend(block.source_line_ids)
    issues = []
    unread = set(extraction.unread_pages or ())
    for record in records:
        if record.page in unread:
            # Left out of this analysis copy on purpose; only a page whose
            # order is unresolved may be, and a caller that names it in its
            # own scope still sees it as unread.
            if record.status != "unresolved":
                issues.append({"page": record.page, "reason": "reading_order_scope_invalid"})
            elif pages is not None and record.page in pages:
                issues.append(
                    {"page": record.page, "reason": record.reason or "reading_order_unresolved"}
                )
            continue
        ids = record.ordered_source_line_ids
        if not saved_table_witness_valid(extraction, record):
            issues.append({"page": record.page, "reason": "reading_order_table_witness_invalid"})
            continue
        if record.status == "ordered" and (record.reason or not record.source_inventory_sha256):
            issues.append({"page": record.page, "reason": "reading_order_metadata_inconsistent"})
            continue
        if (
            ids != by_page.get(record.page, [])
            or len(ids) != record.source_line_count
            or len(set(ids)) != len(ids)
        ):
            issues.append({"page": record.page, "reason": "reading_order_source_identity_mismatch"})
        elif record.status == "unresolved" and (pages is None or record.page in pages):
            issues.append(
                {"page": record.page, "reason": record.reason or "reading_order_unresolved"}
            )
    return issues
