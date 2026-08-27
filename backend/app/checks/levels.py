"""Levelled-rubric grading vocabulary (V-069): the single source of truth
for turning a grading verdict STRING into a persistable (outcome, score,
level) triple — generalizes `escalation.py`'s old `_SCORE_BY_VERDICT`/
`_OUTCOME_BY_VERDICT` (pass/partial/fail only) so a criterion carrying its
OWN named performance levels (`Criterion.levels`, V-069 AC1) is graded
against ITS scale instead. Branches on whether the CRITERION at hand
carries levels, never on a global mode flag — so a rubric mixing levelled
and pass/fail criteria (ticket edge case) grades each criterion correctly.

Both `checks/semantic.py` (single-pass) and `checks/escalation.gate_vote`
(N-pass voting) call `outcome_and_score` instead of keeping their own copy
of this mapping — the exact duplication-drift class Track D's "999%/1082%"
finding (D-023) exists to prevent.
"""

from dataclasses import dataclass
from typing import Any

from app.models.enums import ResultOutcome

_SCORE_BY_VERDICT = {"pass": 100.0, "partial": 50.0, "fail": 0.0}
_OUTCOME_BY_VERDICT = {
    "pass": ResultOutcome.passed,
    "partial": ResultOutcome.passed,
    "fail": ResultOutcome.failed,
}


@dataclass(frozen=True)
class LevelMatch:
    """One decided rung of a levelled criterion's own scale — everything
    the report/export/escalation-detail layers need to show the level as
    the judgment (V-069 AC2's owner-approved treatment) without re-deriving
    it from raw JSON at each call site."""

    name: str
    ordinal: int
    points: float
    max_points: float

    def as_detail(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ordinal": self.ordinal,
            "points": self.points,
            "max_points": self.max_points,
        }


def is_levelled(criterion: Any) -> bool:
    return bool(getattr(criterion, "levels", None))


def max_points(criterion: Any) -> float | None:
    """None only when `criterion` isn't levelled at all — a levelled
    criterion always has >=2 rungs (`ParsedLevel`'s own validator), so this
    is never 0 for a real levelled criterion."""
    levels = getattr(criterion, "levels", None)
    if not levels:
        return None
    return max(float(lvl["points"]) for lvl in levels)


def match_level_by_ordinal(criterion: Any, ordinal: int | None) -> LevelMatch | None:
    """The resolve-panel counterpart to `match_level` (V-069 AC4): the
    instructor picks a level by its ORDINAL position (what the picker UI
    shows), not by typing the level's name back."""
    if ordinal is None:
        return None
    levels = getattr(criterion, "levels", None) or []
    for lvl in levels:
        if int(lvl["level"]) == ordinal:
            return LevelMatch(
                name=lvl["name"],
                ordinal=int(lvl["level"]),
                points=float(lvl["points"]),
                max_points=max_points(criterion) or 0.0,
            )
    return None


def match_level(criterion: Any, verdict: str) -> LevelMatch | None:
    """`None` means `verdict` doesn't name any of THIS criterion's own
    levels — never guessed or fuzzy-matched (a level name the model
    invented, or answered in the wrong criterion's vocabulary in a mixed
    batch, must escalate to the instructor, not silently snap to the
    nearest-looking rung)."""
    levels = getattr(criterion, "levels", None) or []
    for lvl in levels:
        if lvl["name"] == verdict:
            return LevelMatch(
                name=lvl["name"],
                ordinal=int(lvl["level"]),
                points=float(lvl["points"]),
                max_points=max_points(criterion) or 0.0,
            )
    return None


def outcome_and_score(
    criterion: Any, verdict: str
) -> tuple[ResultOutcome, float | None, LevelMatch | None]:
    """`criterion=None` (or a non-levelled criterion) falls straight through
    to the original pass/partial/fail lookup, byte-identical to the old
    behavior (V-069 AC3) — including for an unrecognized string, which now
    escalates instead of raising `KeyError`. That's only reachable at all
    because `GradeVerdict.verdict` was relaxed from a `Literal["pass",
    "partial","fail"]` to a plain string (needed so a level NAME can
    validate) — a hardening side effect, not a path the old Literal-
    validated schema could ever have hit.
    """
    if is_levelled(criterion):
        match = match_level(criterion, verdict)
        if match is None:
            return ResultOutcome.escalated, None, None
        if not match.max_points:
            # `backend-critic` finding: a degenerate all-zero-points scale
            # (now blocked at the source by `ParsedLevel`'s own scale
            # validator, but guarded here too for any criterion that
            # predates that check) must never look DECIDED with no score
            # -- every other "can't actually score this" state in this
            # codebase (escalated/not_applicable/quota_exhausted) is
            # honest about being undecided; silently returning `passed`
            # with `score=None` would make this the one exception, never
            # surfacing to the instructor at all (charter rule 9).
            return ResultOutcome.escalated, None, None
        return ResultOutcome.passed, match.points / match.max_points * 100.0, match
    outcome = _OUTCOME_BY_VERDICT.get(verdict)
    if outcome is None:
        return ResultOutcome.escalated, None, None
    return outcome, _SCORE_BY_VERDICT[verdict], None


def level_scale_prompt_fragment(criterion: Any) -> str | None:
    """The per-criterion scale text injected into the grading batch listing
    (`semantic.py::_criteria_listing`) so the model knows THIS criterion's
    own level names — `None` for a non-levelled criterion (nothing to
    inject, the generic pass/partial/fail instruction already covers it).

    BUG-135: the level name is quoted and the ordinal kept OUTSIDE the
    quotes, on purpose. The old `f"{name} ({level}) = ..."` glued the
    ordinal directly onto the name with no delimiter — harmless for a
    scale like "Proficient", but many real rubrics (this project's own
    TIP oral-presentation rubric among them) name each rung "Exemplary 4"
    /"Proficient 3" style, i.e. the name ITSELF already ends in a digit
    that duplicates the ordinal. Shown as "Exemplary 4 (4) = ...", the
    model has no way to tell the trailing "(4)" isn't part of "the exact
    level name" it was told to echo, and reliably answers "Exemplary 4
    (4)" — which then fails `match_level`'s deliberately-exact,
    never-fuzzy comparison (charter rule 1: a wrong guess must escalate,
    not silently snap to a rung), escalating nearly every levelled
    criterion in a run for a self-inflicted formatting reason, not a real
    grading difficulty. Quoting the name gives the model an unambiguous
    boundary to copy verbatim; the ordinal stays available for the
    model's own reasoning about level order without being part of the
    string it must reproduce.
    """
    levels = getattr(criterion, "levels", None)
    if not levels:
        return None
    scale = "; ".join(
        f'level {lvl["level"]} is "{lvl["name"]}" = {lvl["descriptor"]}' for lvl in levels
    )
    return (
        'Named performance levels for THIS criterion, set "verdict" to the '
        "EXACT quoted level name below (verbatim, nothing else) that the evidence "
        f"best supports, not pass/partial/fail: {scale}"
    )
