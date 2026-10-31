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

from app.checks.accuracy_stats import ConfusionMatrix, cohens_kappa, wilson_interval
from app.ingest.schemas import ExtractionResult

# D-016: below this many units a rate is indicative only, never a baseline.
MIN_VIABLE_N = 30
HEADER = (
    "Measured on known-answer cases or internal proxies, NOT instructor-labelled "
    "accuracy. Each lane states its scope below."
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


def rate_row(
    lane, metric, unit, numerator, denominator, *, split="all", note="", extra=None, clusters=None
):
    """One rate with its Wilson interval. When the cases come in clusters
    (several plants built on one objective, every plant on one host), the
    number of clusters is what D-016's floor applies to, not the case count."""
    interval = wilson_interval(numerator, denominator) if denominator else None
    extra = dict(extra or {})
    if clusters is not None:
        extra["clusters"] = clusters
    return MetricRow(
        lane=lane,
        metric=metric,
        unit=unit,
        numerator=numerator,
        denominator=denominator,
        rate=numerator / denominator if denominator else None,
        ci_low=interval.low if interval else None,
        ci_high=interval.high if interval else None,
        indicative=denominator < MIN_VIABLE_N or (clusters is not None and clusters < MIN_VIABLE_N),
        split=split,
        note=note,
        extra=extra,
    )


def cluster_bootstrap(
    outcomes: list[tuple[object, bool]], *, seed: int, resamples: int = 2000, level: float = 0.95
) -> tuple[float, float] | None:
    """A percentile interval for a rate whose cases come in clusters (several
    plants built on one objective), resampling whole clusters so the interval
    reflects how many independent clusters there are, not how many cases."""
    clusters: dict[object, list[bool]] = {}
    for key, success in outcomes:
        clusters.setdefault(key, []).append(success)
    groups = list(clusters.values())
    # With fewer than two clusters, or every cluster at the same rate, every
    # resample is identical and the "interval" is a point. That says nothing
    # about uncertainty, so no interval is given.
    if len(groups) < 2 or len({sum(g) / len(g) for g in groups}) < 2:
        return None
    rng = random.Random(seed)
    rates = []
    for _ in range(resamples):
        picked = [groups[rng.randrange(len(groups))] for _ in groups]
        total = sum(len(g) for g in picked)
        rates.append(sum(sum(g) for g in picked) / total)
    rates.sort()
    tail = (1 - level) / 2
    return rates[int(tail * (resamples - 1))], rates[int((1 - tail) * (resamples - 1))]


def real_precision_rows(golden: Path, lane: str) -> list[MetricRow]:
    """Precision on findings the module raised on real production reports,
    judged against the PDF by two raters with a human tie-break.

    Reads `<golden>/<lane>/real_judgments.json`: a list of
    {"finding": ..., "rater_a": "correct"|"incorrect", "rater_b": ...,
    "tiebreak": "correct"|"incorrect"|null}. Planted cases measure the check
    logic; this is the real-manuscript side, reported beside it, not pooled.
    """
    path = golden / lane / "real_judgments.json"
    metric = "real-manuscript precision"
    if not path.exists():
        return [rate_row(lane, metric, "real finding", 0, 0, note="Not yet judged.")]
    judged = json.loads(path.read_text(encoding="utf-8"))
    both = [j for j in judged if j.get("rater_a") and j.get("rater_b")]
    agree = [j for j in both if j["rater_a"] == j["rater_b"]]
    final = [j["rater_a"] if j["rater_a"] == j["rater_b"] else j.get("tiebreak") for j in both]
    decided = [label for label in final if label]
    counts = {
        "tp": sum(j["rater_a"] == j["rater_b"] == "correct" for j in both),
        "tn": sum(j["rater_a"] == j["rater_b"] == "incorrect" for j in both),
        "fp": sum(j["rater_a"] == "correct" and j["rater_b"] == "incorrect" for j in both),
        "fn": sum(j["rater_a"] == "incorrect" and j["rater_b"] == "correct" for j in both),
    }
    kappa = cohens_kappa(ConfusionMatrix(**counts)) if both else None
    return [
        rate_row(
            lane,
            metric,
            "real finding",
            sum(label == "correct" for label in decided),
            len(decided),
            note=(
                f"Two raters against the PDF: agreed on {len(agree)}/{len(both)}, "
                f"Cohen's kappa {kappa if kappa is None else round(kappa, 2)}; "
                f"{len(both) - len(agree)} settled by the human tie-break."
            ),
            extra={"kappa": kappa, "unresolved": len(both) - len(decided)},
        )
    ]


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
    """The commit a report was produced from, marked "+uncommitted" when the
    measured code differs from it, so a figure is never credited to a commit
    that did not produce it."""
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        changed = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--", "backend/app", "tools"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{head}+uncommitted" if changed else head


def write_report(
    ctx: Context,
    lanes: list[str],
    rows: list[MetricRow],
    details: dict,
    scopes: dict[str, str],
    limitations: dict[str, list[str]] | None = None,
    warnings: dict[str, list[str]] | None = None,
) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ctx.output / f"{stamp}-{'-'.join(lanes)}"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "header": HEADER,
        "generated_at": stamp,
        "code_commit": ctx.code_commit,
        "seed": ctx.seed,
        "scopes": scopes,
        "limitations": limitations or {},
        "warnings": warnings or {},
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
    ]
    if payload["code_commit"].endswith("+uncommitted"):
        lines += [
            "**Measured on code with uncommitted changes: these figures cannot be "
            "reproduced from the commit named above. Do not quote them.**",
            "",
        ]
    for lane, items in payload.get("warnings", {}).items():
        lines += [f"**{lane}: {item}**" for item in items]
        lines += [""] if items else []
    lines += [*[f"- **{lane}**: {scope}" for lane, scope in payload["scopes"].items()], ""]
    for lane, items in payload.get("limitations", {}).items():
        if items:
            lines += [f"Limitations of the {lane} figures:", "", *[f"- {i}" for i in items], ""]
    lines += [
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
    # Development rows are excluded: they are where the checks were looked at.
    # Every reported row (holdout, or measured once over all cases) is listed.
    under = [
        row
        for row in payload["rows"]
        if row["split"] != "dev" and row["ci_low"] is not None and row["ci_low"] < 0.90
    ]
    lines += ["", "## Under 90% (lower bound, holdout and whole-set rows)", ""]
    lines += [
        f"- {row['lane']} {row['metric']} ({row['split']}): lower bound "
        f"{_percent(row['ci_low'])}. Reason: {_reason(row)}"
        for row in under
    ] or ["- None."]
    return "\n".join(lines) + "\n"


def _reason(row: dict) -> str:
    parts = [row["note"]] if row["note"] else []
    if row["extra"].get("misses_by_host"):
        parts.append(
            "Misses by host: "
            + ", ".join(f"{h} {n}" for h, n in sorted(row["extra"]["misses_by_host"].items()))
            + "."
        )
    if row["indicative"]:
        parts.append(
            f"Too few cases or clusters for a baseline (n={row['denominator']}"
            + (f", {row['extra']['clusters']} clusters" if "clusters" in row["extra"] else "")
            + ")."
        )
    return " ".join(parts) or "No reason recorded; see the lane details."
