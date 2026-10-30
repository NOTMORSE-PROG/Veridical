"""What the rubric decomposer reads (F2.1, BUG-566).

Generic extraction flattens a grading grid into reading order, and short cells
end up next to the wrong row: on a real four-level rubric a Beginner cell sat
above its own row label and the next row's first sentence sat between two of
this row's cells, so the model shifted descriptors from one level, and one
criterion, to the next. The extractor already recovers tables cell by cell, so
the model gets each table as labelled cells and only the remaining text in
reading order.

Nothing here knows what a rubric looks like. A "grid" is any recovered table
with enough rows and columns, and a "copy" is any table or page that repeats an
earlier one. Values that differ between copies (a student's name typed into
each copy, a score written on one) are filled-in data, not requirements, and
are withheld from the model rather than sent to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.config import Settings
from app.ingest.normalize import match_key, normalize
from app.ingest.schemas import ExtractionResult, TableBlock, TextBlock
from app.rubric.decompose import raw_text_for_decomposition

SNAPSHOT_VERSION = 1


@dataclass(frozen=True)
class GridCell:
    """One cell as the model sees it: `T{table}.R{row}.C{col}`, 1-based, with
    empty columns already dropped."""

    table: int
    row: int
    col: int
    text: str
    page: int | None = None

    @property
    def ref(self) -> str:
        return f"T{self.table}.R{self.row}.C{self.col}"


@dataclass
class _Grid:
    block: TableBlock
    rows: list[list[str]]
    withheld: set[tuple[int, int]] = field(default_factory=set)
    copy_pages: list[int] = field(default_factory=list)
    number: int = 0
    # The kept grid this one repeats; None for a kept grid itself.
    owner: _Grid | None = None


@dataclass(frozen=True)
class DecompositionSource:
    """Everything the decomposition and its checks read from one upload.

    `prompt_text` is what the model receives. `cells` and `outside_lines` are
    the same content, split so a stored descriptor can be traced to exactly
    one cell or one passage. `withheld` lists the filled-in values kept out of
    the prompt; they never reach the model, so they can never reach a stored
    criterion either.
    """

    prompt_text: str
    cells: tuple[GridCell, ...]
    outside_lines: tuple[str, ...]
    gate_blocks: list[TextBlock]
    checklist_text: str
    withheld: tuple[str, ...] = ()
    # Cells left out because they differ between copies, as (table, row, col).
    withheld_cells: tuple[tuple[int, int, int], ...] = ()
    copies_merged: int = 0
    # Lines printed only on a repeated copy, not on the copy that was read.
    unread_copy_lines: int = 0
    # Every page a repeated table appears on (the first is the one read).
    copy_pages: tuple[int, ...] = ()

    def to_snapshot(self) -> dict:
        """What a later recheck needs, stored with the rubric. Withheld values
        (names typed on a copy) are never part of it; only their count is."""
        return {
            "version": SNAPSHOT_VERSION,
            "cells": [[c.table, c.row, c.col, c.page, c.text] for c in self.cells],
            "outside_lines": list(self.outside_lines),
            "withheld_cells": [list(key) for key in self.withheld_cells],
            "copy_pages": list(self.copy_pages),
            "withheld_count": len(self.withheld),
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> DecompositionSource | None:
        """The stored source, for rechecking edited criteria without reading
        the file or calling the model; None for a snapshot this code cannot
        read."""
        if not isinstance(snapshot, dict) or snapshot.get("version") != SNAPSHOT_VERSION:
            return None
        cells = tuple(
            GridCell(table=t, row=r, col=c, page=page, text=text)
            for t, r, c, page, text in snapshot["cells"]
        )
        return cls(
            prompt_text="",
            cells=cells,
            outside_lines=tuple(snapshot["outside_lines"]),
            gate_blocks=[],
            checklist_text="",
            withheld_cells=tuple(tuple(key) for key in snapshot["withheld_cells"]),
            copy_pages=tuple(snapshot["copy_pages"]),
        )

    @classmethod
    def from_text(cls, blocks: list[TextBlock], raw_text: str) -> DecompositionSource:
        """A source with no recovered tables: the model reads `raw_text` and
        every descriptor has to be found in it."""
        return cls(
            prompt_text=raw_text,
            cells=(),
            outside_lines=tuple(raw_text.splitlines()),
            gate_blocks=list(blocks),
            checklist_text=raw_text,
        )


def _trimmed_rows(rows: list[list[str]]) -> list[list[str]]:
    """Pad ragged rows and drop columns and rows that are empty throughout
    (PDF table detection often inserts a hairline column with no text)."""
    width = max((len(row) for row in rows), default=0)
    padded = [[normalize(cell or "") for cell in row] + [""] * (width - len(row)) for row in rows]
    padded = [row for row in padded if any(row)]
    keep = [col for col in range(width) if any(row[col] for row in padded)]
    return [[row[col] for col in keep] for row in padded]


def _is_grid(rows: list[list[str]], settings: Settings) -> bool:
    return (
        len(rows) >= settings.rubric_grid_min_rows
        and (len(rows[0]) if rows else 0) >= settings.rubric_grid_min_columns
    )


def _differing_cells(
    kept: list[list[str]], other: list[list[str]], settings: Settings
) -> set[tuple[int, int]] | None:
    """The cells that differ when `other` is a copy of `kept`; None when it is
    a different table."""
    if len(kept) != len(other) or any(len(a) != len(b) for a, b in zip(kept, other, strict=True)):
        return None
    compared = 0
    differing: set[tuple[int, int]] = set()
    for r, (row_a, row_b) in enumerate(zip(kept, other, strict=True)):
        for c, (a, b) in enumerate(zip(row_a, row_b, strict=True)):
            if not a and not b:
                continue
            compared += 1
            if match_key(a) != match_key(b):
                differing.add((r, c))
    if not compared:
        return None
    if (compared - len(differing)) / compared < settings.rubric_copy_min_identical_cell_ratio:
        return None
    return differing


def _grids(result: ExtractionResult, settings: Settings) -> tuple[list[_Grid], list[_Grid]]:
    """(kept grids, every grid including copies). Only tables read from the
    file itself: a table recovered from an image is a model transcription,
    not source text a descriptor can be checked against."""
    every: list[_Grid] = []
    kept: list[_Grid] = []
    for table in result.tables:
        if table.source != "native" or table.low_confidence:
            continue
        rows = _trimmed_rows(table.rows)
        if not _is_grid(rows, settings):
            continue
        grid = _Grid(block=table, rows=rows)
        every.append(grid)
        for earlier in kept:
            differing = _differing_cells(earlier.rows, rows, settings)
            if differing is not None:
                earlier.withheld |= differing
                grid.owner = earlier
                if table.page is not None:
                    earlier.copy_pages.append(table.page)
                break
        else:
            kept.append(grid)
    for number, grid in enumerate(kept, start=1):
        grid.number = number
    return kept, every


def _inside(block: TextBlock, table: TableBlock) -> bool:
    if block.page is None or block.bbox is None or table.page != block.page:
        return False
    if table.bbox is None:
        return False
    x0, y0, x1, y1 = block.bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    tx0, ty0, tx1, ty1 = table.bbox
    return tx0 <= cx <= tx1 and ty0 <= cy <= ty1


_TRAILING_PARTIAL_WORD = re.compile(r"[^\W_]+$")


def _common_prefix(a: str, b: str) -> str:
    """The shared label of two variants of one filled-in line ("Name: _" and
    "Name: _Cruz, J."), cut back to a word boundary."""
    size = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        size += 1
    prefix = a[:size]
    if size < min(len(a), len(b)):
        prefix = _TRAILING_PARTIAL_WORD.sub("", prefix)
    return prefix


@dataclass
class _Line:
    block: TextBlock
    text: str
    position: float
    original: str = ""
    key: str = ""
    # Length of the leading part still shown once a varying value is withheld.
    shown: int | None = None

    def __post_init__(self) -> None:
        self.original = self.text
        self.key = match_key(self.text)


def _merge_page_copies(
    lines: list[_Line], every: list[_Grid], settings: Settings
) -> tuple[list[_Line], list[str], int]:
    """Drop the text of pages that repeat an earlier page, and withhold the
    parts of the kept page that vary between the copies.

    A page counts as a copy only when every table on it repeats a table of the
    earlier page and its other text is close to that page's: a page that only
    shares a letterhead keeps its text. DOCX has no pages, so its paragraphs
    are never merged here (its repeated tables still are)."""
    grids_on: dict[int, set[int]] = {}
    for grid in every:
        if grid.block.page is not None:
            grids_on.setdefault(grid.block.page, set()).add(id(grid.owner or grid))
    pages: dict[int, list[_Line]] = {page: [] for page in grids_on}
    for line in lines:
        if line.block.page is None:
            return lines, [], 0
        pages.setdefault(line.block.page, []).append(line)
    marker = settings.rubric_withheld_value_marker
    kept_pages: list[int] = []
    dropped: set[int] = set()
    withheld: list[str] = []
    unread = 0
    for page in sorted(pages):
        current = pages[page]
        for earlier_page in kept_pages:
            earlier = pages[earlier_page]
            tables = grids_on.get(page, set())
            if not tables or not tables <= grids_on.get(earlier_page, set()):
                continue
            matcher = SequenceMatcher(
                None, [line.key for line in earlier], [line.key for line in current], autojunk=False
            )
            if (earlier or current) and matcher.ratio() < settings.rubric_copy_min_line_ratio:
                continue
            dropped.update(id(line) for line in current)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                kept_block, copy_block = earlier[i1:i2], current[j1:j2]
                withheld.extend(line.text for line in copy_block)
                pairs = _pairs(kept_block, copy_block)
                paired = {id(copy_line) for _, copy_line, _ in pairs}
                # A line found only on a copy may be a requirement the kept copy
                # lacks; it is not read, so the instructor is told it exists.
                unread += sum(1 for line in copy_block if id(line) not in paired)
                for kept_line, _, prefix in pairs:
                    if prefix == kept_line.original:
                        continue  # a field the kept copy left blank
                    if kept_line.shown is None or len(prefix) < kept_line.shown:
                        kept_line.shown = len(prefix)
                        withheld.append(kept_line.original[len(prefix) :])
                        kept_line.text = f"{prefix}{marker}" if prefix.strip() else marker
            break
        else:
            kept_pages.append(page)
    return [line for line in lines if id(line) not in dropped], withheld, unread


def _pairs(kept: list[_Line], copy: list[_Line]) -> list[tuple[_Line, _Line, str]]:
    """Which kept line each differing copy line is a variant of, with their
    shared leading label. Blocks of equal length pair line for line (a name
    on a line of its own differs completely); otherwise a copy line pairs with
    the kept line it shares the longest label with, or with none."""
    if len(kept) == len(copy):
        return [(k, c, _common_prefix(k.original, c.text)) for k, c in zip(kept, copy, strict=True)]
    pairs = []
    for copy_line in copy:
        best = max(
            kept, key=lambda k: len(_common_prefix(k.original, copy_line.text)), default=None
        )
        if best is None:
            continue
        prefix = _common_prefix(best.original, copy_line.text)
        if prefix.strip():
            pairs.append((best, copy_line, prefix))
    return pairs


def _grid_position(grid: _Grid, blocks: list[TextBlock], fragments: dict[int, int]) -> float:
    """Where the table sits in reading order: at its first fragment in the
    PDF's flattened text, or before the first paragraph after it in a DOCX."""
    if id(grid) in fragments:
        return fragments[id(grid)] - 0.5
    table = grid.block
    if table.paragraph is not None:
        for index, block in enumerate(blocks):
            if block.paragraph is not None and block.paragraph > table.paragraph:
                return index - 0.5
        return len(blocks) + 0.5
    if table.page is not None:
        for index, block in enumerate(blocks):
            if block.page is not None and block.page > table.page:
                return index - 0.5
    return len(blocks) + 0.5


def _render_grid(grid: _Grid, settings: Settings) -> list[str]:
    table = grid.block
    where = f"page {table.page}" if table.page is not None else "in the document body"
    repeats = (
        f"; the same table repeats on page(s) {', '.join(map(str, grid.copy_pages))}"
        if grid.copy_pages
        else ""
    )
    lines = [
        f"[TABLE T{grid.number}, {where}{repeats}: {len(grid.rows)} rows, "
        f"{len(grid.rows[0])} columns, each cell quoted exactly as extracted]"
    ]
    for r, row in enumerate(grid.rows):
        lines.append(f"T{grid.number} row {r + 1}:")
        for c, text in enumerate(row):
            ref = f"T{grid.number}.R{r + 1}.C{c + 1}"
            if (r, c) in grid.withheld:
                lines.append(f"  {ref}: {settings.rubric_withheld_value_marker}")
            elif text:
                lines.append(f"  {ref}: {text}")
            else:
                lines.append(f"  {ref}: (empty)")
    lines.append(f"[END OF TABLE T{grid.number}]")
    return lines


def build_decomposition_source(result: ExtractionResult, settings: Settings) -> DecompositionSource:
    """Structured input whenever the extraction recovered a table; otherwise
    exactly the flattened text the decomposer always read."""
    kept, every = _grids(result, settings)
    blocks = [b for b in result.blocks if not b.is_furniture and b.text.strip()]
    if not every:
        return DecompositionSource.from_text(
            list(result.blocks), raw_text_for_decomposition(result.blocks)
        )

    fragments: dict[int, int] = {}
    outside: list[_Line] = []
    # The kept tables' own lines as the extractor flattened them: a checklist
    # inside one cell keeps one line per item here, unlike the joined cell.
    table_lines: list[str] = []
    for index, block in enumerate(blocks):
        container = next((g for g in every if _inside(block, g.block)), None)
        if container is not None:
            fragments.setdefault(id(container.owner or container), index)
            if container.owner is None:
                table_lines.extend(t for t in block.text.splitlines() if t.strip())
            continue
        texts = [normalize(text) for text in block.text.splitlines()]
        texts = [text for text in texts if text]
        outside.extend(
            _Line(block=block, text=text, position=index + n / (len(texts) + 1))
            for n, text in enumerate(texts)
        )

    outside, withheld, unread = _merge_page_copies(outside, every, settings)
    for grid in kept:
        for r, c in sorted(grid.withheld):
            if grid.rows[r][c]:
                withheld.append(grid.rows[r][c])

    entries: list[tuple[float, list[str]]] = [(line.position, [line.text]) for line in outside]
    entries.extend((_grid_position(g, blocks, fragments), _render_grid(g, settings)) for g in kept)
    entries.sort(key=lambda entry: entry[0])
    prompt_lines = [text for _, chunk in entries for text in chunk]

    cells = tuple(
        GridCell(table=grid.number, row=r + 1, col=c + 1, text=text, page=grid.block.page)
        for grid in kept
        for r, row in enumerate(grid.rows)
        for c, text in enumerate(row)
        if text and (r, c) not in grid.withheld
    )
    gate_blocks = [line.block.model_copy(update={"text": line.text}) for line in outside]
    gate_blocks.extend(
        TextBlock(page=None, text=cell.text, max_font_size=0.0, bold_ratio=0.0) for cell in cells
    )
    # A DOCX table has no flattened lines (its text never reaches `blocks`).
    in_tables = table_lines or [cell.text for cell in cells]
    checklist = "\n".join([*(line.text for line in outside), *in_tables])
    return DecompositionSource(
        prompt_text="\n".join(prompt_lines),
        cells=cells,
        outside_lines=tuple(line.text for line in outside),
        gate_blocks=gate_blocks,
        checklist_text=checklist,
        withheld=tuple(dict.fromkeys(value for value in withheld if value.strip())),
        withheld_cells=tuple(
            (grid.number, r + 1, c + 1) for grid in kept for r, c in sorted(grid.withheld)
        ),
        copies_merged=len(every) - len(kept),
        unread_copy_lines=unread,
        copy_pages=tuple(
            sorted(
                {
                    page
                    for grid in kept
                    if grid.copy_pages
                    for page in (grid.block.page, *grid.copy_pages)
                    if page is not None
                }
            )
        ),
    )
