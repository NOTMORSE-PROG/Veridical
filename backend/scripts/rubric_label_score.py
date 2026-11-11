"""Score automatic rubric grades against an instructor's completed label sheet.

Inputs are two CSV files joined on `row_id`:

* the label sheet the instructor filled in (from the labelling package's
  "Download my grades" button, or the blank sheet filled in a spreadsheet):
  row_id, instructor_level, cannot_judge, instructor_rationale,
  ai_revealed_before_label, labelled_by, label_source, ...
* the automatic grades for the same rows:
  row_id, manuscript, criterion, ai_outcome (decided|escalated), ai_level,
  graded_against_misparsed_levels (yes|no), ...

The scale is passed in by the caller (`--levels`, lowest first) because it
belongs to the rubric being checked, not to this tool. The gate threshold
and minimum sample size come from settings (TIER_PROMOTION_AGREEMENT,
TIER_PROMOTION_MIN_N), the same D-016 bar the promotion gate uses.

Usage (from backend/):

    uv run python -m scripts.rubric_label_score \\
        --labels labels.csv --grades ai_grades.csv \\
        --levels Beginner,Acceptable,Proficient,Exemplary --out report.md

Exits 2 when the sheet's provenance is refused: grades a model produced are
never scored as instructor ground truth.
"""

import argparse
import csv
import sys
from pathlib import Path

from app.checks.rubric_agreement import (
    AutomaticGrade,
    InstructorLabel,
    ProvenanceError,
    report_as_markdown,
    score,
)
from app.config import get_settings

_YES = {"yes", "y", "true", "1", "x"}


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(handle)
        ]


def parse_level(raw: str, names: list[str]) -> int | None:
    """A level as its scale position (1 = lowest). Accepts the level name in
    any case, or its number. Empty means ungraded."""
    if not raw:
        return None
    if raw.isdigit():
        value = int(raw)
        if not 1 <= value <= len(names):
            raise ValueError(f"Level {value} is outside a {len(names)}-level scale")
        return value
    lowered = [n.lower() for n in names]
    if raw.lower() not in lowered:
        raise ValueError(f"'{raw}' is not one of the scale's levels: {', '.join(names)}")
    return lowered.index(raw.lower()) + 1


def load_labels(path: Path, names: list[str]) -> list[InstructorLabel]:
    labels = []
    for row in _read(path):
        revealed = row.get("ai_revealed_before_label", "").lower()
        labels.append(
            InstructorLabel(
                row_id=row["row_id"],
                level=parse_level(row.get("instructor_level", ""), names),
                cannot_judge=row.get("cannot_judge", "").lower() in _YES,
                labelled_by=row.get("labelled_by", ""),
                label_source=row.get("label_source", ""),
                ai_revealed_before_label=(revealed in _YES) if revealed else None,
                rationale=row.get("instructor_rationale", ""),
            )
        )
    return labels


def load_grades(path: Path, names: list[str]) -> list[AutomaticGrade]:
    grades = []
    for row in _read(path):
        decided = row.get("ai_outcome", "").lower() == "decided"
        grades.append(
            AutomaticGrade(
                row_id=row["row_id"],
                manuscript=row.get("manuscript", ""),
                criterion=row.get("criterion", ""),
                decided=decided,
                level=parse_level(row.get("ai_level", ""), names) if decided else None,
                flagged=row.get("graded_against_misparsed_levels", "").lower() in _YES,
            )
        )
    return grades


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument(
        "--levels", required=True, help="Level names, lowest first, comma separated"
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    names = [n.strip() for n in args.levels.split(",") if n.strip()]
    settings = get_settings()
    try:
        report = score(
            load_grades(args.grades, names),
            load_labels(args.labels, names),
            scale=list(range(1, len(names) + 1)),
            threshold=settings.tier_promotion_agreement,
            min_n=settings.tier_promotion_min_n,
        )
    except ProvenanceError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    text = report_as_markdown(report, level_names={i + 1: n for i, n in enumerate(names)})
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
