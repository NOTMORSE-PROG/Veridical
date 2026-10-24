"""Exact, nonmutating print grouping of already projected native inventories."""

from dataclasses import dataclass, replace

from app.report.schemas import IntegrityCheckStatusOut


@dataclass(frozen=True)
class NativeSourceNote:
    number: int
    checks: tuple[str, ...]
    state: str | None
    count: int | None
    unlocated: int | None
    excluded: tuple[int, ...]
    items: tuple[tuple[str, int | None, int], ...]


def group_native_source_notes(
    coverages: list[IntegrityCheckStatusOut],
) -> tuple[list[NativeSourceNote], list[int | None]]:
    """Share known identical tuples only; unknown inventories retain separate notes.

    Item order and duplicate occurrences are part of identity. This projection
    neither sums counts between checks nor establishes source coverage itself.
    """
    notes: list[NativeSourceNote] = []
    references: list[int | None] = []
    known: dict[tuple, int] = {}
    for coverage in coverages:
        state = coverage.native_math_inventory_state
        count = coverage.n_native_equations_unassessed
        unlocated = coverage.n_native_equations_unlocated
        excluded = tuple(coverage.native_math_excluded_paragraphs)
        items = tuple(
            (item.kind, item.paragraph, item.n_occurrences)
            for item in coverage.native_math_coverage_items
        )
        if not (
            state in {"not_recorded", "unavailable"} or count or unlocated or excluded or items
        ):
            references.append(None)
            continue
        identity = (state, count, unlocated, excluded, items)
        shareable = state == "complete" and count is not None and unlocated is not None
        index = known.get(identity) if shareable else None
        if index is None:
            index = len(notes)
            notes.append(
                NativeSourceNote(
                    index + 1, (coverage.check_kind,), state, count, unlocated, excluded, items
                )
            )
            if shareable:
                known[identity] = index
        elif coverage.check_kind not in notes[index].checks:
            notes[index] = replace(notes[index], checks=(*notes[index].checks, coverage.check_kind))
        references.append(notes[index].number)
    return notes, references
