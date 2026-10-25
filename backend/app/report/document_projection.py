"""Source-bound DOCX display shaping, independent of storage and record ownership."""

from app.config import Settings
from app.ingest.analytical_source import analytical_source
from app.ingest.figures import saved_figure_readings
from app.ingest.regions import _flatten
from app.ingest.schemas import ExtractionResult, SectionTree
from app.report.schemas import (
    DocumentImageOut,
    DocumentParagraphOut,
    DocumentParagraphsOut,
    DocumentTableOut,
    NativeMathSourceOut,
)


def project_docx_source(
    extraction: ExtractionResult,
    expected_source_sha256: str | None,
    section_tree: SectionTree,
    settings: Settings,
) -> DocumentParagraphsOut:
    heading_levels = {
        node.paragraph: node.level
        for node in _flatten(section_tree.nodes)
        if node.paragraph is not None
    }
    paragraphs = [
        DocumentParagraphOut(
            paragraph=block.paragraph,
            text=block.text,
            heading_level=heading_levels.get(block.paragraph),
        )
        for block in extraction.blocks
        if block.paragraph is not None
    ]
    image_items = []
    source_bound = (
        extraction.docx_source_sha256 is not None
        and extraction.docx_source_sha256 == expected_source_sha256
    )
    visual_by_index = {
        entry.image_index: entry
        for entry in saved_figure_readings(extraction, expected_source_sha256) or []
    }
    for index, image in enumerate(extraction.images[: settings.docx_image_max_count]):
        if image.paragraph is None:
            continue
        source = image.docx_source if source_bound else None
        visual_payload = visual_by_index.get(index)
        image_items.append(
            DocumentImageOut(
                image_index=index,
                paragraph=image.paragraph,
                available=source is not None,
                source_alt_text=image.source_alt_text,
                width_px=source.width_px if source else None,
                height_px=source.height_px if source else None,
                unavailable_reason=None
                if source
                else (image.display_unavailable_reason or "source_not_recorded"),
                figure_reading=visual_payload,
            )
        )
    native = analytical_source(extraction, expected_source_sha256=expected_source_sha256 or "")
    native_cells = native.inventory.display_cells if native.inventory else []
    native_cells_by_paragraph = {}
    native_coordinates_by_paragraph = {}
    for cell in native_cells:
        native_cells_by_paragraph.setdefault(cell.paragraph, []).append(cell)
        native_coordinates_by_paragraph.setdefault(cell.paragraph, set()).update(
            cell.display_coordinates
        )
    return DocumentParagraphsOut(
        paragraphs=paragraphs,
        tables=[
            DocumentTableOut(
                paragraph=table.paragraph,
                rows=[
                    [
                        ""
                        if (row_index, column_index)
                        in native_coordinates_by_paragraph.get(table.paragraph, set())
                        else value
                        for column_index, value in enumerate(row)
                    ]
                    for row_index, row in enumerate(table.rows)
                ],
                native_cells=[
                    {
                        "display_coordinates": cell.display_coordinates,
                        "pieces": [piece.model_dump() for piece in cell.pieces],
                    }
                    for cell in native_cells_by_paragraph.get(table.paragraph, [])
                ],
            )
            for table in extraction.tables
            if table.paragraph is not None and table.source == "native"
        ],
        images=image_items,
        omitted_image_occurrences=extraction.docx_omitted_image_occurrences
        + max(0, len(extraction.images) - settings.docx_image_max_count),
        native_math=NativeMathSourceOut(
            state=native.state,
            occurrence_count=native.occurrence_count,
            containers=[
                {
                    "paragraph": container.paragraph,
                    "kind": container.kind,
                    "pieces": [piece.model_dump() for piece in container.pieces],
                }
                for container in native.inventory.containers
            ]
            if native.inventory
            else [],
        ),
    )
