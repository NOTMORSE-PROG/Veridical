"""F1.1: order native region proposals without replacing manuscript text.

The native segmenter proposes a hierarchy, not a final reading sequence. Map
every proposed line back to the original dictionary inventory exactly. Furniture
classification and evidence coordinates remain those of the original extraction.
An unresolved page retains that source sequence and an explicit reason.
"""

import hashlib
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import pairwise
from statistics import median

import pymupdf

from app.config import Settings
from app.ingest.native_table_order import (
    NativeTableProposal,
    TableOrderUnresolved,
    order_native_tables,
)
from app.ingest.normalize import normalize
from app.ingest.schemas import Line, PageReadingOrder


@dataclass
class Region:
    children: list["Region"]
    source_ids: list[str]


@dataclass
class Proposal:
    root: Region
    baselines: dict[str, float]
    unsupported: set[str]
    inventory_hash: str | None
    problem: str | None = None
    table_proposal: NativeTableProposal | None = None


class UnresolvedOrder(ValueError):
    """The geometry does not support the current ordering policy."""


def _line_hash(line: dict) -> str:
    return hashlib.sha256(
        json.dumps(line, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def line_source_id(page: int, block: dict, block_no: int, line_no: int) -> str:
    return f"{page}:{block.get('number', block_no)}:{line_no}"


def propose_regions(
    page: pymupdf.Page, raw_blocks: list[dict], lines: list[Line], settings: Settings
) -> Proposal:
    """Native proposal, bounded before the second text extraction where possible.

    Limits bound this adapter's work, not the underlying PDF parser's initial
    decoding. Existing file/page/ingestion limits remain independent controls.
    """
    proposal = Proposal(Region([], []), {}, set(), None)
    if len(lines) > settings.ingest_reading_order_max_lines_per_page:
        proposal.problem = "line_budget_exhausted"
        return proposal
    original = {line.source_id: line for line in lines}
    identities: dict[str, deque[str]] = defaultdict(deque)
    inventory = []
    baselines = {}
    unsupported = set()
    try:
        for block_no, block in enumerate(raw_blocks):
            for line_no, raw in enumerate(block.get("lines", [])):
                sid = line_source_id(page.number + 1, block, block_no, line_no)
                if sid not in original:
                    continue
                digest = _line_hash(raw)
                inventory.append((sid, digest))
                identities[digest].append(sid)
                baselines[sid] = median(span["origin"][1] for span in raw["spans"])
                direction = raw.get("dir", ())
                eps = settings.ingest_reading_order_direction_tolerance
                if (
                    len(direction) != 2
                    or abs(direction[0] - 1) > eps
                    or abs(direction[1]) > eps
                    or raw.get("wmode") != 0
                ):
                    unsupported.add(sid)
        proposal.baselines = baselines
        proposal.unsupported = unsupported
        proposal.inventory_hash = hashlib.sha256(json.dumps(inventory).encode("utf-8")).hexdigest()
    except (ValueError, KeyError, TypeError, OverflowError):
        proposal.problem = "source_inventory_invalid"
        return proposal
    if len(original) != len(lines) or len(inventory) != len(lines):
        proposal.problem = "source_identity_mismatch"
        return proposal
    nodes_seen = 0

    def map_node(raw: dict, depth: int) -> Region:
        nonlocal nodes_seen
        nodes_seen += 1
        if nodes_seen > settings.ingest_reading_order_max_nodes_per_page:
            raise UnresolvedOrder("node_budget_exhausted")
        if depth > settings.ingest_reading_order_max_depth:
            raise UnresolvedOrder("depth_budget_exhausted")
        ids = []
        for line in raw.get("lines", []):
            if not normalize("".join(span["text"] for span in line.get("spans", []))):
                continue
            candidates = identities.get(_line_hash(line))
            if not candidates:
                raise UnresolvedOrder("segment_identity_mismatch")
            ids.append(candidates.popleft())
        children = [map_node(child, depth + 1) for child in raw.get("blocks", [])]
        if children and ids:
            raise UnresolvedOrder("mixed_structure_node")
        return Region(children, ids)

    try:
        flags = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES) | pymupdf.TEXT_SEGMENT
        segmented = page.get_text("dict", sort=False, flags=flags)
        proposal.root = Region([map_node(block, 1) for block in segmented["blocks"]], [])
        if any(remaining for remaining in identities.values()):
            raise UnresolvedOrder("segment_identity_mismatch")
    except UnresolvedOrder as exc:
        proposal.problem = str(exc)
    except (RuntimeError, ValueError, KeyError, TypeError, OverflowError):
        proposal.problem = "segment_unavailable"
    return proposal


def order_page(
    page: int, lines: list[Line], proposal: Proposal, *, has_tables: bool, settings: Settings
) -> tuple[list[Line], PageReadingOrder]:
    """Return a permutation of the original Line objects, or retain the source."""
    source = {line.source_id: line for line in lines}
    margin_blocks: set[int] = set()
    native_blocks: dict[int, list[Line]] = {}
    for line in lines:
        native_blocks.setdefault(line.block_no, []).append(line)
        if line.is_furniture:
            margin_blocks.add(line.block_no)
    # Existing raw stores exclude a block only when ALL its original lines are
    # furniture. A split output group must not newly remove part of a mixed
    # native block merely because the region proposer grouped it differently.
    # Keep the ENTIRE original mixed margin block atomic. Its original
    # canonical inclusion remains unchanged; do not inject its unmarked half
    # between columns or silently reclassify that half as removable furniture.
    body = {sid: line for sid, line in source.items() if line.block_no not in margin_blocks}
    excluded = {
        number
        for number, block in native_blocks.items()
        if all(line.is_furniture for line in block)
    }
    canonical = {sid: line for sid, line in source.items() if line.block_no not in excluded}
    eps = settings.ingest_reading_order_geometry_tolerance_pts
    row_eps = settings.ingest_reading_order_row_tolerance_pts

    def check_overlap() -> None:
        active: list[str] = []
        compared = 0
        # Position protection does not exempt mixed margin blocks: their text
        # remains canonical evidence under the original all-lines rule.
        for sid in sorted(canonical, key=lambda item: canonical[item].bbox[1]):
            line = canonical[sid]
            active = [other for other in active if canonical[other].bbox[3] > line.bbox[1] + eps]
            for other in active:
                compared += 1
                if compared > settings.ingest_reading_order_max_pair_comparisons:
                    raise UnresolvedOrder("pair_budget_exhausted")
                peer = canonical[other]
                width = min(line.bbox[2], peer.bbox[2]) - max(line.bbox[0], peer.bbox[0])
                gap = abs(proposal.baselines[sid] - proposal.baselines[other])
                minimum = (
                    min(line.max_size, peer.max_size)
                    * settings.ingest_reading_order_min_baseline_separation_em
                )
                if width > row_eps and gap + eps < minimum:
                    raise UnresolvedOrder("overlapping_text")
            active.append(sid)

    def row_order(ids: list[str]) -> list[str]:
        ordered = sorted(ids, key=lambda sid: (proposal.baselines[sid], body[sid].bbox[0]))
        rows: list[list[str]] = []
        for sid in ordered:
            if not rows or proposal.baselines[sid] - proposal.baselines[rows[-1][0]] > row_eps:
                rows.append([sid])
            else:
                rows[-1].append(sid)
        output = []
        for row in rows:
            row.sort(key=lambda sid: body[sid].bbox[0])
            for left, right in pairwise(row):
                if body[left].bbox[2] - body[right].bbox[0] > row_eps:
                    raise UnresolvedOrder("overlapping_text")
            output.extend(row)
        return output

    def intervals(ids: list[str], axis: str) -> tuple[float, float]:
        if axis == "x":
            return min(body[sid].bbox[0] for sid in ids), max(body[sid].bbox[2] for sid in ids)
        return min(proposal.baselines[sid] for sid in ids), max(
            proposal.baselines[sid] for sid in ids
        )

    def clusters(parts: list[list[str]], axis: str) -> list[list[list[str]]]:
        ordered = sorted(parts, key=lambda ids: intervals(ids, axis))
        result: list[list[list[str]]] = []
        end = -math.inf
        for ids in ordered:
            low, high = intervals(ids, axis)
            if not result or low > end + eps:
                result.append([ids])
                end = high
            else:
                result[-1].append(ids)
                end = max(end, high)
        return result

    def shared_vertical_extent(groups: list[list[list[str]]]) -> bool:
        ranges = [intervals([sid for part in group for sid in part], "y") for group in groups]
        return all(max(a[0], b[0]) <= min(a[1], b[1]) + row_eps for a, b in pairwise(ranges))

    def original_columns(ids: list[str], depth: int = 0) -> list[str] | None:
        if depth > settings.ingest_reading_order_max_depth:
            raise UnresolvedOrder("depth_budget_exhausted")
        groups = clusters([[sid] for sid in ids], "x")
        if len(groups) <= 1 or not shared_vertical_extent(groups):
            return None
        # A single floating heading in the gap between upper/lower bands is
        # not a third column. Let the native regional hierarchy separate those
        # bands; only override its grouping for multiple flowing columns.
        ranges = [intervals([sid for part in group for sid in part], "y") for group in groups]
        if any(high - low <= row_eps for low, high in ranges):
            return None
        ordered = []
        for group in groups:
            members = [sid for part in group for sid in part]
            nested = original_columns(members, depth + 1)
            ordered.extend(nested if nested is not None else row_order(members))
        return ordered

    def order_parts(parts: list[list[str]], *, leaves: bool, depth: int = 0) -> list[str]:
        if depth > settings.ingest_reading_order_max_depth:
            raise UnresolvedOrder("depth_budget_exhausted")
        if not parts:
            return []
        # Native nodes can group by rows, or collapse several columns into a
        # leaf. Inspect original line gutters before trusting either proposal.
        columns = original_columns([sid for part in parts for sid in part])
        if columns is not None:
            return columns
        if len(parts) == 1:
            return parts[0]
        # A complete vertical gutter establishes column order before y-order.
        # If no such gutter exists, separate top/bottom regions instead.
        for axis in ("x", "y"):
            groups = clusters(parts, axis)
            if len(groups) > 1 and (axis != "x" or shared_vertical_extent(groups)):
                return [
                    sid
                    for group in groups
                    for sid in order_parts(group, leaves=leaves, depth=depth + 1)
                ]
        if leaves:
            # Adjacent native text blocks can overlap by font ascenders or a
            # separately emitted TeX-logo glyph. Original line baselines and
            # x positions can order these without discarding their spans.
            return row_order([sid for part in parts for sid in part])
        # Nested region extents can overlap even when their original lines form
        # one unambiguous column. Require a common positive x intersection and
        # disjoint full source boxes in the actual returned order; do not infer
        # flow from aggregate region boxes or relax any earlier source guard.
        single_column_ids = [sid for part in parts for sid in part]
        if max(body[sid].bbox[0] for sid in single_column_ids) < min(
            body[sid].bbox[2] for sid in single_column_ids
        ):
            row_ids = row_order(single_column_ids)
            if all(body[left].bbox[3] <= body[right].bbox[1] for left, right in pairwise(row_ids)):
                return row_ids
        raise UnresolvedOrder("ambiguous_region_overlap")

    def visit(region: Region) -> list[str]:
        if not region.children:
            ids = [sid for sid in region.source_ids if sid in body]
            columns = original_columns(ids)
            return columns if columns is not None else row_order(ids)
        parts = [visit(child) for child in region.children]
        parts = [part for part in parts if part]
        return order_parts(parts, leaves=all(not child.children for child in region.children))

    problem = proposal.problem
    if not problem and has_tables and proposal.table_proposal is None:
        problem = "table_regions_unresolved"
    if not problem and proposal.unsupported.intersection(canonical):
        problem = "unsupported_text_direction"
    table_witness = None
    if not problem:
        try:
            check_overlap()
            if has_tables:

                def order_subset(subset):
                    ordered, record = order_page(
                        page, subset, proposal, has_tables=False, settings=settings
                    )
                    if record.status != "ordered":
                        raise TableOrderUnresolved(record.reason or "native_table_flow_unresolved")
                    return [line.source_id for line in ordered]

                ids, table_witness = order_native_tables(
                    list(body.values()),
                    proposal.table_proposal,
                    proposal.inventory_hash,
                    order_subset,
                    row_order,
                    settings,
                )
            else:
                ids = visit(proposal.root)
            if len(ids) != len(body) or set(ids) != set(body):
                raise UnresolvedOrder("ordered_inventory_mismatch")
            # Preserve marked furniture, outside the substantive flow, without
            # reclassifying it based on the segmenter's different grouping.
            top = min((line.bbox[1] for line in body.values()), default=math.inf)
            bottom = max((line.bbox[3] for line in body.values()), default=-math.inf)
            headers, footers = [], []
            for block_no, block in native_blocks.items():
                if block_no not in margin_blocks:
                    continue
                if max(line.bbox[3] for line in block) <= top + eps:
                    headers.extend(block)
                elif min(line.bbox[1] for line in block) >= bottom - eps:
                    footers.extend(block)
                else:
                    raise UnresolvedOrder("ambiguous_margin_block")
            ordered_lines = headers + [body[sid] for sid in ids] + footers
        except (UnresolvedOrder, TableOrderUnresolved, KeyError) as exc:
            problem = (
                str(exc)
                if isinstance(exc, (UnresolvedOrder, TableOrderUnresolved))
                else "source_identity_mismatch"
            )
    if problem:
        ordered_lines = lines
    return ordered_lines, PageReadingOrder(
        page=page,
        status="unresolved" if problem else "ordered",
        reason=problem,
        source_line_count=len(lines),
        source_inventory_sha256=proposal.inventory_hash,
        ordered_source_line_ids=[line.source_id for line in ordered_lines],
        table_order=table_witness if not problem else None,
    )
