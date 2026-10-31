"""F1 native table proposals require complete, exact source ownership.

No canonical line is split or rewritten. A cross-cell line is admitted only
when its original characters completely cover a contiguous same-row cell run.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

import pymupdf

from app.config import Settings
from app.ingest.native_table_models import (
    NativeTableOrderWitness,
    TableCellOrderWitness,
    TableCharacterRun,
    TableGridOrderWitness,
)
from app.ingest.normalize import NUL
from app.ingest.schemas import Line

Rect = tuple[float, float, float, float]


class TableOrderUnresolved(ValueError):
    """The admitted source inventory does not establish this table order."""


def whitespace_text(value: str) -> str:
    # Line text has NUL dropped at extraction, but PyMuPDF's characters and
    # table cells still carry it, so every comparison here drops it too.
    return " ".join(value.replace(NUL, "").split())


def text_digest(value: str) -> str:
    return hashlib.sha256(whitespace_text(value).encode("utf-8")).hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _contains(outer: Rect, inner: Rect, eps: float) -> bool:
    return (
        outer[0] - eps <= inner[0] < inner[2] <= outer[2] + eps
        and outer[1] - eps <= inner[1] < inner[3] <= outer[3] + eps
    )


def _intersects(left: Rect, right: Rect, eps: float) -> bool:
    return (
        min(left[2], right[2]) - max(left[0], right[0]) > eps
        and min(left[3], right[3]) - max(left[1], right[1]) > eps
    )


def _valid_rect(rect: Rect) -> bool:
    return (
        len(rect) == 4
        and all(math.isfinite(v) for v in rect)
        and rect[0] < rect[2]
        and rect[1] < rect[3]
    )


@dataclass
class NativeCell:
    row: int
    column: int
    bbox: Rect
    text: str


@dataclass
class NativeGrid:
    bbox: Rect
    row_count: int
    column_count: int
    cells: list[NativeCell]


@dataclass
class CharacterLine:
    chars: list[tuple[str, Rect]]
    sha256: str


@dataclass
class NativeTableProposal:
    page_bbox: Rect | None = None
    grids: list[NativeGrid] = field(default_factory=list)
    characters: dict[str, CharacterLine] = field(default_factory=dict)
    problem: str | None = None


def admit_native_tables(tables, proposal: NativeTableProposal, settings: Settings) -> None:
    """Charge complete proposed slots before copying native cell inventories."""
    if not settings.ingest_native_table_order_enabled:
        proposal.problem = "native_table_order_disabled"
    elif len(tables) > settings.ingest_native_table_max_tables_per_page or (
        sum(table.row_count * table.col_count for table in tables)
        > settings.ingest_native_table_max_cells_per_page
    ):
        proposal.problem = "native_table_budget_exhausted"


def snapshot_native_table(table, rows, proposal: NativeTableProposal, settings: Settings) -> None:
    """Copy one native table's cells. A cell that spans rows or columns has one
    rectangle covering its span, and the slots it covers come back as None;
    those slots get no cell of their own, and validate_grid proves the span."""
    if proposal.problem:
        return
    try:
        if len(rows) != table.row_count or any(len(row) != table.col_count for row in rows):
            raise TableOrderUnresolved("native_table_grid_invalid")
        cells = []
        for row_index, row in enumerate(table.rows):
            if len(row.cells) != table.col_count:
                raise TableOrderUnresolved("native_table_grid_invalid")
            for column, rect in enumerate(row.cells):
                value = rows[row_index][column]
                if rect is None:
                    # A covered slot carries no text of its own.
                    if value:
                        raise TableOrderUnresolved("native_table_merged_cell")
                    continue
                if value is not None and not isinstance(value, str):
                    raise TableOrderUnresolved("native_table_cell_invalid")
                cells.append(NativeCell(row_index, column, tuple(rect), value or ""))
        grid = NativeGrid(tuple(table.bbox), table.row_count, table.col_count, cells)
        validate_grid(
            grid, proposal.page_bbox, settings.ingest_reading_order_geometry_tolerance_pts
        )
        proposal.grids.append(grid)
    except TableOrderUnresolved as refused:
        # Keep the specific reason (a merged cell is not a damaged grid).
        proposal.problem = str(refused)
    except (TypeError, IndexError, AttributeError, ValueError):
        proposal.problem = "native_table_grid_invalid"


def _line(values: list[float], eps: float) -> float | None:
    """One grid line from the cell edges that claim it, or None if they disagree."""
    if not values or max(values) - min(values) > eps:
        return None
    return values[0]


def _span_end(lines: list[float], start: int, edge: float, eps: float) -> int | None:
    matches = [k for k in range(start + 1, len(lines)) if abs(lines[k] - edge) <= eps]
    return matches[0] if len(matches) == 1 else None


def validate_grid(grid: NativeGrid, page_bbox: Rect | None, eps: float) -> None:
    """A grid is its row and column lines plus cells that each start on one
    line pair and end on another, covering every slot exactly once. A plain
    grid is the case where every cell spans one row and one column; a merged
    cell spans more, and is read once, at its top-left slot."""
    if (
        page_bbox is None
        or not _valid_rect(page_bbox)
        or not _valid_rect(grid.bbox)
        or not _contains(page_bbox, grid.bbox, eps)
        or grid.row_count <= 0
        or grid.column_count <= 0
        or not grid.cells
    ):
        raise TableOrderUnresolved("native_table_grid_invalid")
    origins = [(c.row, c.column) for c in grid.cells]
    if origins != sorted(set(origins)) or any(
        not (0 <= r < grid.row_count and 0 <= c < grid.column_count) for r, c in origins
    ):
        raise TableOrderUnresolved("native_table_grid_invalid")
    if any(not _valid_rect(c.bbox) or not _contains(grid.bbox, c.bbox, eps) for c in grid.cells):
        raise TableOrderUnresolved("native_table_grid_invalid")
    merged = len(grid.cells) != grid.row_count * grid.column_count
    reason = "native_table_merged_cell" if merged else "native_table_grid_invalid"
    rows = [
        _line([c.bbox[1] for c in grid.cells if c.row == r], eps) for r in range(grid.row_count)
    ]
    columns = [
        _line([c.bbox[0] for c in grid.cells if c.column == k], eps)
        for k in range(grid.column_count)
    ]
    if None in rows or None in columns:
        raise TableOrderUnresolved(reason)
    rows.append(grid.bbox[3])
    columns.append(grid.bbox[2])
    if (
        abs(rows[0] - grid.bbox[1]) > eps
        or abs(columns[0] - grid.bbox[0]) > eps
        or any(b - a <= eps for a, b in zip(rows, rows[1:], strict=False))
        or any(b - a <= eps for a, b in zip(columns, columns[1:], strict=False))
    ):
        raise TableOrderUnresolved(reason)
    covered: set[tuple[int, int]] = set()
    for cell in grid.cells:
        bottom = _span_end(rows, cell.row, cell.bbox[3], eps)
        right = _span_end(columns, cell.column, cell.bbox[2], eps)
        if bottom is None or right is None:
            # A cell that ends between lines because it runs into another
            # cell (a list misread as a table) is an overlap, not a span.
            if any(
                other is not cell and _intersects(cell.bbox, other.bbox, eps)
                for other in grid.cells
            ):
                raise TableOrderUnresolved("native_table_cells_overlap")
            raise TableOrderUnresolved(reason)
        slots = {(r, k) for r in range(cell.row, bottom) for k in range(cell.column, right)}
        if covered & slots:
            # Two cells claim one slot: not a table layout this can read.
            raise TableOrderUnresolved("native_table_cells_overlap")
        covered |= slots
    if len(covered) != grid.row_count * grid.column_count:
        raise TableOrderUnresolved(reason)


def capture_native_characters(
    page, raw_blocks, lines: list[Line], proposal: NativeTableProposal, settings: Settings
) -> None:
    """Read RAWDICT only for admitted cross-cell candidates; charge all text first."""
    if proposal.problem or not proposal.grids:
        return
    eps = settings.ingest_reading_order_geometry_tolerance_pts
    cells = [cell for grid in proposal.grids for cell in grid.cells]
    if len(cells) * len(lines) > settings.ingest_reading_order_max_pair_comparisons:
        proposal.problem = "native_table_pair_budget_exhausted"
        return
    candidates = {
        line.source_id
        for line in lines
        if any(_intersects(grid.bbox, line.bbox, eps) for grid in proposal.grids)
        and sum(_contains(cell.bbox, line.bbox, eps) for cell in cells) != 1
    }
    if not candidates:
        return
    originals = {}
    total = 0
    from app.ingest.reading_order import line_source_id

    for block_no, block in enumerate(raw_blocks):
        for line_no, line in enumerate(block.get("lines", [])):
            sid = line_source_id(page.number + 1, block, block_no, line_no)
            originals[sid] = line
            total += sum(len(span.get("text", "")) for span in line.get("spans", []))
    if total > settings.ingest_native_table_max_characters_per_page:
        proposal.problem = "native_table_character_budget_exhausted"
        return
    charged = len(cells) * (
        len(lines)
        + sum(
            len(span.get("text", ""))
            for sid in candidates
            for span in originals.get(sid, {}).get("spans", [])
        )
    )
    if charged > settings.ingest_reading_order_max_pair_comparisons:
        proposal.problem = "native_table_pair_budget_exhausted"
        return
    raw = page.get_text(
        "rawdict", sort=True, flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    )["blocks"]
    observed = set()
    try:
        for block_no, block in enumerate(raw):
            for line_no, line in enumerate(block.get("lines", [])):
                sid = line_source_id(page.number + 1, block, block_no, line_no)
                if sid not in candidates:
                    continue
                if sid in observed or sid not in originals:
                    raise TableOrderUnresolved("native_table_character_binding_invalid")
                observed.add(sid)
                original = originals[sid]
                if (
                    line.get("dir") != original.get("dir")
                    or line.get("wmode") != original.get("wmode")
                    or tuple(line["bbox"]) != tuple(original["bbox"])
                    or len(line["spans"]) != len(original["spans"])
                ):
                    raise TableOrderUnresolved("native_table_character_binding_invalid")
                chars = []
                for source_span, native_span in zip(original["spans"], line["spans"], strict=True):
                    if any(
                        source_span.get(key) != native_span.get(key)
                        for key in ("font", "size", "flags", "origin", "bbox")
                    ):
                        raise TableOrderUnresolved("native_table_character_binding_invalid")
                    sequence = native_span["chars"]
                    if "".join(char["c"] for char in sequence) != source_span["text"]:
                        raise TableOrderUnresolved("native_table_character_binding_invalid")
                    chars.extend((char["c"], tuple(char["bbox"])) for char in sequence)
                if not chars or any(len(c) != 1 or not _valid_rect(box) for c, box in chars):
                    raise TableOrderUnresolved("native_table_character_binding_invalid")
                proposal.characters[sid] = CharacterLine(chars, _digest(chars))
        if observed != candidates:
            raise TableOrderUnresolved("native_table_character_binding_invalid")
    except (TableOrderUnresolved, KeyError, TypeError, ValueError):
        proposal.characters.clear()
        proposal.problem = "native_table_character_binding_invalid"


def order_native_tables(
    lines: list[Line],
    proposal: NativeTableProposal,
    inventory_sha: str,
    order_subset: Callable[[list[Line]], list[str]],
    order_cell: Callable[[list[str]], list[str]],
    settings: Settings,
) -> tuple[list[str], NativeTableOrderWitness]:
    if proposal.problem or not proposal.grids:
        raise TableOrderUnresolved(proposal.problem or "native_table_grid_missing")
    eps = settings.ingest_reading_order_geometry_tolerance_pts
    grids = sorted(proposal.grids, key=lambda g: (g.bbox[1], g.bbox[0]))
    if not math.isfinite(eps) or not math.isfinite(settings.ingest_reading_order_row_tolerance_pts):
        raise TableOrderUnresolved("native_table_policy_invalid")
    for grid in grids:
        validate_grid(grid, proposal.page_bbox, eps)
    if any(a.bbox[3] > b.bbox[1] + eps for a, b in zip(grids, grids[1:], strict=False)):
        raise TableOrderUnresolved("native_table_flow_interleaved")
    cells = [(table_index, cell) for table_index, grid in enumerate(grids) for cell in grid.cells]
    character_count = sum(len(value.chars) for value in proposal.characters.values())
    if character_count > settings.ingest_native_table_max_characters_per_page:
        raise TableOrderUnresolved("native_table_character_budget_exhausted")
    if (len(lines) + character_count) * len(
        cells
    ) > settings.ingest_reading_order_max_pair_comparisons:
        raise TableOrderUnresolved("native_table_pair_budget_exhausted")
    if len(lines) * len(cells) > settings.ingest_reading_order_max_pair_comparisons:
        raise TableOrderUnresolved("native_table_pair_budget_exhausted")
    by_id = {line.source_id: line for line in lines}
    if len(by_id) != len(lines) or None in by_id:
        raise TableOrderUnresolved("native_table_source_identity_invalid")
    owned = defaultdict(list)
    fragments = {}
    char_runs = defaultdict(list)
    spanning = {}
    outside = [[] for _ in range(len(grids) + 1)]
    for line in lines:
        owners = [
            (ti, cell.row, cell.column)
            for ti, cell in cells
            if _contains(cell.bbox, line.bbox, eps)
        ]
        if len(owners) == 1:
            key = owners[0]
            owned[key].append(line.source_id)
            fragments[key, line.source_id] = line.text
            continue
        touched = [ti for ti, grid in enumerate(grids) if _intersects(grid.bbox, line.bbox, eps)]
        if touched:
            if len(touched) != 1 or line.source_id not in proposal.characters:
                raise TableOrderUnresolved("native_table_line_ownership_unresolved")
            native = proposal.characters[line.source_id]
            if whitespace_text("".join(c for c, _ in native.chars)) != line.text:
                raise TableOrderUnresolved("native_table_character_binding_invalid")
            assignments = []
            for _, rect in native.chars:
                matches = [
                    (ti, cell.row, cell.column)
                    for ti, cell in cells
                    if _contains(cell.bbox, rect, eps)
                ]
                if len(matches) != 1:
                    raise TableOrderUnresolved("native_table_character_owner_unresolved")
                assignments.append(matches[0])
            keys = list(dict.fromkeys(assignments))
            if (
                len({key[:2] for key in keys}) != 1
                or [key[2] for key in keys] != list(range(keys[0][2], keys[-1][2] + 1))
                or assignments != sorted(assignments)
            ):
                raise TableOrderUnresolved("native_table_character_flow_unresolved")
            spanning[line.source_id] = keys
            for key in keys:
                indices = [i for i, owner in enumerate(assignments) if owner == key]
                start, end = indices[0], indices[-1] + 1
                if end - start != len(indices):
                    raise TableOrderUnresolved("native_table_character_flow_unresolved")
                owned[key].append(line.source_id)
                fragments[key, line.source_id] = "".join(c for c, _ in native.chars[start:end])
                char_runs[key].append(
                    TableCharacterRun(
                        source_line_id=line.source_id,
                        raw_character_count=len(native.chars),
                        raw_character_sha256=native.sha256,
                        start=start,
                        end=end,
                    )
                )
            continue
        for band in range(len(outside)):
            top = grids[band - 1].bbox[3] if band else -math.inf
            bottom = grids[band].bbox[1] if band < len(grids) else math.inf
            if top - eps <= line.bbox[1] and line.bbox[3] <= bottom + eps:
                outside[band].append(line)
                break
        else:
            raise TableOrderUnresolved("native_table_flow_interleaved")
    for sid, keys in spanning.items():
        if any(owned[key] != [sid] for key in keys):
            raise TableOrderUnresolved("native_table_character_flow_interleaved")
    output, witnesses = [], []
    for ti, grid in enumerate(grids):
        output.extend(order_subset(outside[ti]) if outside[ti] else [])
        cell_witnesses = []
        table_ids = []
        for cell in grid.cells:
            key = ti, cell.row, cell.column
            ids = owned[key]
            ids = order_cell(ids) if len(ids) > 1 else list(ids)
            content = whitespace_text(" ".join(fragments[key, sid] for sid in ids))
            if content != whitespace_text(cell.text):
                raise TableOrderUnresolved("native_table_cell_text_mismatch")
            cell_witnesses.append(
                TableCellOrderWitness(
                    row=cell.row,
                    column=cell.column,
                    bbox=cell.bbox,
                    text_sha256=text_digest(cell.text),
                    source_line_ids=ids,
                    character_runs=char_runs[key],
                )
            )
            table_ids.extend(sid for sid in ids if sid not in table_ids)
        output.extend(table_ids)
        witnesses.append(
            TableGridOrderWitness(
                bbox=grid.bbox,
                row_count=grid.row_count,
                column_count=grid.column_count,
                cells=cell_witnesses,
            )
        )
    output.extend(order_subset(outside[-1]) if outside[-1] else [])
    if len(output) != len(lines) or set(output) != set(by_id):
        raise TableOrderUnresolved("native_table_ordered_inventory_mismatch")
    characters = sorted(
        (sid, value.sha256, len(value.chars))
        for sid, value in proposal.characters.items()
        if sid in spanning
    )
    return output, NativeTableOrderWitness(
        page_bbox=proposal.page_bbox,
        geometry_tolerance_pts=eps,
        row_tolerance_pts=settings.ingest_reading_order_row_tolerance_pts,
        source_inventory_sha256=inventory_sha,
        character_inventory_sha256=_digest(characters) if characters else None,
        tables=witnesses,
    )


def saved_table_witness_valid(extraction, record) -> bool:
    """Check cached structure and canonical binding; read-time still replays bytes."""
    tables = sorted(
        (t for t in extraction.tables if t.page == record.page and t.source == "native"),
        key=lambda t: (t.bbox[1], t.bbox[0]) if t.bbox else (math.inf, math.inf),
    )
    witness = record.table_order
    if record.status != "ordered":
        return witness is None
    if not tables:
        return witness is None
    if (
        witness is None
        or witness.source_inventory_sha256 != record.source_inventory_sha256
        or len(witness.tables) != len(tables)
    ):
        return False
    source = {}
    for block in extraction.blocks:
        if block.page == record.page:
            for line in block.source_lines or []:
                if line.source_id in source:
                    return False
                source[line.source_id] = (block.text[line.start : line.end], line.bbox)
    occurrences = defaultdict(list)
    character_runs = defaultdict(list)
    table_ids = []
    try:
        for ti, (grid, saved) in enumerate(zip(witness.tables, tables, strict=True)):
            if (
                grid.bbox != saved.bbox
                or grid.row_count != len(saved.rows)
                or any(len(row) != grid.column_count for row in saved.rows)
            ):
                return False
            native = NativeGrid(
                grid.bbox,
                grid.row_count,
                grid.column_count,
                [
                    NativeCell(c.row, c.column, c.bbox, saved.rows[c.row][c.column])
                    for c in grid.cells
                ],
            )
            validate_grid(native, witness.page_bbox, witness.geometry_tolerance_pts)
            # A slot covered by a merged cell holds no text of its own.
            origins = {(c.row, c.column) for c in grid.cells}
            if any(
                saved.rows[r][k]
                for r in range(grid.row_count)
                for k in range(grid.column_count)
                if (r, k) not in origins
            ):
                return False
            current_ids = []
            for cell in grid.cells:
                if (
                    cell.text_sha256 != text_digest(saved.rows[cell.row][cell.column])
                    or len(cell.source_line_ids) != len(set(cell.source_line_ids))
                    or any(sid not in source for sid in cell.source_line_ids)
                ):
                    return False
                runs_by_id = defaultdict(list)
                for run in cell.character_runs:
                    if run.source_line_id not in cell.source_line_ids:
                        return False
                    runs_by_id[run.source_line_id].append(run)
                    character_runs[run.source_line_id].append(run)
                for sid in cell.source_line_ids:
                    occurrences[sid].append((ti, cell.row, cell.column))
                    if sid not in runs_by_id and not _contains(
                        cell.bbox, source[sid][1], witness.geometry_tolerance_pts
                    ):
                        return False
                if not cell.character_runs:
                    content = whitespace_text(
                        " ".join(source[sid][0] for sid in cell.source_line_ids)
                    )
                    if text_digest(content) != cell.text_sha256:
                        return False
                elif len(cell.source_line_ids) != 1:
                    return False
                current_ids.extend(sid for sid in cell.source_line_ids if sid not in current_ids)
            if set(table_ids).intersection(current_ids):
                return False
            table_ids.extend(current_ids)
        if [sid for sid in record.ordered_source_line_ids if sid in set(table_ids)] != table_ids:
            return False
        for sid, owners in occurrences.items():
            if len(owners) > 1 and (
                sid not in character_runs
                or len({owner[:2] for owner in owners}) != 1
                or [owner[2] for owner in owners] != list(range(owners[0][2], owners[-1][2] + 1))
            ):
                return False
        summaries = []
        for sid, runs in character_runs.items():
            # Character intervals describe the original native line. Bind its
            # cached canonical text as well: these cells have one sole owner,
            # so their complete retained texts must reconstruct that line.
            cell_text = " ".join(
                tables[ti].rows[row][column] for ti, row, column in occurrences[sid]
            )
            if whitespace_text(source[sid][0]) != whitespace_text(cell_text):
                return False
            hashes = {run.raw_character_sha256 for run in runs}
            counts = {run.raw_character_count for run in runs}
            if len(hashes) != 1 or len(counts) != 1:
                return False
            end = 0
            for run in sorted(runs, key=lambda run: run.start):
                if run.start != end:
                    return False
                end = run.end
            if end != runs[0].raw_character_count:
                return False
            summaries.append((sid, runs[0].raw_character_sha256, runs[0].raw_character_count))
        return witness.character_inventory_sha256 == (
            _digest(sorted(summaries)) if summaries else None
        )
    except (TableOrderUnresolved, ValueError, IndexError, TypeError):
        return False
