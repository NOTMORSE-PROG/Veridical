"""Loader for the forensics table-column-header data file (ENGINEERING.md
§8: a table's own header vocabulary is data, never a hardcoded branch)."""

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "forensics_keywords.json"


@dataclass(frozen=True)
class ForensicsKeywords:
    n_headers: frozenset[str]
    mean_headers: frozenset[str]
    sd_headers: frozenset[str]
    percentage_headers: frozenset[str]


def load_keywords(path: Path | None = None) -> ForensicsKeywords:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    return ForensicsKeywords(
        n_headers=frozenset(h.casefold() for h in raw["n_headers"]),
        mean_headers=frozenset(h.casefold() for h in raw["mean_headers"]),
        sd_headers=frozenset(h.casefold() for h in raw["sd_headers"]),
        percentage_headers=frozenset(h.casefold() for h in raw["percentage_headers"]),
    )
