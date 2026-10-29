"""How an automatic grade was chosen, in the same words as the report screen.

The sentences live in data/automatic_grade_copy.json and match the
frontend's AUTOMATIC_GRADE_COPY (a test compares them). Which lines a row
gets is pinned by tests/fixtures/automatic_grade_cases.json, which the
frontend's own copy of this logic can run as well.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

# Cautions saved before decide-mode cautions were worded for a kept grade
# could end "no automatic grade was assigned". The grade beside them was
# kept, so that clause is left out wherever the caution is shown.
_STALE_NO_GRADE = re.compile(r"[\s;,.]*no automatic grade was assigned\.?\s*$", re.IGNORECASE)


class AutomaticGradeRow(Protocol):
    decision_basis: str | None
    support_caution: str | None
    agreeing_passes: int | None
    grading_passes: int | None


@lru_cache
def _copy() -> dict[str, str]:
    return json.loads(
        (Path(__file__).parent / "data" / "automatic_grade_copy.json").read_text("utf-8")
    )


def clean_caution(caution: object) -> str | None:
    """The caution as it should read beside a kept grade, or None if empty."""
    if not isinstance(caution, str):
        return None
    reason = _STALE_NO_GRADE.sub("", caution).strip()
    if not reason:
        return None
    return reason if reason[-1] in ".!?" else f"{reason}."


def automatic_grade_lines(row: AutomaticGradeRow) -> list[str]:
    """How the automatic grade was chosen. Nothing for a grade the instructor
    set; the report omits these fields then."""
    copy = _copy()
    if row.decision_basis in copy:
        return [copy[row.decision_basis]]
    if row.grading_passes and row.agreeing_passes:
        return [copy["agreed"].format(agreeing=row.agreeing_passes, passes=row.grading_passes)]
    return []


def automatic_grade_caution(row: AutomaticGradeRow) -> str | None:
    """The evidence caution on an automatic grade, shown apart from how it
    was chosen. None when there is no automatic grade to qualify."""
    reason = clean_caution(row.support_caution)
    if reason is None or not automatic_grade_lines(row):
        return None
    return _copy()["caution"].format(reason=reason)
