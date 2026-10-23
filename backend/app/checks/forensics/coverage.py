"""F6 applicability cannot be inferred from image-reading omissions."""

from collections import Counter

from app.checks.forensics.extract import ReportedStat, table_statistical_cells_complete
from app.ingest.analytical_source import analytical_source
from app.ingest.schemas import ExtractionResult


def image_coverage(extraction: ExtractionResult, stats: list[ReportedStat]) -> dict:
    native_source = analytical_source(extraction)
    readings = extraction.image_readings or []
    ledger_valid = extraction.image_reading_version == "v1" and [
        row.image_index for row in readings
    ] == list(range(len(extraction.images)))
    parsed_tables = {stat.table_index for stat in stats if stat.table_index is not None}
    parsed_images = set()
    incomplete_tables = set()
    for table_index, table in enumerate(extraction.tables):
        index = table.source_image_index
        if not ledger_valid or index is None or index >= len(extraction.images):
            continue
        reading, image = readings[index], extraction.images[index]
        if (
            table.source == "vision"
            and not table.low_confidence
            and table_index in parsed_tables
            and reading.status == "transcribed"
            and reading.kind == "table"
            and reading.confidence == "high"
            and reading.attempted
            and (table.page, table.paragraph) == (image.page, image.paragraph)
        ):
            parsed_images.add(index)
            if not table_statistical_cells_complete(table):
                incomplete_tables.add(index)
        else:
            incomplete_tables.add(index)
    checked_images = parsed_images - incomplete_tables

    items = []
    for index, image in enumerate(extraction.images):
        if index in checked_images:
            continue
        reading = readings[index] if ledger_valid else None
        reason = "reading_not_recorded"
        if reading is not None:
            reason = reading.reason or (
                "equation_not_checked" if reading.kind == "equation" else "table_not_checked"
            )
        if index in parsed_images and index in incomplete_tables:
            reason = "table_partly_parsed"
        items.append(
            {
                "image_index": index,
                "page": image.page,
                "paragraph": image.paragraph,
                "reason": reason,
            }
        )
    reasons = Counter(item["reason"] for item in items)
    recovered_figures = (
        sum(len(page.figures) for page in extraction.recovery.pages) if extraction.recovery else 0
    )
    recovered_tables = (
        sum(len(page.tables) for page in extraction.recovery.pages) if extraction.recovery else 0
    )
    # Recovery regions are unassessed proposals, not additional confirmed
    # figures. Do not merge their counts into the native image inventory.
    incomplete = bool(
        items
        or extraction.docx_omitted_image_occurrences
        or extraction.equations
        or recovered_figures
        or recovered_tables
    )
    return {
        **native_source.detail(),
        "image_coverage_version": "v1",
        "n_image_occurrences": len(extraction.images),
        "n_image_readings_attempted": sum(row.attempted for row in readings) if ledger_valid else 0,
        "n_image_tables_with_stats": len(parsed_images),
        "n_images_unassessed": len(items),
        "n_image_occurrences_omitted": extraction.docx_omitted_image_occurrences,
        "n_equations_unassessed": len(extraction.equations),
        "n_recovery_figures_unassessed": recovered_figures,
        "n_recovery_tables_unassessed": recovered_tables,
        "image_coverage_items": items,
        "image_coverage_reasons": dict(reasons),
        "n_skipped_api_down": reasons["api_down"],
        "n_skipped_quota": reasons["quota_exhausted"],
        "n_skipped_parse_failure": reasons["invalid_response"],
        **(
            {
                "coverage_reason": "image_content_unassessed",
                "assessment_scope": "available_text_and_tables",
                "analysis_coverage": "partial" if stats else "unsupported",
            }
            if incomplete
            else {}
        ),
        **(
            {
                "coverage_reason": "image_content_unassessed"
                if incomplete
                else (
                    "native_math_unassessed"
                    if native_source.inventory is not None
                    else "native_math_inventory_unavailable"
                ),
                "assessment_scope": "available_text_and_tables",
                "analysis_coverage": "partial" if stats else "unsupported",
            }
            if native_source.limited
            else {}
        ),
    }
