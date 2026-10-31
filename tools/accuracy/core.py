"""Shared pieces of the module accuracy harness: metric rows with Wilson
intervals, host extractions, plant placement and the report writer.

Every number this harness produces is measured on known-answer cases planted
into stored extractions. It is not instructor-labelled accuracy, and because
plants go into the extraction rather than the PDF it tests the checks, not
PDF extraction. The report says so before any number.
"""

from __future__ import annotations

import json
import random
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.checks.accuracy_stats import wilson_interval
from app.ingest.schemas import ExtractionResult

# D-016: below this many units a rate is indicative only, never a baseline.
MIN_VIABLE_N = 30
HEADER = (
    "Known-answer/seeded measurement, NOT instructor-labelled accuracy. Cases are "
    "planted into stored extractions, so this tests the checks, not PDF extraction."
)


@dataclass
class MetricRow:
    lane: str
    metric: str
    unit: str
    numerator: int
    denominator: int
    rate: float | None
    ci_low: float | None
    ci_high: float | None
    indicative: bool
    split: str = "all"
    note: str = ""
    extra: dict = field(default_factory=dict)


def rate_row(lane, metric, unit, numerator, denominator, *, split="all", note="", extra=None):
    interval = wilson_interval(numerator, denominator) if denominator else None
    return MetricRow(
        lane=lane,
        metric=metric,
        unit=unit,
        numerator=numerator,
        denominator=denominator,
        rate=numerator / denominator if denominator else None,
        ci_low=interval.low if interval else None,
        ci_high=interval.high if interval else None,
        indicative=denominator < MIN_VIABLE_N,
        split=split,
        note=note,
        extra=extra or {},
    )


@dataclass
class Context:
    golden: Path
    output: Path
    seed: int
    code_commit: str

    def lane_config(self, lane: str) -> dict:
        """A lane's own settings (model, repeats, call caps), from
        `<golden>/<lane>/config.json` when it exists."""
        path = self.golden / lane / "config.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @property
    def extractions(self) -> Path:
        return self.golden / "extractions"

    def hosts(self) -> dict[str, Path]:
        return {path.stem: path for path in sorted(self.extractions.glob("*.json"))}


def load_extraction(path: Path) -> ExtractionResult:
    return ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))


def body_pages(extraction: ExtractionResult) -> list[int]:
    """Pages from the middle of the document that carry body text: where a
    thesis reports its results, away from front matter and references."""
    pages = sorted({b.page for b in extraction.blocks if b.page and not b.is_furniture})
    if not pages:
        return []
    low, high = pages[len(pages) // 5], pages[(len(pages) * 9) // 10]
    return [p for p in pages if low <= p <= high]


def spaced_pages(rng: random.Random, pages: list[int], count: int, gap: int) -> list[int]:
    """`count` pages at least `gap` apart, so one finding cannot cover two plants."""
    for _ in range(200):
        picked = sorted(rng.sample(pages, min(count, len(pages))))
        if all(b - a >= gap for a, b in zip(picked, picked[1:], strict=False)):
            return picked
    raise ValueError("Not enough spaced body pages for the plants")


def split_for(rng: random.Random) -> str:
    """Half the plants are for development, half are held out for reporting."""
    return "dev" if rng.random() < 0.5 else "holdout"


def git_commit(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_report(ctx: Context, lanes: list[str], rows: list[MetricRow], details: dict) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ctx.output / f"{stamp}-{'-'.join(lanes)}"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "header": HEADER,
        "generated_at": stamp,
        "code_commit": ctx.code_commit,
        "seed": ctx.seed,
        "rows": [asdict(row) for row in rows],
        "details": details,
    }
    (out / "report.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    (out / "report.md").write_text(_markdown(payload), encoding="utf-8")
    return out


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _markdown(payload: dict) -> str:
    lines = [
        "# Module accuracy",
        "",
        f"> {payload['header']}",
        "",
        f"Code {payload['code_commit']}, seed {payload['seed']}, "
        f"generated {payload['generated_at']}.",
        "",
        "| Lane | Metric | Split | Result | 95% Wilson | n | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in payload["rows"]:
        caveat = "INDICATIVE ONLY: " if row["indicative"] else ""
        interval = (
            f"{_percent(row['ci_low'])} to {_percent(row['ci_high'])}"
            if row["ci_low"] is not None
            else "n/a"
        )
        lines.append(
            f"| {row['lane']} | {row['metric']} | {row['split']} | "
            f"{caveat}{row['numerator']}/{row['denominator']} = {_percent(row['rate'])} | "
            f"{interval} | {row['denominator']} | {row['note']} |"
        )
    under = [
        row
        for row in payload["rows"]
        if row["split"] == "holdout" and row["ci_low"] is not None and row["ci_low"] < 0.90
    ]
    lines += ["", "## Under 90% (holdout lower bound)", ""]
    lines += [
        f"- {row['lane']} {row['metric']}: lower bound {_percent(row['ci_low'])}. {row['note']}"
        for row in under
    ] or ["- None."]
    return "\n".join(lines) + "\n"
