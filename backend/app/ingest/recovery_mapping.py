"""F1 source-proof adapter for local layout proposals; no replacement text."""

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import median

from app.config import Settings
from app.ingest.quality import reading_order_issues
from app.ingest.recovery_models import (
    LayoutBox,
    LayoutCaption,
    LayoutPage,
    LayoutReply,
    LayoutTable,
    NativeSourceSpan,
    RecoveredCaption,
    RecoveredCell,
    RecoveredFigure,
    RecoveredTable,
    RecoveryEnvelope,
    RecoveryPage,
    Rect,
)
from app.ingest.schemas import ExtractionResult
from app.ingest.source_slices import line_inventory_valid


def digest_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def native_fingerprint(extraction: ExtractionResult) -> str:
    """Include all canonical fields so cached mappings cannot outlive source edits."""
    payload = extraction.model_dump(
        mode="json", exclude={"recovery", "vision_status", "tables", "equations"}
    )
    payload["tables"] = [
        table.model_dump(mode="json") for table in extraction.tables if table.source == "native"
    ]
    return digest_json(payload)


class MappingUnavailable(ValueError):
    """A coded reason safe to retain without exposing parser exceptions."""


def _fold_space(text: str) -> str:
    # Whitespace-only comparison. Signs, punctuation, case and all symbols stay.
    return " ".join(text.split())


def _center_y(box: Rect) -> float:
    return (box[1] + box[3]) / 2


def _within(box: Rect, outer: Rect, tolerance: float) -> bool:
    return (
        outer[0] - tolerance <= box[0] < box[2] <= outer[2] + tolerance
        and outer[1] - tolerance <= _center_y(box) <= outer[3] + tolerance
    )


def _intersects(box: Rect, outer: Rect) -> bool:
    return min(box[2], outer[2]) > max(box[0], outer[0]) and min(box[3], outer[3]) > max(
        box[1], outer[1]
    )


def _rect(box: LayoutBox, page: LayoutPage, tolerance: float) -> Rect:
    value = box.top_left(page.height)
    if not (
        -tolerance <= value[0] < value[2] <= page.width + tolerance
        and -tolerance <= value[1] < value[3] <= page.height + tolerance
    ):
        raise MappingUnavailable("invalid_proposed_geometry")
    return value


def _native_lines(extraction: ExtractionResult, page: int) -> list[NativeSourceSpan]:
    result = []
    for index, block in enumerate(extraction.blocks):
        if block.page != page:
            continue
        if not line_inventory_valid(block) or block.source_lines is None:
            raise MappingUnavailable("native_line_inventory_unavailable")
        for line in block.source_lines:
            if line.source_id is None:
                raise MappingUnavailable("native_line_identity_unavailable")
            if not line.is_furniture:
                result.append(
                    NativeSourceSpan(
                        page=page,
                        block_index=index,
                        source_id=line.source_id,
                        start=line.start,
                        end=line.end,
                        text=block.text[line.start : line.end],
                        bbox=line.bbox,
                    )
                )
    if len({line.source_id for line in result}) != len(result):
        raise MappingUnavailable("duplicate_native_identity")
    return result


@dataclass
class _Budget:
    remaining: int

    def use(self) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise MappingUnavailable("mapping_budget_exhausted")


def _matches(
    text: str,
    box: Rect,
    lines: list[NativeSourceSpan],
    settings: Settings,
    budget: _Budget,
) -> list[list[NativeSourceSpan]]:
    """Enumerate bounded exact whole-line candidates, never choose nearest text."""
    eligible = [
        line
        for line in lines
        if _within(line.bbox, box, settings.ingest_reading_order_row_tolerance_pts)
    ]
    eligible.sort(key=lambda line: (_center_y(line.bbox), line.bbox[0]))
    rows: list[list[NativeSourceSpan]] = []
    for line in eligible:
        if (
            not rows
            or _center_y(line.bbox) - _center_y(rows[-1][0].bbox)
            > settings.ingest_reading_order_row_tolerance_pts
        ):
            rows.append([line])
        else:
            rows[-1].append(line)
    ordered = [line for row in rows for line in sorted(row, key=lambda line: line.bbox[0])]
    target = _fold_space(text)
    matches = []
    for start in range(len(ordered)):
        for end in range(
            start + 1,
            min(len(ordered), start + settings.ingest_recovery_max_fragments_per_cell) + 1,
        ):
            budget.use()
            group = ordered[start:end]
            candidate = _fold_space(" ".join(line.text for line in group))
            if candidate == target:
                matches.append(group)
            if len(candidate) > len(target):
                break
    return matches


def _captions(
    candidates: list[LayoutCaption],
    page: LayoutPage,
    lines: list[NativeSourceSpan],
    settings: Settings,
    budget: _Budget,
) -> list[RecoveredCaption]:
    result = []
    for caption in candidates:
        if caption.page != page.page:
            raise MappingUnavailable("caption_page_mismatch")
        box = _rect(caption.bbox, page, settings.ingest_reading_order_geometry_tolerance_pts)
        matches = _matches(caption.text, box, lines, settings, budget)
        result.append(
            RecoveredCaption(
                proposed_text=caption.text, sources=matches[0] if len(matches) == 1 else []
            )
        )
    return result


def _table(
    proposal: LayoutTable,
    page: LayoutPage,
    lines: list[NativeSourceSpan],
    settings: Settings,
    budget: _Budget,
) -> RecoveredTable:
    limit = settings.ingest_recovery_max_cells_per_table
    if len(proposal.cells) > limit or proposal.rows * proposal.cols > limit:
        raise MappingUnavailable("table_cell_limit")
    eps = settings.ingest_reading_order_geometry_tolerance_pts
    row_eps = settings.ingest_reading_order_row_tolerance_pts
    region = _rect(proposal.bbox, page, eps)
    local = [
        line
        for line in lines
        if _within(line.bbox, region, row_eps) or _intersects(line.bbox, region)
    ]
    occupied: set[tuple[int, int]] = set()
    choices: list[list[list[NativeSourceSpan]]] = []
    boxes: list[Rect | None] = []
    for cell in proposal.cells:
        if not (
            0 <= cell.row_start < cell.row_end <= proposal.rows
            and 0 <= cell.col_start < cell.col_end <= proposal.cols
        ):
            raise MappingUnavailable("invalid_cell_span")
        positions = {
            (row, col)
            for row in range(cell.row_start, cell.row_end)
            for col in range(cell.col_start, cell.col_end)
        }
        if occupied.intersection(positions):
            raise MappingUnavailable("overlapping_cell_spans")
        occupied.update(positions)
        box = _rect(cell.bbox, page, eps) if cell.bbox else None
        boxes.append(box)
        choices.append(
            _matches(cell.text, box, local, settings, budget)
            if box is not None and cell.text.strip()
            else []
        )

    # Only uniquely mapped single-row cells establish row positions. A repeated
    # value cannot choose its own occurrence by box center or input order.
    anchors: dict[int, list[float]] = defaultdict(list)
    for cell, matches in zip(proposal.cells, choices, strict=True):
        if len(matches) == 1 and cell.row_end == cell.row_start + 1:
            ys = [_center_y(line.bbox) for line in matches[0]]
            if max(ys) - min(ys) <= row_eps:
                anchors[cell.row_start].append(median(ys))
    coherent = {row: median(ys) for row, ys in anchors.items() if max(ys) - min(ys) <= row_eps}
    incoherent = set(anchors) - coherent.keys()
    cells = []
    for cell, matches, box in zip(proposal.cells, choices, boxes, strict=True):
        if cell.row_end == cell.row_start + 1:
            if cell.row_start in incoherent:
                matches = []
            elif cell.row_start in coherent:
                matches = [
                    group
                    for group in matches
                    if all(
                        abs(_center_y(line.bbox) - coherent[cell.row_start]) <= row_eps
                        for line in group
                    )
                ]
        sources = matches[0] if len(matches) == 1 else []
        cells.append(
            RecoveredCell(
                row_start=cell.row_start,
                row_end=cell.row_end,
                col_start=cell.col_start,
                col_end=cell.col_end,
                proposed_text=cell.text,
                proposed_bbox=box,
                column_header=cell.column_header,
                row_header=cell.row_header,
                status="source_mapped"
                if sources
                else ("unresolved" if cell.text.strip() else "empty"),
                sources=sources,
            )
        )
    used = Counter(source.source_id for cell in cells for source in cell.sources)
    repeated = {sid for sid, count in used.items() if count > 1}
    for cell in cells:
        if any(source.source_id in repeated for source in cell.sources):
            cell.sources = []
            cell.status = "unresolved"
    reasons = []
    if repeated:
        reasons.append("source_occurrence_reused")
    if incoherent:
        reasons.append("row_geometry_inconsistent")
    # A blank cell can hide a shifted value from within-row ordering checks.
    # Require a shared horizontal position for each unmerged column across
    # source rows. Mixed header/body alignment without this shared interval
    # remains partial; do not guess which column owns an isolated value.
    columns: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for cell in cells:
        if cell.sources and cell.col_end == cell.col_start + 1:
            columns[cell.col_start].append(
                (
                    min(source.bbox[0] for source in cell.sources),
                    max(source.bbox[2] for source in cell.sources),
                )
            )
    intersections = {
        col: (max(x[0] for x in spans), min(x[1] for x in spans)) for col, spans in columns.items()
    }
    if any(left > right + eps for left, right in intersections.values()):
        reasons.append("column_geometry_inconsistent")
    ordered_columns = sorted(intersections.items())
    for (_, first), (_, second) in zip(ordered_columns, ordered_columns[1:], strict=False):
        if first[1] > second[0] + eps:
            reasons.append("column_geometry_ambiguous")
    # A row/column permutation with correct text must still fail geometry.
    for left_index, left in enumerate(cells):
        if not left.sources:
            continue
        for right in cells[left_index + 1 :]:
            budget.use()
            if not right.sources:
                continue
            if left.row_start == right.row_start and left.row_end == right.row_end:
                first, second = sorted((left, right), key=lambda cell: cell.col_start)
                if first.col_end <= second.col_start and (
                    max(source.bbox[2] for source in first.sources)
                    > min(source.bbox[0] for source in second.sources) + eps
                ):
                    reasons.append("column_geometry_inconsistent")
            if left.row_end <= right.row_start and (
                median(_center_y(source.bbox) for source in left.sources)
                >= median(_center_y(source.bbox) for source in right.sources) - eps
            ):
                reasons.append("row_geometry_inconsistent")
            if right.row_end <= left.row_start and (
                median(_center_y(source.bbox) for source in right.sources)
                >= median(_center_y(source.bbox) for source in left.sources) - eps
            ):
                reasons.append("row_geometry_inconsistent")
    captions = _captions(proposal.captions, page, lines, settings, budget)
    assigned = {source.source_id for cell in cells for source in cell.sources}
    caption_ids = {source.source_id for caption in captions for source in caption.sources}
    if any(
        not _within(line.bbox, region, row_eps) and line.source_id not in caption_ids
        for line in local
    ):
        reasons.append("table_boundary_ambiguous")
    unmapped = [line.source_id for line in local if line.source_id not in assigned | caption_ids]
    if unmapped:
        reasons.append("unmapped_native_content")
    if any(cell.status == "unresolved" for cell in cells):
        reasons.append("cell_source_unresolved")
    if any(not caption.sources for caption in captions):
        reasons.append("caption_source_unresolved")
    if not assigned:
        reasons.append("no_mapped_cells")
    return RecoveredTable(
        region_id=proposal.region_id,
        page=page.page,
        proposed_bbox=region,
        rows=proposal.rows,
        cols=proposal.cols,
        cells=cells,
        captions=captions,
        status="partial" if reasons else "source_mapped",
        reasons=sorted(set(reasons)),
        unmapped_source_ids=unmapped,
    )


def map_layout_reply(
    extraction: ExtractionResult, envelope: RecoveryEnvelope, reply: LayoutReply, settings: Settings
) -> RecoveryEnvelope:
    if (
        reply.source_sha256 != envelope.source_sha256
        or reply.request_sha256 != envelope.request_sha256
        or reply.manifest_sha256 != envelope.manifest_sha256
        or native_fingerprint(extraction) != envelope.native_sha256
    ):
        raise MappingUnavailable("recovery_identity_mismatch")
    if [page.page for page in reply.pages] != envelope.requested_pages:
        raise MappingUnavailable("recovery_page_scope_mismatch")
    scope = set(envelope.requested_pages)
    if (
        extraction.reading_order is None
        or any(
            record.page in scope and not record.source_inventory_sha256
            for record in extraction.reading_order
        )
        or any(
            issue["reason"] != "table_regions_unresolved"
            for issue in reading_order_issues(extraction, pages=scope)
        )
    ):
        raise MappingUnavailable("native_ledger_unavailable")
    geometries = {geometry.page: geometry for geometry in extraction.geometry}
    pages = []
    for page in reply.pages:
        try:
            geometry = geometries.get(page.page)
            eps = settings.ingest_reading_order_geometry_tolerance_pts
            if (
                geometry is None
                or geometry.rotation != 0
                or abs(geometry.width - page.width) > eps
                or abs(geometry.height - page.height) > eps
            ):
                raise MappingUnavailable("recovery_page_geometry_mismatch")
            if page.status != "complete":
                raise MappingUnavailable("worker_page_unavailable")
            if len(page.tables) + len(page.figures) > settings.ingest_recovery_max_regions_per_page:
                raise MappingUnavailable("page_region_limit")
            ids = [item.region_id for item in [*page.tables, *page.figures]]
            if len(ids) != len(set(ids)):
                raise MappingUnavailable("duplicate_region_identity")
            lines = _native_lines(extraction, page.page)
            budget = _Budget(settings.ingest_recovery_max_mapping_comparisons)
            tables = [_table(table, page, lines, settings, budget) for table in page.tables]
            used = Counter(s.source_id for t in tables for c in t.cells for s in c.sources)
            for table in tables:
                if any(used[s.source_id] > 1 for cell in table.cells for s in cell.sources):
                    table.status = "partial"
                    table.reasons.append("overlapping_table_sources")
            figures = [
                RecoveredFigure(
                    region_id=figure.region_id,
                    page=page.page,
                    proposed_bbox=_rect(figure.bbox, page, eps),
                    captions=_captions(figure.captions, page, lines, settings, budget),
                )
                for figure in page.figures
            ]
            pages.append(
                RecoveryPage(page=page.page, status="processed", tables=tables, figures=figures)
            )
        except MappingUnavailable as exc:
            pages.append(RecoveryPage(page=page.page, status="unavailable", reason=str(exc)))
    return envelope.model_copy(
        update={"pages": pages, "versions": reply.versions, "proposal": reply.model_copy(deep=True)}
    )
