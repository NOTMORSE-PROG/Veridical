"""Golden-dataset regression harness (V-025, TESTING.md Sec2): grades
`context/golden/set.jsonl` through the EXACT SAME production code path as
a real manuscript (`app.checks.consistency.vote_batch` -- N=2+tie-break
voting, V-023's escalation gate -- never a reimplementation), computes
agreement % (decided items only -- an escalation is a non-answer, not a
wrong answer), a per-criterion breakdown, and a disagreement dump, then
writes a dated report to `context/golden/reports/`.

Placed under `backend/scripts/` (not root `tools/`, which the ticket
suggests) for the same reason V-024's replay tool is here: it needs the
app's Python package and DB config, matching the established
`backend/scripts/{ingest_file,seed_dev,replay_call}.py` convention.

MANDATORY before any `semantic_grading` prompt_version bump ships (ticket
gate) -- run it, diff the agreement, and record the number in STATE.md
before changing the prompt. Not run on every push (workflow_dispatch
only in CI once this project has a CI -- real runs spend real quota).

Usage (from backend/):

    uv run python -m scripts.golden_harness --fake   # zero-quota mechanics dry run
    uv run python -m scripts.golden_harness --live    # real Gemini -- the actual baseline
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.checks.consistency import vote_batch
from app.checks.golden import GoldenItem, GoldenPrediction, report_as_markdown, score_golden_set
from app.checks.promotion import evaluate_promotions
from app.checks.semantic import SemanticBatch
from app.config import get_settings
from app.errors import ApiDownError, QuotaExhaustedError
from app.ingest.schemas import TextBlock
from app.llm.base import LLMClient
from app.llm.fake import FakeLLMClient

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "context" / "golden"
SET_FILE = GOLDEN_DIR / "set.jsonl"
REPORTS_DIR = GOLDEN_DIR / "reports"

PROVISIONAL_NOTE = (
    "**PROVISIONAL** — graded by the AI session that built this harness "
    "(2026-07-25), NOT a real Capstone Instructor (see `set.README.md`). "
    "Real golden-data access is FEATURES.md Sec10 open question #3, "
    "pending adviser/instructor access — an owner action, not obtainable "
    "this session. Never cite this number as real-world grading accuracy."
)


@dataclass
class _GoldenCriterion:
    """Grading only needs `.id`/`.text`/`.evidence` (same minimal shape
    tests use) — a golden item isn't a real `rubric.Criterion` row."""

    id: int
    text: str
    evidence: str | None = None


def _load_items() -> list[GoldenItem]:
    if not SET_FILE.is_file():
        sys.exit(f"No golden set at {SET_FILE} — see set.README.md.")
    items = []
    for line in SET_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        items.append(GoldenItem(**json.loads(line)))
    if not items:
        sys.exit(f"{SET_FILE} is empty.")
    return items


async def _grade_item(item: GoldenItem, idx: int, llm: LLMClient, settings) -> GoldenPrediction:
    criterion = _GoldenCriterion(id=idx, text=item.criterion_text)
    batch = SemanticBatch(
        label=item.id,
        blocks=[TextBlock(page=1, text=item.excerpt, max_font_size=11, bold_ratio=0.0)],
    )
    voted = await vote_batch(batch, [criterion], llm, settings, anchor_kind="page")
    v = voted[0]
    escalated = v.outcome.value == "escalated"
    predicted = None
    if not escalated:
        verdict = v.detail.get("verdict")
        predicted = "pass" if verdict in ("pass", "partial") else "fail"
    return GoldenPrediction(
        item=item,
        escalated=escalated,
        predicted=predicted,
        agreement=v.detail.get("agreement"),
        votes=v.detail.get("votes", []),
    )


async def run(*, live: bool) -> None:
    settings = get_settings()
    if live:
        if settings.veridical_fake_llm:
            sys.exit(
                "--live requires VERIDICAL_FAKE_LLM=0 and a real GEMINI_API_KEY "
                "in the environment (it spends real Gemini quota)."
            )
        from app.llm import get_llm_client

        llm = get_llm_client(settings)
    else:
        llm = FakeLLMClient()  # zero quota, mechanics only — every fake row grades identically

    items = _load_items()
    predictions: list[GoldenPrediction] = []
    for idx, item in enumerate(items):
        # A live run is genuinely quota-aware (ticket responsibility): an
        # api_down/quota_exhausted mid-run must never crash the whole
        # harness OR silently read as "escalated" (TESTING.md §5) — it's
        # recorded as its own honest state and the run continues so the
        # rest of the set still produces a partial report.
        try:
            pred = await _grade_item(item, idx, llm, settings)
        except (ApiDownError, QuotaExhaustedError) as exc:
            pred = GoldenPrediction(
                item=item, escalated=False, predicted=None, agreement=None, votes=[], error=str(exc)
            )
            print(f"[!] {item.id}: ERRORED — {exc}")
            predictions.append(pred)
            continue
        predictions.append(pred)
        mark = "?" if pred.escalated else ("+" if pred.predicted == item.instructor_grade else "x")
        print(
            f"[{mark}] {item.id}: predicted={pred.predicted} "
            f"escalated={pred.escalated} golden={item.instructor_grade}"
        )

    report = score_golden_set(predictions)
    today = datetime.now(UTC).date().isoformat()
    mode = "live" if live else "fake"
    title = f"Golden-set report — {mode} mode — {today}"
    markdown = report_as_markdown(report, title=title, provisional_note=PROVISIONAL_NOTE)

    # D-012 mechanism #1: the promotion gate runs every time, honestly —
    # see promotion.py's own note on why this is currently always empty.
    promotions = evaluate_promotions([], dated=today, settings=settings)
    markdown += (
        f"\n\n## Tier promotions ({len(promotions)})\n"
        "No Tier 0/1 class has a binary verdict function yet to shadow-"
        "evaluate against these golden labels — the promotion GATE "
        "(`app/checks/promotion.py`) is built, tested, and runs every "
        "harness pass; it has nothing to promote until a future ticket "
        "gives a signal class a pass/fail mapping.\n"
    )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"{today}-{mode}.md"
    out_path.write_text(markdown, encoding="utf-8")

    print()
    print(markdown)
    print(f"\nReport written to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fake", action="store_true", help="zero-quota mechanics dry run")
    mode.add_argument("--live", action="store_true", help="real Gemini calls — the actual baseline")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    asyncio.run(run(live=args.live))


if __name__ == "__main__":
    main()
