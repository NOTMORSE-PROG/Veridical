"""Simulated annotator perspectives (V-054, D-017).

Loader for `data/annotator_perspectives.json` — see that file's own `_readme`
for why the perspectives exist and what changing them costs.

The short version: self-consistency at temperature 0 repeats a
near-deterministic sample, so the agreement between passes carried almost no
information — and that agreement is this product's only confidence signal
(D-006). Conditioning one model on genuinely different reading stances
recovers real signal without the two alternatives the research rules out
(temperature noise degrades each judgement; multi-model panels collapse to
~2 effective votes through correlated errors).
"""

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "annotator_perspectives.json"

ROLE_PASS_1 = "pass_1"
ROLE_PASS_2 = "pass_2"
ROLE_TIE_BREAK = "tie_break"
_REQUIRED_ROLES = (ROLE_PASS_1, ROLE_PASS_2, ROLE_TIE_BREAK)


@dataclass(frozen=True)
class AnnotatorPerspective:
    id: str
    label: str
    role: str
    stance: str


class AnnotatorPerspectiveError(ValueError):
    """The perspectives file is unusable. Raised rather than silently falling
    back to a single stance: that would quietly restore the meaningless
    agreement signal this exists to fix, and nothing downstream would show it.
    """


def load_perspectives(path: Path | None = None) -> tuple[AnnotatorPerspective, ...]:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    entries = raw.get("perspectives") or []
    perspectives = tuple(
        AnnotatorPerspective(
            id=str(entry["id"]),
            label=str(entry["label"]),
            role=str(entry["role"]),
            stance=str(entry["stance"]).strip(),
        )
        for entry in entries
    )
    if not perspectives:
        raise AnnotatorPerspectiveError("No annotator perspectives defined.")

    for perspective in perspectives:
        if not perspective.stance:
            raise AnnotatorPerspectiveError(f"Perspective {perspective.id!r} has a blank stance.")

    roles = [p.role for p in perspectives]
    for required in _REQUIRED_ROLES:
        if roles.count(required) != 1:
            raise AnnotatorPerspectiveError(
                f"Exactly one perspective must have role {required!r}; "
                f"found {roles.count(required)}."
            )

    stances = {p.stance for p in perspectives}
    if len(stances) != len(perspectives):
        # Identical stances would look like an ensemble while being the same
        # judgement repeated — the exact failure mode being removed here.
        raise AnnotatorPerspectiveError("Two perspectives share an identical stance.")
    return perspectives


def perspective_for(role: str, path: Path | None = None) -> AnnotatorPerspective:
    for perspective in load_perspectives(path):
        if perspective.role == role:
            return perspective
    raise AnnotatorPerspectiveError(f"No perspective defined for role {role!r}.")
