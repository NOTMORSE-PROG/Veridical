"""Numeric bound extraction from a criterion's own wording (data-driven
regex, ENGINEERING.md §8) — "at least 15 references", "must not exceed 100
pages", "1 inch margins" all become one `Bound` shape the rule modules
compare against reality. No rubric-specific phrasing is assumed; only the
phrase PATTERNS are fixed, and those live in bound_patterns.json.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_DEFAULT_FILE = Path(__file__).parent / "data" / "bound_patterns.json"

# Points-per-unit for physical measurements (72pt = 1in, the PDF/DOCX point).
_UNIT_TO_POINTS = {
    "in": 72.0,
    "inch": 72.0,
    "inches": 72.0,
    "cm": 28.3465,
    "mm": 2.83465,
    "pt": 1.0,
    "point": 1.0,
    "points": 1.0,
}

BoundKind = Literal["min", "max", "exact", "range"]


@dataclass(frozen=True)
class Bound:
    kind: BoundKind
    low: float | None  # points/pages/count, already unit-converted if a unit was recognized
    high: float | None  # only set for kind == "range"
    unit: str | None  # raw unit word as written, lowercased ("inch", "pages", "references", ...)


def to_points(value: float, unit: str) -> float | None:
    """None if `unit` isn't a recognized physical-length unit (e.g. it was
    "pages" or "references") — the caller decides what that means."""
    factor = _UNIT_TO_POINTS.get(unit.lower())
    return None if factor is None else value * factor


def _load_patterns(path: Path | None = None) -> dict[str, re.Pattern[str]]:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    return {
        key: re.compile(pattern, re.IGNORECASE)
        for key, pattern in raw.items()
        if not key.startswith("_")
    }


_PATTERNS = _load_patterns()


def extract_bound(text: str, *, patterns: dict[str, re.Pattern[str]] | None = None) -> Bound | None:
    patterns = patterns or _PATTERNS
    for kind in ("range", "min", "max", "exact", "bare"):
        pattern = patterns.get(kind)
        if pattern is None:
            continue
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groupdict()
        unit = (groups.get("unit") or "").lower() or None
        low = float(groups["num1"])
        if kind == "range":
            return Bound(kind="range", low=low, high=float(groups["num2"]), unit=unit)
        effective_kind: BoundKind = "exact" if kind == "bare" else kind  # type: ignore[assignment]
        return Bound(kind=effective_kind, low=low, high=None, unit=unit)
    return None
