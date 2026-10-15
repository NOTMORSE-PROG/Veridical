"""Exact source projections; no re-extraction, text repair or estimated geometry."""

import math
from bisect import bisect_left, bisect_right

from app.ingest.schemas import TextBlock


class SourceFragment(TextBlock):
    """Transient view into result.blocks; these fields are never persisted as source."""

    source_block_index: int
    source_start: int
    source_end: int


def line_inventory_valid(block: TextBlock) -> bool:
    """Unknown legacy lines are not assessed; present but invalid lines are rejected."""
    if block.source_lines is None:
        return True
    expected = []
    offset = 0
    for line in block.text.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        if text:
            expected.append((offset, offset + len(text)))
        offset += len(line)
    lines = block.source_lines
    if [(line.start, line.end) for line in lines] != expected:
        return False
    ids = [line.source_id for line in lines]
    if block.source_line_ids is not None and ids != block.source_line_ids:
        return False
    known_ids = [value for value in ids if value is not None]
    if len(known_ids) != len(set(known_ids)):
        return False
    parent = block.bbox
    if parent is not None and (
        not all(math.isfinite(v) for v in parent)
        or parent[0] >= parent[2]
        or parent[1] >= parent[3]
    ):
        return False
    return all(
        all(math.isfinite(v) for v in (*line.bbox, line.max_font_size, line.bold_ratio))
        and line.bbox[0] < line.bbox[2]
        and line.bbox[1] < line.bbox[3]
        and (
            parent is None
            or (
                parent[0] <= line.bbox[0] < line.bbox[2] <= parent[2]
                and parent[1] <= line.bbox[1] < line.bbox[3] <= parent[3]
            )
        )
        for line in lines
    )


def slice_block(block: TextBlock, start: int, end: int) -> TextBlock:
    """Slice exact characters, retaining rectangles only for evidenced whole lines."""
    return SourceSlicer(block).slice(start, end)


class SourceSlicer:
    """Validate once for a local slicing operation, then index only selected lines.

    No global cache: the source is not mutated during this operation, and a later
    operation revalidates it. Many entries in one native block stay near-linear.
    """

    def __init__(self, block: TextBlock):
        self.block = block
        self.lines = block.source_lines if line_inventory_valid(block) else None
        self.starts = [line.start for line in self.lines or []]
        self.ends = [line.end for line in self.lines or []]

    def slice(self, start: int, end: int) -> TextBlock:
        block = self.block
        if not 0 <= start < end <= len(block.text):
            raise ValueError("Source slice is outside its block")
        if start == 0 and end == len(block.text):
            return block
        text = block.text[start:end]
        update = {"text": text, "bbox": None, "source_lines": None, "source_line_ids": None}
        if isinstance(block, SourceFragment):
            update.update(
                source_start=block.source_start + start, source_end=block.source_start + end
            )
        lower = bisect_left(self.starts, start)
        upper = bisect_right(self.ends, end)
        lines = (self.lines or [])[lower:upper]
        # A substring of an individual line has no exact rectangle. Do not
        # borrow the whole line's box, even when other complete lines remain.
        covered = "".join(block.text[line.start : line.end] for line in lines)
        if lines and "".join(text.split()) == "".join(covered.split()):
            update.update(
                bbox=(
                    min(line.bbox[0] for line in lines),
                    min(line.bbox[1] for line in lines),
                    max(line.bbox[2] for line in lines),
                    max(line.bbox[3] for line in lines),
                ),
                source_lines=[
                    line.model_copy(update={"start": line.start - start, "end": line.end - start})
                    for line in lines
                ],
                source_line_ids=[line.source_id for line in lines]
                if all(line.source_id is not None for line in lines)
                else None,
                max_font_size=max(line.max_font_size for line in lines),
                bold_ratio=sum(line.bold_ratio * (line.end - line.start) for line in lines)
                / sum(line.end - line.start for line in lines),
                is_furniture=all(line.is_furniture for line in lines),
            )
        return block.model_copy(update=update)
