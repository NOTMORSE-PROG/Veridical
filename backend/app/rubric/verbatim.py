"""Source check for drafted level descriptors (F2.2, BUG-566).

A level descriptor is the definition a manuscript is graded against, so it has
to be the document's own words: one table cell, or one passage of the text
outside tables. The model is asked to copy it; this module checks that it did.
A descriptor that matches a cell is replaced by that cell's exact text, so
what is stored is the source and not the model's retyping of it. A descriptor
that matches nothing, joins two cells, or keeps only part of one is reported
as an issue: the rubric is then not plain "parsed", and the instructor sees
why before activating it.

Comparison ignores case, spacing, punctuation, soft hyphens and compatibility
forms (ligatures), because extraction and retyping disagree on exactly those
("a n academic", curly versus straight apostrophes, "in-depth" broken across
two lines). Words themselves must match.

Within a table, every level of one criterion must come from one row (or one
column, for a table laid out the other way), in order, without reusing a cell
another criterion already took, and without skipping a level cell of that row.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.config import Settings
from app.rubric.schemas import ParsedCriterion, ParsedLevel
from app.rubric.source import DecompositionSource, GridCell

_NON_TEXT = re.compile(r"[\W_]+")
_SOFT_HYPHEN = "­"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[^\W\d_]{4,}")
_UNIT_SEPARATOR = "|"


def match_form(text: str) -> str:
    """Letters and digits only, casefolded, compatibility-normalized."""
    text = unicodedata.normalize("NFKC", text).replace(_SOFT_HYPHEN, "")
    return _NON_TEXT.sub("", text.casefold())


def _words(text: str) -> set[str]:
    return set(_WORD.findall(unicodedata.normalize("NFKC", text).casefold()))


def _excerpt(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


@dataclass(frozen=True)
class _Corpus:
    cells_by_form: dict[str, list[GridCell]]
    cell_forms: tuple[str, ...]
    outside_form: str
    joined: str

    @classmethod
    def of(cls, source: DecompositionSource) -> _Corpus:
        by_form: dict[str, list[GridCell]] = {}
        for cell in source.cells:
            by_form.setdefault(match_form(cell.text), []).append(cell)
        cell_forms = tuple(by_form)
        # Lines outside tables are one running text: a passage may wrap across
        # lines. Cells are separate units: nothing may run from one into the next.
        outside = "".join(match_form(line) for line in source.outside_lines)
        return cls(by_form, cell_forms, outside, _UNIT_SEPARATOR.join((outside, *cell_forms)))


def _problem(descriptor: str, form: str, corpus: _Corpus) -> str:
    if any(form in cell and form != cell for cell in corpus.cell_forms):
        return "keeps only part of the table cell it comes from"
    sentences = [match_form(s) for s in _SENTENCE_END.split(descriptor)]
    sentences = [s for s in sentences if s]
    if len(sentences) > 1 and all(s in corpus.joined for s in sentences):
        where = (
            "more than one table cell" if corpus.cell_forms else "different parts of the document"
        )
        return f"joins text from {where}"
    return "contains wording that is not in the original document"


def _placements(candidates: list[list[GridCell]]) -> list[GridCell] | None:
    """One cell per level, all on one row (or one column), strictly in order
    left to right or right to left; None when the levels cannot be placed that
    way. Usually each level matches exactly one cell; identical cells (a
    repeated "N/A", a merged cell read twice) are the only ambiguity."""
    for axis, step in (("row", "col"), ("col", "row")):
        shared = set.intersection(
            *({(cell.table, getattr(cell, axis)) for cell in options} for options in candidates)
        )
        for line in sorted(shared):
            per_level = [
                sorted(
                    (c for c in options if (c.table, getattr(c, axis)) == line),
                    key=lambda c: getattr(c, step),
                )
                for options in candidates
            ]
            for descending in (False, True):
                placed = _in_order(per_level, step, descending=descending)
                if placed is not None:
                    return placed
    return None


def _in_order(
    per_level: list[list[GridCell]], step: str, *, descending: bool
) -> list[GridCell] | None:
    placed: list[GridCell] = []
    for options in per_level:
        ordered = options[::-1] if descending else options
        last = getattr(placed[-1], step) if placed else None
        pick = next(
            (
                c
                for c in ordered
                if last is None
                or (getattr(c, step) < last if descending else getattr(c, step) > last)
            ),
            None,
        )
        if pick is None:
            return None
        placed.append(pick)
    return placed


def verify_against_source(
    criteria: list[ParsedCriterion], source: DecompositionSource, settings: Settings
) -> tuple[list[ParsedCriterion], list[str]]:
    """Return the criteria with table-cell descriptors replaced by the cells'
    exact text, and one issue per descriptor or row that cannot be traced to
    the source. Criteria without levels pass through unchanged."""
    corpus = _Corpus.of(source)
    limit = settings.rubric_feedback_criterion_max_chars
    cell_text = {(c.table, c.row, c.col): c.text for c in source.cells}
    issues: list[str] = []
    used: dict[tuple[int, int, int], str] = {}
    lines: list[tuple[ParsedCriterion, list[GridCell]]] = []
    verified: list[ParsedCriterion] = []

    for criterion in criteria:
        if not criterion.levels:
            verified.append(criterion)
            continue
        label = _excerpt(criterion.text, limit)
        candidates: list[list[GridCell]] = []
        levels: list[ParsedLevel] = []
        from_text = failed = 0
        for level in criterion.levels:
            form = match_form(level.descriptor)
            if form and form in corpus.cells_by_form:
                candidates.append(corpus.cells_by_form[form])
            elif form and form in corpus.outside_form:
                from_text += 1
            else:
                failed += 1
                issues.append(
                    f"'{label}' level '{level.name}': the descriptor "
                    f"{_problem(level.descriptor, form, corpus)}; compare it with the "
                    "original document"
                )
            levels.append(level)

        if candidates and not failed:
            placed = None if from_text else _placements(candidates)
            if placed is None:
                issues.append(
                    f"'{label}': its levels do not come from one row of the table, in "
                    "order; compare them with the original document"
                )
            else:
                cells = iter(placed)
                levels = [
                    level.model_copy(update={"descriptor": next(cells).text}) for level in levels
                ]
                for cell in placed:
                    key = (cell.table, cell.row, cell.col)
                    if key in used:
                        issues.append(
                            f"'{label}' uses the same table cell as "
                            f"'{_excerpt(used[key], limit)}'; compare them with the original "
                            "document"
                        )
                    used.setdefault(key, criterion.text)
                lines.append((criterion, placed))
        verified.append(criterion.model_copy(update={"levels": levels}))

    issues.extend(_row_issues(lines, cell_text, limit))
    return verified, issues


def _axes(placed: list[GridCell]) -> tuple[str, str]:
    """("row", "col") when the levels run along a row, else ("col", "row")."""
    if len({cell.row for cell in placed}) == 1:
        return "row", "col"
    return "col", "row"


def _row_issues(
    lines: list[tuple[ParsedCriterion, list[GridCell]]],
    cell_text: dict[tuple[int, int, int], str],
    limit: int,
) -> list[str]:
    """Per table row: no level cell left out, and the criterion names its own
    row. The level columns of a table are the ones any criterion's levels came
    from; every other non-empty cell of a row is that row's label."""
    level_steps: dict[tuple[int, str], set[int]] = {}
    for _, placed in lines:
        axis, step = _axes(placed)
        level_steps.setdefault((placed[0].table, axis), set()).update(
            getattr(cell, step) for cell in placed
        )
    issues: list[str] = []
    for criterion, placed in lines:
        axis, step = _axes(placed)
        table, index = placed[0].table, getattr(placed[0], axis)
        row = {
            (key[2] if axis == "row" else key[1]): text
            for key, text in cell_text.items()
            if key[0] == table and (key[1] if axis == "row" else key[2]) == index
        }
        levels = level_steps[(table, axis)]
        own = {getattr(cell, step) for cell in placed}
        label = _excerpt(criterion.text, limit)
        if any(row.get(s) for s in levels - own):
            issues.append(
                f"'{label}': its table row has text under a level that was left out; "
                "compare it with the original document"
            )
        row_label = " ".join(text for s, text in sorted(row.items()) if s not in levels)
        names = _words(row_label)
        if names and not names & _words(f"{criterion.text} {criterion.evidence_needed or ''}"):
            issues.append(
                f"'{label}' is described with the levels of the table row "
                f"'{_excerpt(row_label, limit)}'; compare it with the original document"
            )
    return issues
