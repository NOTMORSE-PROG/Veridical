"""Source check for drafted level descriptors (F2.2, BUG-566).

A level descriptor is the definition a manuscript is graded against, so it has
to be the document's own words: one table cell, or one passage of the text
outside tables. The model is asked to copy it; this module checks that it did.
A descriptor that matches a cell is replaced by that cell's exact text, so
what is stored is the source and not the model's retyping of it, and every
level records where it came from (`ParsedLevel.source`). Anything else becomes
a `SourceIssue`: the rubric is then not plain "parsed", and the instructor sees
why before activating it.

Matching a whole cell ignores case, spacing, punctuation, soft hyphens and
compatibility forms (ligatures), because extraction and retyping disagree on
exactly those ("a n academic", curly versus straight apostrophes). Equality
with a whole cell is required, so nothing can be dropped from or added to it.
A passage outside tables is matched word by word and must start and end at a
sentence or clause boundary: "Poor" is not a passage of "Poorly", and the start
of a sentence is not a passage of the whole sentence.

Within a table, the levels of one criterion must lie on one row (or column, for
a table laid out the other way), in the same direction as the other rows, under
column headers whose points and names fit them, without skipping a level cell
of that row, and on the row whose label the criterion names. Several criteria
may share one row only when it is a scale legend none of them is named for.

Without a recovered table only passage fidelity can be checked: which row a
passage belonged to is not recoverable from flattened text, so a borderless
grid is guarded less than a detected one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from app.config import Settings
from app.rubric.schemas import LevelSource, ParsedCriterion, ParsedLevel
from app.rubric.source import DecompositionSource, GridCell

# The review screen's vocabulary. "not_one_row": the levels as a whole are
# not one row in order (no single level can be blamed). "wrong_row": the
# levels' row is labelled for another criterion, or (with a level) that
# level's cell is in another criterion's row. "wrong_column": a level's cell is
# in the right row but under another level's column. "other": text that
# differs between repeated copies (and, in validate, every whole-draft note).
IssueCode = Literal[
    "not_in_source",
    "partial_cell",
    "joins_cells",
    "not_one_row",
    "wrong_row",
    "wrong_column",
    "shared_cell",
    "level_left_out",
    "other",
]

_NON_TEXT = re.compile(r"[\W_]+")
_TOKEN = re.compile(r"[^\W_]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SOFT_HYPHEN = "­"
_LINE_BREAK_HYPHEN = re.compile(r"-\s*\n\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_UNIT_SEPARATOR = "|"
# Characters that sit inside a clause rather than end one.
_JOINERS = set("'’-‐‑")


@dataclass(frozen=True)
class SourceIssue:
    """One problem tracing a drafted criterion to the document. `criterion` is
    the criterion's position in the draft, `level` the level's ordinal (None
    when the issue is about the whole criterion)."""

    code: IssueCode
    criterion: int
    level: int | None
    message: str


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace(_SOFT_HYPHEN, "").casefold()


def match_form(text: str) -> str:
    """Letters and digits only, casefolded, compatibility-normalized."""
    return _NON_TEXT.sub("", _nfkc(text))


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(_nfkc(_LINE_BREAK_HYPHEN.sub("", text)))


def _excerpt(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


class _Passages:
    """The text outside tables as one run of words, with where a passage may
    start and end. A line break is a start boundary; it is an end boundary only
    when the next line does not continue the sentence (starts with a capital,
    a digit or a mark), since flattened text wraps sentences mid-clause."""

    def __init__(self, lines: tuple[str, ...]) -> None:
        text = _LINE_BREAK_HYPHEN.sub("", "\n".join(lines))
        self.words: list[str] = []
        self.can_start: list[bool] = []
        self.can_end: list[bool] = []
        lines_of_text = text.split("\n")
        # The next non-blank line after each line, found in one backward pass.
        next_lines = [""] * len(lines_of_text)
        upcoming = ""
        for index in range(len(lines_of_text) - 1, -1, -1):
            next_lines[index] = upcoming
            if lines_of_text[index].strip():
                upcoming = lines_of_text[index].strip()
        for line_index, line in enumerate(lines_of_text):
            matches = list(_TOKEN.finditer(line))
            next_line = next_lines[line_index]
            line_ends = not next_line or not next_line[0].islower()
            for i, match in enumerate(matches):
                before = line[matches[i - 1].end() : match.start()] if i else ""
                after = line[match.end() : matches[i + 1].start()] if i + 1 < len(matches) else ""
                self.words.append(_nfkc(match.group()))
                self.can_start.append(i == 0 or _is_boundary(before))
                last = i + 1 == len(matches)
                self.can_end.append(
                    _is_boundary(after)
                    or (last and (line_ends or _is_boundary(line[match.end() :])))
                )

    def find(self, words: list[str]) -> list[int]:
        size = len(words)
        if not size:
            return []
        return [
            i
            for i in range(len(self.words) - size + 1)
            if self.words[i : i + size] == words
            and self.can_start[i]
            and self.can_end[i + size - 1]
        ]


def _is_boundary(gap: str) -> bool:
    return any(not ch.isspace() and ch not in _JOINERS for ch in gap)


@dataclass
class _Corpus:
    cells_by_form: dict[str, list[GridCell]]
    cell_forms: tuple[str, ...]
    withheld_forms: tuple[str, ...]
    passages: _Passages
    joined: str

    @classmethod
    def of(cls, source: DecompositionSource, settings: Settings) -> _Corpus:
        by_form: dict[str, list[GridCell]] = {}
        for cell in source.cells:
            by_form.setdefault(match_form(cell.text), []).append(cell)
        outside = "".join(match_form(line) for line in source.outside_lines)
        withheld = tuple(
            form
            for form in (
                *(match_form(value) for value in source.withheld),
                match_form(settings.rubric_withheld_value_marker),
            )
            if form
        )
        return cls(
            by_form,
            tuple(by_form),
            withheld,
            _Passages(source.outside_lines),
            _UNIT_SEPARATOR.join((outside, *by_form)),
        )


_PROBLEMS: dict[IssueCode, str] = {
    "other": (
        "comes from text that differs between the repeated copies of this table, "
        "which VERIDICAL did not read"
    ),
    "partial_cell": "keeps only part of the table cell it comes from",
    "joins_cells": "joins text from more than one place in the original document",
    "not_in_source": "contains wording that is not in the original document",
}


def _problem(descriptor: str, form: str, corpus: _Corpus) -> IssueCode:
    if any(form and form in value for value in corpus.withheld_forms):
        return "other"
    if any(form in cell and form != cell for cell in corpus.cell_forms):
        return "partial_cell"
    sentences = [match_form(s) for s in _SENTENCE_END.split(descriptor)]
    sentences = [s for s in sentences if s]
    if len(sentences) > 1 and all(s in corpus.joined for s in sentences):
        return "joins_cells"
    return "not_in_source"


@dataclass(eq=False)
class _Placed:
    index: int
    criterion: ParsedCriterion
    cells: list[GridCell]
    axis: str  # "row": the levels run along a row; "col": down a column
    step: str  # the other coordinate

    @property
    def table(self) -> int:
        return self.cells[0].table

    @property
    def line(self) -> int:
        return getattr(self.cells[0], self.axis)

    @property
    def steps(self) -> list[int]:
        return [getattr(cell, self.step) for cell in self.cells]

    @property
    def descending(self) -> bool:
        return self.steps[0] > self.steps[-1]


def _orderings(candidates: list[list[GridCell]]) -> list[tuple[str, str, list[GridCell]]]:
    """Every way to place one cell per level on a single row or column,
    strictly in order left to right or right to left."""
    found: list[tuple[str, str, list[GridCell]]] = []
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
                    found.append((axis, step, placed))
    return found


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


@dataclass
class _Grid:
    """Cell text by coordinate, for the row checks."""

    text: dict[tuple[int, int, int], str]
    withheld: set[tuple[int, int, int]]
    word_min: int

    def at(self, table: int, axis: str, line: int, step: int) -> str:
        key = (table, line, step) if axis == "row" else (table, step, line)
        return self.text.get(key, "")

    def is_withheld(self, table: int, axis: str, line: int, step: int) -> bool:
        key = (table, line, step) if axis == "row" else (table, step, line)
        return key in self.withheld

    def lines(self, table: int, axis: str) -> set[int]:
        position = 1 if axis == "row" else 2
        return {key[position] for key in self.text if key[0] == table}

    def steps(self, table: int, axis: str) -> set[int]:
        position = 2 if axis == "row" else 1
        return {key[position] for key in self.text if key[0] == table}

    def words(self, text: str) -> set[str]:
        return {word for word in _tokens(text) if len(word) >= self.word_min and word.isalpha()}

    def label(self, table: int, axis: str, line: int, level_steps: set[int]) -> str:
        return " ".join(
            self.at(table, axis, line, s)
            for s in sorted(self.steps(table, axis))
            if s not in level_steps and self.at(table, axis, line, s)
        )

    def fit(self, text: str, label: str) -> tuple[float, int]:
        """How well a criterion's text names a row label: (share of the
        label's words it uses, number of them)."""
        label_words = self.words(label)
        if not label_words:
            return (0.0, 0)
        shared = len(label_words & self.words(text))
        return (shared / len(label_words), shared)


@dataclass
class _Check:
    settings: Settings
    issues: list[SourceIssue] = field(default_factory=list)

    def add(self, code: IssueCode, criterion: int, level: int | None, message: str) -> None:
        issue = SourceIssue(code, criterion, level, message)
        if issue not in self.issues:
            self.issues.append(issue)


def verify_against_source(
    criteria: list[ParsedCriterion], source: DecompositionSource, settings: Settings
) -> tuple[list[ParsedCriterion], list[SourceIssue]]:
    """Return the criteria with each level's descriptor replaced by its cell's
    exact text where one was found, every level's provenance recorded, and
    the issues found. Criteria without levels pass through unchanged."""
    corpus = _Corpus.of(source, settings)
    limit = settings.rubric_feedback_criterion_max_chars
    grid = _Grid(
        {(c.table, c.row, c.col): c.text for c in source.cells},
        set(source.withheld_cells),
        settings.rubric_label_word_min_chars,
    )
    check = _Check(settings)
    verified: list[ParsedCriterion] = list(criteria)
    pending: list[tuple[int, list[tuple[str, str, list[GridCell]]]]] = []
    # Criteria whose levels could not be placed as one row: each level that is
    # still exactly one cell keeps that cell as its source, and is checked on
    # its own once the table's usual level columns are known.
    strays: list[tuple[int, list[list[GridCell] | None], bool]] = []

    for index, criterion in enumerate(criteria):
        if not criterion.levels:
            continue
        label = _excerpt(criterion.text, limit)
        candidates: list[list[GridCell]] = []
        passages: list[list[int]] = []
        failed = False
        for level in criterion.levels:
            form = match_form(level.descriptor)
            if form and form in corpus.cells_by_form:
                candidates.append(corpus.cells_by_form[form])
                continue
            starts = corpus.passages.find(_tokens(level.descriptor))
            if starts:
                passages.append(starts)
                continue
            failed = True
            code = _problem(level.descriptor, form, corpus)
            check.add(
                code,
                index,
                level.level,
                f"'{label}' level '{level.name}': the descriptor {_PROBLEMS[code]}; "
                "compare it with the original document",
            )
        verified[index] = _with_sources(criterion, None)
        if failed:
            strays.append((index, _level_cells(criterion, corpus), True))
            continue
        if candidates and passages:
            check.add(
                "not_one_row",
                index,
                None,
                f"'{label}': some of its levels come from a table and some from other text; "
                "compare them with the original document",
            )
        elif passages:
            verified[index] = _with_sources(criterion, "exact")
            if not _monotonic(passages):
                check.add(
                    "not_one_row",
                    index,
                    None,
                    f"'{label}': its levels are not in the order the document gives them; "
                    "compare them with the original document",
                )
        else:
            options = _orderings(candidates)
            if not options:
                strays.append((index, _level_cells(criterion, corpus), False))
            else:
                pending.append((index, options))

    placed = _assign(pending, criteria, grid)
    for item, (row_label, headings) in _context(placed, grid).items():
        criterion = criteria[item.index]
        verified[item.index] = criterion.model_copy(
            update={
                "levels": [
                    _exact(level, cell, row_label, heading)
                    for level, cell, heading in zip(
                        criterion.levels or [], item.cells, headings, strict=True
                    )
                ]
            }
        )
    typical = _row_checks(placed, grid, check, limit)
    for index, cells, failed in strays:
        verified[index] = _place_strays(
            index, verified[index], cells, failed, grid, typical, check, limit
        )
    return verified, check.issues


def _level_cells(criterion: ParsedCriterion, corpus: _Corpus) -> list[list[GridCell] | None]:
    return [
        corpus.cells_by_form.get(match_form(level.descriptor)) for level in criterion.levels or []
    ]


def _place_strays(
    index: int,
    criterion: ParsedCriterion,
    cells: list[list[GridCell] | None],
    failed: bool,
    grid: _Grid,
    typical: dict[tuple[int, str], dict[int, tuple[int, ...]]],
    check: _Check,
    limit: int,
) -> ParsedCriterion:
    """Give every level that is exactly one cell that cell as its source, and
    raise an issue for each such level that is not where its level belongs:
    in another row ("wrong_row") or under another level's column
    ("wrong_column"). Its row is the one most of its cells share, the one its
    text names on a tie."""
    found = [options for options in cells if options]
    if not found:
        return criterion
    label = _excerpt(criterion.text, limit)
    lines: dict[tuple[int, str, int], int] = {}
    for options in found:
        for key in {(c.table, axis, getattr(c, axis)) for c in options for axis in ("row", "col")}:
            lines[key] = lines.get(key, 0) + 1

    def rank(key: tuple[int, str, int]) -> tuple:
        table, axis, line = key
        steps = {getattr(c, "col" if axis == "row" else "row") for o in found for c in o}
        fit = grid.fit(criterion.text, grid.label(table, axis, line, steps))
        return (lines[key], axis == "row", fit)

    table, axis, line = max(lines, key=rank)
    step_name = "col" if axis == "row" else "row"
    expected = typical.get((table, axis), {}).get(len(criterion.levels or []))
    used = {getattr(c, step_name) for o in found for c in o if getattr(c, axis) == line}
    level_steps = set(expected) if expected else used
    header = min(grid.lines(table, axis))
    levels = list(criterion.levels or [])
    positional = 0
    for position, (level, options) in enumerate(zip(levels, cells, strict=True)):
        if not options:
            continue
        on_line = [c for c in options if c.table == table and getattr(c, axis) == line]
        cell = on_line[0] if on_line else options[0]
        cell_line = getattr(cell, axis if cell.table == table else "row")
        row_label = grid.label(cell.table, axis, cell_line, level_steps)
        heading = grid.at(cell.table, axis, header, getattr(cell, step_name)) or None
        levels[position] = _exact(level, cell, row_label, heading)
        if not on_line:
            positional += 1
            check.add(
                "wrong_row",
                index,
                level.level,
                f"'{label}' level '{level.name}': this text is a cell of the table row "
                f"'{_excerpt(row_label, limit)}'; compare it with the original document",
            )
        elif expected and getattr(cell, step_name) != expected[position]:
            positional += 1
            check.add(
                "wrong_column",
                index,
                level.level,
                f"'{label}' level '{level.name}': in the document this text sits under "
                f"the column '{_excerpt(heading or '', limit)}'; compare it with the "
                "original document",
            )
    if not failed and not positional:
        check.add(
            "not_one_row",
            index,
            None,
            f"'{label}': its levels do not come from one row of the table, in order; "
            "compare them with the original document",
        )
    return criterion.model_copy(update={"levels": levels})


def _with_sources(criterion: ParsedCriterion, match: Literal["exact"] | None) -> ParsedCriterion:
    """Every level marked with one match; a passage outside tables has no
    table coordinates."""
    source = LevelSource(match=match or "unmatched")
    return criterion.model_copy(
        update={
            "levels": [
                level.model_copy(update={"source": source}) for level in criterion.levels or []
            ]
        }
    )


def _exact(
    level: ParsedLevel, cell: GridCell, row_label: str | None, heading: str | None
) -> ParsedLevel:
    return level.model_copy(
        update={
            "descriptor": cell.text,
            "source": LevelSource(
                match="exact",
                page=cell.page,
                table=cell.table,
                row=cell.row,
                col=cell.col,
                cell_text=cell.text,
                row_label=row_label or None,
                column_heading=heading or None,
            ),
        }
    )


def _context(placed: list[_Placed], grid: _Grid) -> dict[_Placed, tuple[str, list[str | None]]]:
    """Each placed criterion's row label and the heading over each of its
    levels, as the instructor would find them in the document. The heading
    line is the table's first row (or column), unless a criterion's levels
    are on it."""
    groups: dict[tuple[int, str], list[_Placed]] = {}
    for item in placed:
        groups.setdefault((item.table, item.axis), []).append(item)
    context: dict[_Placed, tuple[str, list[str | None]]] = {}
    for (table, axis), items in groups.items():
        used = {s for item in items for s in item.steps}
        level_steps = _scored_steps(items, grid, set(range(min(used), max(used) + 1)))
        header = min(grid.lines(table, axis))
        has_header = all(item.line != header for item in items)
        for item in items:
            label = grid.label(table, axis, item.line, level_steps)
            headings = [
                (grid.at(table, axis, header, s) or None) if has_header else None
                for s in item.steps
            ]
            context[item] = (label, headings)
    return context


def _monotonic(passages: list[list[int]]) -> bool:
    """Whether one occurrence per level can be chosen so the levels run in
    one direction through the text."""
    for descending in (False, True):
        last: int | None = None
        for starts in passages:
            ordered = sorted(starts, reverse=descending)
            pick = next(
                (s for s in ordered if last is None or (s < last if descending else s > last)),
                None,
            )
            if pick is None:
                break
            last = pick
        else:
            return True
    return False


def _assign(
    pending: list[tuple[int, list[tuple[str, str, list[GridCell]]]]],
    criteria: list[ParsedCriterion],
    grid: _Grid,
) -> list[_Placed]:
    """Choose one placement per criterion. Identical cell text can appear on
    several rows (a repeated "Excellent (4)"), so each criterion takes the
    placement on the row it names best, then one no other criterion took."""
    taken: set[tuple[int, str, int]] = set()
    placed: list[_Placed] = []
    for index, options in pending:
        text = criteria[index].text

        def score(option: tuple[str, str, list[GridCell]], text: str = text) -> tuple:
            axis, step, cells = option
            table, line = cells[0].table, getattr(cells[0], axis)
            steps = {getattr(cell, step) for cell in cells}
            fit = grid.fit(text, grid.label(table, axis, line, steps))
            return (fit, (table, axis, line) not in taken)

        axis, step, cells = max(options, key=score)
        item = _Placed(index, criteria[index], cells, axis, step)
        taken.add((item.table, axis, item.line))
        placed.append(item)
    return placed


def _row_checks(
    placed: list[_Placed], grid: _Grid, check: _Check, limit: int
) -> dict[tuple[int, str], dict[int, tuple[int, ...]]]:
    """The checks on placed criteria. Returns, per table and direction, the
    level columns most rows of each length use, for judging stray levels."""
    typical: dict[tuple[int, str], dict[int, tuple[int, ...]]] = {}
    by_table: dict[tuple[int, str], list[_Placed]] = {}
    for item in placed:
        by_table.setdefault((item.table, item.axis), []).append(item)
    for (table, axis), items in by_table.items():
        used = {s for item in items for s in item.steps}
        level_steps = _scored_steps(items, grid, set(range(min(used), max(used) + 1)))
        legends = _legend_lines(items, grid, level_steps)
        typical[(table, axis)] = _usual_steps(items)
        _direction_checks(items, grid, check, limit, level_steps, typical[(table, axis)])
        for item in items:
            label = _excerpt(item.criterion.text, limit)
            own = set(item.steps)
            for s in sorted(level_steps - own):
                if grid.is_withheld(table, axis, item.line, s):
                    check.add(
                        "other",
                        item.index,
                        None,
                        f"'{label}': a cell of its table row differs between the repeated "
                        "copies of this table and was not read; compare it with the original "
                        "document",
                    )
                    continue
                text = grid.at(table, axis, item.line, s)
                neighbours = {grid.at(table, axis, item.line, s + d) for d in (-1, 1)} & {
                    grid.at(table, axis, item.line, o) for o in own
                }
                if text and match_form(text) not in {match_form(n) for n in neighbours}:
                    check.add(
                        "level_left_out",
                        item.index,
                        None,
                        f"'{label}': its table row has text under a level that was left out; "
                        "compare it with the original document",
                    )
            if item.line in legends:
                continue
            _label_check(item, items, grid, check, limit, level_steps)
        _shared_cells(items, legends, check, limit)
    return typical


def _scored_steps(items: list[_Placed], grid: _Grid, steps: set[int]) -> set[int]:
    """The table's level columns: those its rows' levels span, extended to a
    neighbouring column whose header continues the scale's numbering (a top
    level every row left out is still a level column, "EXEMPLARY 4" after
    1-3). A neighbour headed "Score" or "Weight 20%" does not continue it."""
    table, axis = items[0].table, items[0].axis
    header = min(grid.lines(table, axis))
    if any(item.line == header for item in items):
        return steps

    def number(step: int) -> float | None:
        found = _NUMBER.findall(grid.at(table, axis, header, step))
        return float(found[0]) if len(found) == 1 else None

    ordered = sorted(steps)
    numbers = [number(s) for s in ordered if grid.at(table, axis, header, s)]
    if len(numbers) < 2 or None in numbers:
        return steps
    gaps = {b - a for a, b in zip(numbers, numbers[1:], strict=False)}
    if len(gaps) != 1:
        return steps
    gap = gaps.pop()
    extended = set(steps)
    all_steps = grid.steps(table, axis)
    for direction, edge, value in ((1, ordered[-1], numbers[-1]), (-1, ordered[0], numbers[0])):
        step = edge + direction
        while step in all_steps:
            heading = grid.at(table, axis, header, step)
            if not heading:
                step += direction  # an empty spacer column
                continue
            if number(step) != value + direction * gap:
                break
            extended.add(step)
            value += direction * gap
            step += direction
    return extended


def _usual_steps(items: list[_Placed]) -> dict[int, tuple[int, ...]]:
    """For each number of levels, the columns (in level order) most rows with
    that many levels use, counting only rows that run the majority way."""
    descending = sum(1 for item in items if item.descending) * 2 > len(items)
    counts: dict[int, dict[tuple[int, ...], int]] = {}
    for item in items:
        if item.descending != descending:
            continue
        steps = tuple(item.steps)
        by_steps = counts.setdefault(len(steps), {})
        by_steps[steps] = by_steps.get(steps, 0) + 1
    return {n: max(c, key=c.get) for n, c in counts.items()}


def _legend_lines(items: list[_Placed], grid: _Grid, level_steps: set[int]) -> set[int]:
    """Lines several criteria use with exactly the same cells, none of them
    named for the line's label: a scale legend shared by the criteria."""
    by_line: dict[int, list[_Placed]] = {}
    for item in items:
        by_line.setdefault(item.line, []).append(item)
    legends = set()
    for line, users in by_line.items():
        if len(users) < 2 or len({tuple(u.steps) for u in users}) != 1:
            continue
        label = grid.label(users[0].table, users[0].axis, line, level_steps)
        if all(grid.fit(u.criterion.text, label)[1] == 0 for u in users):
            legends.add(line)
    return legends


def _direction_checks(
    items: list[_Placed],
    grid: _Grid,
    check: _Check,
    limit: int,
    level_steps: set[int],
    usual: dict[int, tuple[int, ...]],
) -> None:
    """Rows of one table run the same way, and each level sits under a header
    that fits its points and name. A level in the wrong column is named on its
    own ("wrong_column"); a row reversed as a whole with no column to blame
    is named as a row ("not_one_row")."""
    table, axis = items[0].table, items[0].axis
    lines = grid.lines(table, axis)
    header_line = min(lines)
    has_header = all(item.line != header_line for item in items)
    ascending = sum(1 for item in items if not item.descending)
    descending = len(items) - ascending
    if ascending != descending:
        majority_descending = descending > ascending
        for item in items:
            if item.descending == majority_descending:
                continue
            expected = usual.get(len(item.steps))
            blamed = 0
            for level, step, want in zip(
                item.criterion.levels or [], item.steps, expected or (), strict=False
            ):
                if step == want:
                    continue
                blamed += 1
                heading = grid.at(table, axis, header_line, step) if has_header else ""
                check.add(
                    "wrong_column",
                    item.index,
                    level.level,
                    f"'{_excerpt(item.criterion.text, limit)}' level '{level.name}': in the "
                    f"document this text sits under the column '{_excerpt(heading, limit)}'; "
                    "compare it with the original document",
                )
            if not blamed:
                check.add(
                    "not_one_row",
                    item.index,
                    None,
                    f"'{_excerpt(item.criterion.text, limit)}': its levels run in the "
                    "opposite order to the other rows of the table; compare them with the "
                    "original document",
                )
    if not has_header:
        return
    headers = {s: grid.at(table, axis, header_line, s) for s in level_steps}
    similarity = check.settings.rubric_level_header_min_similarity
    for item in items:
        for level, s in zip(item.criterion.levels or [], item.steps, strict=True):
            header = headers.get(s, "")
            numbers = _NUMBER.findall(header)
            if len(numbers) == 1 and float(numbers[0]) != level.points:
                misplaced = True
            else:
                name = match_form(re.sub(r"\d", "", level.name))
                fits = {
                    step: SequenceMatcher(None, name, match_form(re.sub(r"\d", "", h))).ratio()
                    for step, h in headers.items()
                    if h
                }
                best = max(fits, key=fits.get) if fits else None
                misplaced = (
                    best is not None
                    and best != s
                    and fits[best] >= similarity
                    and fits.get(s, 0.0) < similarity
                )
            if misplaced:
                check.add(
                    "wrong_column",
                    item.index,
                    level.level,
                    f"'{_excerpt(item.criterion.text, limit)}' level '{level.name}': in the "
                    f"document this text sits under the column '{_excerpt(header, limit)}'; "
                    "compare it with the original document",
                )


def _label_check(
    item: _Placed,
    items: list[_Placed],
    grid: _Grid,
    check: _Check,
    limit: int,
    level_steps: set[int],
) -> None:
    """The criterion names its own row, and no other row better."""
    table, axis = item.table, item.axis
    own_label = grid.label(table, axis, item.line, level_steps)
    if not grid.words(own_label):
        return
    own = grid.fit(item.criterion.text, own_label)
    better = [
        line
        for line in grid.lines(table, axis) - {item.line}
        if grid.fit(item.criterion.text, grid.label(table, axis, line, level_steps)) > own
    ]
    if own[1] == 0 or better:
        check.add(
            "wrong_row",
            item.index,
            None,
            f"'{_excerpt(item.criterion.text, limit)}' is described with the levels of the "
            f"table row '{_excerpt(own_label, limit)}'; compare it with the original document",
        )


def _shared_cells(items: list[_Placed], legends: set[int], check: _Check, limit: int) -> None:
    used: dict[tuple[int, int, int], ParsedCriterion] = {}
    for item in items:
        if item.line in legends:
            continue
        for cell in item.cells:
            key = (cell.table, cell.row, cell.col)
            other = used.setdefault(key, item.criterion)
            if other is not item.criterion:
                check.add(
                    "shared_cell",
                    item.index,
                    None,
                    f"'{_excerpt(item.criterion.text, limit)}' uses the same table cell as "
                    f"'{_excerpt(other.text, limit)}'; compare them with the original document",
                )
