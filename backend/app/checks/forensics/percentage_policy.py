"""Loader for the data file that decides when a table's percentages are
parts of one whole (ENGINEERING.md section 8: cue words and tolerances are
data, never branches)."""

import json
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "percentage_sum_policy.json"


@dataclass(frozen=True)
class PercentageSumPolicy:
    version: str
    total_row_labels: frozenset[str]
    whole_header_cues: tuple[str, ...]
    independent_header_cues: tuple[str, ...]
    max_part_percent: float
    near_whole_band: float
    share_match_tolerance: float
    share_match_min_fraction: float
    share_match_min_rows: int


def load_percentage_policy(path: Path | None = None) -> PercentageSumPolicy:
    if path is None:
        from app.config import get_settings

        path = get_settings().forensics_percentage_policy_file
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    return PercentageSumPolicy(
        version=raw["version"],
        total_row_labels=frozenset(label.casefold() for label in raw["total_row_labels"]),
        whole_header_cues=tuple(cue.casefold() for cue in raw["whole_header_cues"]),
        independent_header_cues=tuple(cue.casefold() for cue in raw["independent_header_cues"]),
        max_part_percent=float(raw["max_part_percent"]),
        near_whole_band=float(raw["near_whole_band"]),
        share_match_tolerance=float(raw["share_match_tolerance"]),
        share_match_min_fraction=float(raw["share_match_min_fraction"]),
        share_match_min_rows=int(raw["share_match_min_rows"]),
    )
