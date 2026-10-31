"""Run the module accuracy harness and write report.json and report.md.

Usage, from the repository root:
    uv run --project backend python -m tools.accuracy.run_accuracy --lane f6 \
        --golden context/golden/accuracy

Case data and reports stay in the golden folder, which is not committed.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from tools.accuracy.core import HEADER, Context, git_commit, write_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", action="append", required=True, help="lane name, e.g. f6")
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261031)
    args = parser.parse_args()
    ctx = Context(
        golden=args.golden,
        output=args.golden / "runs",
        seed=args.seed,
        code_commit=git_commit(ROOT),
    )
    rows, details, scopes = [], {}, {}
    for lane in args.lane:
        module = importlib.import_module(f"tools.accuracy.lanes.{lane}")
        lane_rows, lane_details = module.run(ctx)
        rows += lane_rows
        details[lane] = lane_details
        # Every lane must say what its numbers measure.
        scopes[lane] = module.SCOPE
    out = write_report(ctx, args.lane, rows, details, scopes)
    print(HEADER)
    print((out / "report.md").read_text(encoding="utf-8"))
    print("Written to", out)


if __name__ == "__main__":
    main()
