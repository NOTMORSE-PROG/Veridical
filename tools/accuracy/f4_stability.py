"""Compare F4 verdicts on the same plants across lane runs (BUG-635).

Each run is a `report.json` from the F4 lane. Only plants judged in both runs
are compared. Three agreement figures per pair of runs: the exact verdict,
whether a finding was raised at all (contradictory or partial against silent),
and whether the pair was called contradictory.

    python -m tools.accuracy.f4_stability RUN_A RUN_B [RUN_C ...]
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

FLAGGED = {"contradictory", "partial"}
JUDGED = {
    "contradictory",
    "partial",
    "consistent",
    "consistent_in_context",
    "unrelated",
    "cannot_determine",
}


def plants(run: Path) -> dict[str, dict]:
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    records = report["details"]["f4"]["plants"]
    return {r["plant_id"]: r for r in records if r.get("verdict") in JUDGED}


def compare(a: dict[str, dict], b: dict[str, dict]) -> dict:
    common = sorted(set(a) & set(b))
    if not common:
        return {"n": 0}

    def same(test):
        return sum(test(a[p]["verdict"]) == test(b[p]["verdict"]) for p in common)

    return {
        "n": len(common),
        "same_verdict": same(lambda v: v),
        "same_flagged_or_silent": same(lambda v: v in FLAGGED),
        "same_contradictory": same(lambda v: v == "contradictory"),
        "changed": [
            {"plant": p, "kind": a[p]["kind"], "a": a[p]["verdict"], "b": b[p]["verdict"]}
            for p in common
            if a[p]["verdict"] != b[p]["verdict"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", type=Path)
    args = parser.parse_args()
    loaded = {run.name: plants(run) for run in args.runs}
    for (name_a, a), (name_b, b) in itertools.combinations(loaded.items(), 2):
        result = compare(a, b)
        n = result["n"]
        if not n:
            print(f"{name_a} vs {name_b}: no plant judged in both")
            continue
        print(
            f"{name_a} vs {name_b}: {n} plants judged in both | "
            f"same verdict {result['same_verdict']}/{n} | "
            f"same flagged-or-silent {result['same_flagged_or_silent']}/{n} | "
            f"same contradictory {result['same_contradictory']}/{n}"
        )


if __name__ == "__main__":
    main()
