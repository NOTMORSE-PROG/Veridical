"""Grading-quality proxies: repeat stability, evidence support and quote grounding.

None of these numbers is accuracy. Accuracy needs an independent instructor's
grades, and this lane uses none. It measures whether the grader agrees with
itself when asked again, whether its own support check accepts the evidence
behind each grade, and whether every quoted passage really is on the page it
cites. Those are preconditions for accuracy, not substitutes for it.

Everything here is pure: grading results and page texts come in, rates come
out. Talking to the database, the model or the PDF tools is the caller's job,
so the arithmetic can be checked by hand.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from app.checks.accuracy_stats import fleiss_kappa, gwet_ac1_multi

LANE = "grading"
LABEL = "internal proxy, NOT accuracy (no instructor labels)"

# A level was decided automatically only on these outcomes (the lowest level
# of a scale can come out as "failed"); anything else went to the instructor
# or did not apply.
DECIDED = {"passed", "failed"}

# --- quote grounding ---------------------------------------------------------

ON_ANCHOR_PAGE = "on_anchor_page"
OTHER_PAGE = "in_document_other_page"
NOT_FOUND = "not_found"
IMAGE_PAGE = "image_page"
NO_PAGE_ANCHOR = "no_page_anchor"

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"'})
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"})
_ELLIPSIS = re.compile(r"\.\s*\.\s*\.|…")
_LINE_HYPHEN = re.compile(r"(\w)-[ \t]*\r?\n[ \t]*(\w)")
_SPACE = re.compile(r"\s+")
_PAGE_ANCHOR = re.compile(r"^\s*page\s+(\d+)\s*$", re.IGNORECASE)


def _base(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_QUOTES).translate(_DASHES)


def _flat(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def page_variants(text: str) -> tuple[str, ...]:
    """A page's text in the forms a quote may legitimately match.

    A word split across a line break with a hyphen is joined; a hyphen that
    belongs to the word (self-\\nconsistency) is also kept as a second form,
    because the extraction cannot tell the two apart."""
    base = _base(text)
    joined = _flat(_LINE_HYPHEN.sub(r"\1\2", base))
    kept = _flat(_LINE_HYPHEN.sub(r"\1-\2", base))
    return (joined,) if joined == kept else (joined, kept)


def quote_fragments(quote: str) -> list[str]:
    """An ellipsis marks omitted text, so the parts either side of it must
    appear in order rather than as one run."""
    parts = (_flat(_LINE_HYPHEN.sub(r"\1\2", _base(part))) for part in _ELLIPSIS.split(quote))
    return [part for part in parts if part]


def _in_order(fragments: Sequence[str], text: str) -> bool:
    position = 0
    for fragment in fragments:
        found = text.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


def page_number(anchor: str | None) -> int | None:
    match = _PAGE_ANCHOR.match(anchor or "")
    return int(match.group(1)) if match else None


def classify_quote(
    quote: str,
    anchor: str | None,
    pages: Mapping[int, str],
    *,
    image_page_max_chars: int,
) -> str:
    """Where a quoted passage is in the source, from an extraction that is
    independent of the product's own.

    `pages` maps 1-based page numbers to page text. A cited page with almost
    no text layer is an image page: a person has to look at it, so it is not
    counted as found or missing."""
    fragments = quote_fragments(quote)
    if not fragments:
        return NOT_FOUND
    cited = page_number(anchor)
    if cited is None:
        return NO_PAGE_ANCHOR
    cited_text = pages.get(cited, "")
    if any(_in_order(fragments, form) for form in page_variants(cited_text)):
        return ON_ANCHOR_PAGE
    for number, text in pages.items():
        if number != cited and any(_in_order(fragments, form) for form in page_variants(text)):
            return OTHER_PAGE
    if len(_flat(cited_text)) <= image_page_max_chars:
        return IMAGE_PAGE
    return NOT_FOUND


# --- repeat stability --------------------------------------------------------


@dataclass(frozen=True)
class Grade:
    """One criterion of one manuscript, as one repeat graded it. `level` is
    None when the repeat decided nothing (escalated or ungraded)."""

    unit: Hashable
    repeat: int
    level: str | None
    decided: bool
    # The level the grader put forward whether or not it was kept: an
    # escalated grade keeps it as `proposed_level`. None when no level was
    # proposed at all (the grading was unavailable).
    proposed: str | None = None


@dataclass
class StabilitySummary:
    units: int
    all_equal: int
    pairs: int
    pairs_exact: int
    pairs_within_one: int
    decided_flips: list[Hashable] = field(default_factory=list)
    incomplete_units: list[Hashable] = field(default_factory=list)
    # Decided in every repeat, but with a level outside the scale (a verdict
    # word, a renamed level): listed, never silently left out.
    off_scale_units: list[Hashable] = field(default_factory=list)
    fleiss_kappa: float | None = None
    ac1: float | None = None
    prevalence: dict[str, float] = field(default_factory=dict)
    # Over every unit seen in all repeats: a flip or an off-scale level counts
    # as disagreement here, so instability cannot leave the denominator.
    strict_units: int = 0
    strict_all_equal: int = 0
    # The grader's proposals, kept or not, over units with one in every repeat.
    proposed_units: int = 0
    proposed_all_equal: int = 0
    # Ordered pairs of repeats of the same unit, by level (row, column).
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    specific_agreement: dict[str, float | None] = field(default_factory=dict)


def stability(grades: Iterable[Grade], scale: Sequence[str], repeats: int) -> StabilitySummary:
    """Agreement across repeats of the same grading, cache bypassed.

    A unit counts toward level agreement only when every repeat decided a
    level; units where a repeat escalated are listed as flips (decided in one
    repeat, escalated in another) or as incomplete, never quietly dropped.
    `scale` is the rubric's level order, lowest first, for within-one."""
    order = {level: index for index, level in enumerate(scale)}
    by_unit: dict[Hashable, dict[int, Grade]] = {}
    for grade in grades:
        by_unit.setdefault(grade.unit, {})[grade.repeat] = grade
    summary = StabilitySummary(0, 0, 0, 0, 0)
    complete: list[list[str]] = []
    for unit, seen in sorted(by_unit.items(), key=lambda item: str(item[0])):
        if len(seen) != repeats:
            summary.incomplete_units.append(unit)
            continue
        decided = {grade.decided for grade in seen.values()}
        if len(decided) > 1:
            summary.decided_flips.append(unit)
        levels = [seen[r].level for r in sorted(seen)]
        proposed = [seen[r].proposed for r in sorted(seen)]
        if all(level in order for level in proposed):
            summary.proposed_units += 1
            summary.proposed_all_equal += len(set(proposed)) == 1
        all_decided = all(grade.decided for grade in seen.values())
        on_scale = all(level in order for level in levels)
        if any(grade.decided and grade.level not in order for grade in seen.values()):
            summary.off_scale_units.append(unit)
        # The result an instructor would see: the level, or "escalated". An
        # off-scale level is marked per repeat, so it never matches anything.
        results = [
            (seen[r].level if seen[r].level in order else f"off-scale in r{r}")
            if seen[r].decided
            else "escalated"
            for r in sorted(seen)
        ]
        summary.strict_units += 1
        summary.strict_all_equal += len(set(results)) == 1
        if not (all_decided and on_scale):
            continue
        summary.units += 1
        summary.all_equal += len(set(levels)) == 1
        for left, right in combinations(levels, 2):
            summary.pairs += 1
            summary.pairs_exact += left == right
            summary.pairs_within_one += abs(order[left] - order[right]) <= 1
        complete.append(levels)
    if complete:
        summary.fleiss_kappa = fleiss_kappa(complete)
        summary.ac1 = gwet_ac1_multi(complete, scale)
        shares = Counter(level for unit in complete for level in unit)
        total = sum(shares.values())
        summary.prevalence = {level: shares[level] / total for level in scale}
        summary.confusion = pair_confusion(complete, scale)
        summary.specific_agreement = specific_agreement(complete, scale)
    return summary


def pair_confusion(units: Sequence[Sequence[str]], scale: Sequence[str]) -> dict:
    """Counts of (level in one repeat, level in another) over every ordered
    pair of repeats of a unit, so the table is symmetric. An "always the same
    level" grader shows up as one heavy cell."""
    table = {row: dict.fromkeys(scale, 0) for row in scale}
    for levels in units:
        for left, right in combinations(levels, 2):
            table[left][right] += 1
            table[right][left] += 1
    return table


def specific_agreement(units: Sequence[Sequence[str]], scale: Sequence[str]) -> dict:
    """Per-level specific agreement: given one repeat chose this level, the
    chance another repeat of the same unit chose it too. It is the
    repeat-against-repeat analogue of per-level recall; with no instructor
    labels there is no true level to recall against.

    For level j: sum over units of n_j(n_j - 1) / sum of n_j(m - 1), where
    n_j repeats of a unit chose j out of m. None when no repeat chose j."""
    result: dict[str, float | None] = {}
    for level in scale:
        agree = possible = 0
        for levels in units:
            n = sum(item == level for item in levels)
            agree += n * (n - 1)
            possible += n * (len(levels) - 1)
        result[level] = agree / possible if possible else None
    return result


# --- lane entry point -------------------------------------------------------

SCOPE = (
    "Internal proxy (self-agreement, support check, quote grounding), "
    "no instructor labels, NOT accuracy."
)


def tally(items: Iterable, key: Callable) -> dict:
    return dict(Counter(key(item) for item in items))


def run(ctx) -> tuple[list, dict]:
    """Scores whatever grading data the golden folder holds: stored production
    runs exported read-only (`grading/live/`) today, cache-bypassed repeats
    once they exist. Page texts come from an independent PDF tool."""
    from tools.accuracy.grading_live import analyse

    config = ctx.lane_config(LANE)
    folder = ctx.golden / LANE
    rows, details = [], {}
    live = folder / "live"
    if (live / "results.json").exists():
        live_rows, details["live"] = analyse(
            live,
            folder / "pages",
            json.loads((live / "docs.json").read_text(encoding="utf-8")),
            image_page_max_chars=config.get("image_page_max_chars", 40),
            targets=config.get("targets", {}),
        )
        rows += live_rows
    if (folder / "setup.json").exists() and (folder / "repeats").is_dir():
        from tools.accuracy.grading_repeat_scores import score_repeats

        repeat_rows, details["repeats"] = score_repeats(
            folder,
            folder / "pages",
            image_page_max_chars=config.get("image_page_max_chars", 40),
            targets=config.get("targets", {}),
        )
        rows += repeat_rows
    return rows, details
