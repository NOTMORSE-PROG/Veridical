"""Validated source scopes; omissions are discontinuities, never prose (F1-F7)."""

import json
from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from app.ingest.native_math_models import NativeMathInventory
from app.ingest.schemas import ExtractionResult, TableBlock, TextBlock


@dataclass(frozen=True)
class AnalyticalSource:
    state: Literal["complete", "not_recorded", "unavailable", "not_applicable"]
    inventory: NativeMathInventory | None
    scopes: tuple[tuple[TextBlock, ...], ...]
    tables: tuple[TableBlock, ...]
    excluded_paragraphs: tuple[int, ...]
    unlocated_occurrences: int = 0

    @property
    def occurrence_count(self) -> int | None:
        if self.state == "not_applicable":
            return 0
        return len(self.inventory.occurrences) if self.inventory is not None else None

    def scopes_for(self, blocks: list[TextBlock]) -> tuple[tuple[TextBlock, ...], ...]:
        """Intersect a source selection without reconnecting omitted containers."""
        if self.state == "not_applicable":
            return (tuple(blocks),) if blocks else ()
        by_identity = {}
        by_paragraph = {}
        selected = [[] for _scope in self.scopes]
        for index, scope in enumerate(self.scopes):
            for block in scope:
                by_identity[id(block)] = index
                if block.paragraph is not None:
                    by_paragraph[block.paragraph] = index
        for block in blocks:
            index = by_identity.get(id(block), by_paragraph.get(block.paragraph))
            if index is not None:
                selected[index].append(block)
        return tuple(tuple(scope) for scope in selected if scope)

    @property
    def limited(self) -> bool:
        return self.state in {"not_recorded", "unavailable"} or bool(self.occurrence_count)

    def detail(self) -> dict:
        return {
            "native_math_coverage_version": "v1",
            "native_math_inventory_state": self.state,
            "native_math_inventory_scope": "main_document_body" if self.inventory else None,
            "n_native_equations_unassessed": self.occurrence_count,
            "n_native_equations_unlocated": self.unlocated_occurrences if self.inventory else None,
            "native_math_excluded_paragraphs": list(self.excluded_paragraphs),
            "native_math_coverage_items": [
                {
                    "paragraph": container.paragraph,
                    "kind": container.kind,
                    "n_occurrences": sum(p.kind == "native_math" for p in container.pieces),
                }
                for container in self.inventory.containers
            ]
            if self.inventory
            else [],
        }

    def context(self, blocks: list[TextBlock]) -> str:
        """Structured task input; disclosure metadata is never manuscript text."""
        scopes = self.scopes_for(blocks)
        if not self.limited:
            return "\n".join(b.text for scope in scopes for b in scope if not b.is_furniture)
        return json.dumps(
            {
                "source_coverage": self.detail(),
                "source_limitation": (
                    "Native Word formulas and affected containers are unavailable. "
                    "The separate scopes are not continuous quotations. Omitted material "
                    "cannot establish absence, poor quality or fulfilment of an obligation. "
                    "Assess only independently supported evidence; retain uncertainty otherwise."
                ),
                "contiguous_source_scopes": [
                    [
                        {"paragraph": b.paragraph, "text": b.text}
                        for b in scope
                        if not b.is_furniture
                    ]
                    for scope in scopes
                ],
            },
            ensure_ascii=False,
        )


def analytical_source(
    extraction: ExtractionResult, *, expected_source_sha256: str | None = None
) -> AnalyticalSource:
    if extraction.anchor_kind != "paragraph":
        return AnalyticalSource(
            "not_applicable", None, (tuple(extraction.blocks),), tuple(extraction.tables), ()
        )
    envelope = extraction.native_math_inventory
    if envelope is None:
        return AnalyticalSource("not_recorded", None, (), (), ())
    try:
        inventory = NativeMathInventory.model_validate(
            envelope.model_dump() if isinstance(envelope, NativeMathInventory) else envelope
        )
    except (ValidationError, TypeError, ValueError):
        return AnalyticalSource("unavailable", None, (), (), ())
    expected = (
        expected_source_sha256
        if expected_source_sha256 is not None
        else extraction.analytical_source_sha256
        if extraction.analytical_source_sha256 is not None
        else extraction.docx_source_sha256
    )
    if (
        not expected
        or inventory.source_sha256 != expected
        or inventory.source_sha256 != extraction.docx_source_sha256
    ):
        return AnalyticalSource("unavailable", None, (), (), ())
    excluded = tuple(sorted({c.paragraph for c in inventory.containers if c.paragraph is not None}))
    unlocated = sum(
        len([piece for piece in c.pieces if piece.kind == "native_math"])
        for c in inventory.containers
        if c.paragraph is None
    )
    # A retained XML path without a reconstructed body ordinal remains a
    # known occurrence, but cannot certify which ordinary strings are safe.
    if unlocated:
        return AnalyticalSource("complete", inventory, (), (), excluded, unlocated)
    scopes = []
    excluded_set = set(excluded)
    current = []
    previous = None
    for block in extraction.blocks:
        position = block.paragraph
        if position in excluded_set:
            if current:
                scopes.append(tuple(current))
                current = []
            previous = position
            continue
        if (
            previous is not None
            and position is not None
            and bisect_right(excluded, previous) < bisect_right(excluded, position - 1)
            and current
        ):
            scopes.append(tuple(current))
            current = []
        current.append(block)
        previous = position
    if current:
        scopes.append(tuple(current))
    # Table-level fencing prevents incomplete denominators/header/group
    # membership from supporting aggregate findings. Other tables survive.
    tables = tuple(table for table in extraction.tables if table.paragraph not in excluded_set)
    return AnalyticalSource("complete", inventory, tuple(scopes), tables, excluded)


def bind_analytical_source(
    extraction: ExtractionResult, source_sha256: str | None
) -> ExtractionResult:
    """Bind new analysis to its manuscript row without rewriting saved history."""
    return extraction.model_copy(update={"analytical_source_sha256": source_sha256 or ""})


def section_order_source_complete(extraction: ExtractionResult) -> bool:
    source = analytical_source(extraction)
    if source.state == "not_applicable":
        return True
    return (
        source.state == "complete"
        and not source.unlocated_occurrences
        and all(container.kind == "table_cell" for container in source.inventory.containers)
    )
