"""A/B two grading configurations over the SAME golden items (V-054).

Why this exists: "we improved accuracy" is a claim, and a claim needs an
instrument. Comparing two accuracy percentages side by side is the weak way
to do it — most items are graded identically by both arms and contribute
nothing but noise to that comparison. The paired (McNemar) test looks only at
the items where the two arms DISAGREED, which is where the information is.

It also refuses to let a null result be over-read: before interpreting
anything it reports how many discordant items would have been needed for a
significant result to be possible at all.

Usage (from backend/):

    uv run python -m scripts.ab_prompt_compare --fake              # mechanics
    uv run python -m scripts.ab_prompt_compare --live --a v1 --b v2

Every arm grades through the REAL production path (`vote_batch`), never a
reimplementation, so what is measured is what ships.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.checks.agreement import (
    PairedComparison,
    compute_agreement,
    mcnemar_exact,
    required_discordant_pairs,
)
from app.checks.consistency import vote_batch
from app.checks.golden import GoldenItem
from app.checks.semantic import SemanticBatch
from app.config import get_settings
from app.errors import ApiDownError, QuotaExhaustedError
from app.ingest.schemas import TextBlock
from app.llm.base import LLMClient
from app.llm.fake import FakeLLMClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SET_FILE = REPO_ROOT / "context" / "golden" / "set.jsonl"
REPORT_DIR = REPO_ROOT / "context" / "golden" / "reports"


class _Criterion:
    def __init__(self, idx: int, text: str) -> None:
        self.id = idx
        self.text = text
        self.evidence = None


def _load_items() -> list[GoldenItem]:
    if not SET_FILE.is_file():
        sys.exit(f"No golden set at {SET_FILE}.")
    return [
        GoldenItem(**json.loads(line))
        for line in SET_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _grade(item: GoldenItem, idx: int, llm: LLMClient, settings, prompt_version: str):
    """Returns (predicted, escalated, error). `predicted` is None when the
    arm escalated or errored — those are NOT wrong answers and must never be
    scored as such."""
    batch = SemanticBatch(
        label=item.id,
        blocks=[TextBlock(page=1, text=item.excerpt, max_font_size=11, bold_ratio=0.0)],
    )
    try:
        voted = await vote_batch(
            batch,
            [_Criterion(idx, item.criterion_text)],
            llm,
            settings,
            anchor_kind="page",
            prompt_version=prompt_version,
        )
    except (ApiDownError, QuotaExhaustedError) as exc:
        return None, False, str(exc)
    v = voted[0]
    if v.outcome.value == "escalated":
        return None, True, None
    verdict = v.detail.get("verdict")
    return ("pass" if verdict in ("pass", "partial") else "fail"), False, None


def _arm_stats(items, predictions):
    human, judge, abstained = [], [], 0
    for item, (predicted, escalated, error) in zip(items, predictions, strict=True):
        if error is not None:
            continue
        if escalated or predicted is None:
            abstained += 1
            continue
        human.append(item.instructor_grade)
        judge.append(predicted)
    return compute_agreement(human_labels=human, judge_labels=judge, n_abstained=abstained)


def _paired(items, arm_a, arm_b) -> tuple[PairedComparison, list[str]]:
    """Only items BOTH arms actually decided can be paired. An item one arm
    escalated is not evidence that the other arm is better — it is a
    different kind of answer, and folding it in would reward whichever arm
    guesses more."""
    both_correct = only_a = only_b = both_wrong = 0
    notes = []
    for item, (pa, ea, era), (pb, eb, erb) in zip(items, arm_a, arm_b, strict=True):
        if era or erb or ea or eb or pa is None or pb is None:
            notes.append(item.id)
            continue
        a_ok = pa == item.instructor_grade
        b_ok = pb == item.instructor_grade
        if a_ok and b_ok:
            both_correct += 1
        elif a_ok and not b_ok:
            only_a += 1
        elif b_ok and not a_ok:
            only_b += 1
        else:
            both_wrong += 1
    return (
        PairedComparison(
            both_correct=both_correct,
            only_a_correct=only_a,
            only_b_correct=only_b,
            both_wrong=both_wrong,
        ),
        notes,
    )


async def run(*, live: bool, arm_a: str, arm_b: str) -> None:
    settings = get_settings()
    if live:
        if settings.veridical_fake_llm:
            sys.exit("--live requires VERIDICAL_FAKE_LLM=0 and a real GEMINI_API_KEY.")
        from app.llm import get_llm_client

        llm = get_llm_client(settings)
    else:
        llm = FakeLLMClient()

    items = _load_items()
    results = {}
    for arm in (arm_a, arm_b):
        print(f"\n=== arm {arm} ===", flush=True)
        arm_results = []
        for idx, item in enumerate(items):
            predicted, escalated, error = await _grade(item, idx, llm, settings, arm)
            arm_results.append((predicted, escalated, error))
            mark = (
                "!"
                if error
                else ("?" if escalated else ("+" if predicted == item.instructor_grade else "x"))
            )
            print(
                f"  [{mark}] {item.id}: {predicted}  (golden {item.instructor_grade})", flush=True
            )
        results[arm] = arm_results

    stats_a = _arm_stats(items, results[arm_a])
    stats_b = _arm_stats(items, results[arm_b])
    comparison, unpaired = _paired(items, results[arm_a], results[arm_b])
    test = mcnemar_exact(comparison)
    floor = required_discordant_pairs()

    def fmt(value):
        return f"{value:.3f}" if value is not None else "N/A"

    mode_label = (
        "LIVE (real Gemini)" if live else "FAKE (mechanics only — numbers are not evidence)"
    )
    lines = [
        f"# A/B prompt comparison — {arm_a} vs {arm_b} — {datetime.now(UTC).date().isoformat()}",
        "",
        f"Mode: {mode_label}",
        "",
        "## Per-arm (independent view)",
        "",
    ]
    for name, stats in ((arm_a, stats_a), (arm_b, stats_b)):
        interval = stats.accuracy_ci.as_percent() if stats.accuracy_ci else "N/A"
        accuracy = f"{stats.accuracy:.1%}" if stats.accuracy is not None else "N/A"
        lines.append(
            f"- **{name}**: selective accuracy {accuracy} 95% CI {interval} · "
            f"coverage {stats.coverage:.1%} · κ {fmt(stats.kappa)} · MCC {fmt(stats.mcc)} · "
            f"TP {stats.matrix.tp} FP {stats.matrix.fp} TN {stats.matrix.tn} FN {stats.matrix.fn}"
        )
    lines += [
        "",
        "## Paired comparison (the actual test)",
        "",
        f"- Items decided by BOTH arms: {comparison.n}"
        + (
            f" ({len(unpaired)} excluded — escalated/errored in at least one arm)"
            if unpaired
            else ""
        ),
        f"- Both correct: {comparison.both_correct} · both wrong: {comparison.both_wrong}",
        f"- **Discordant**: {arm_a} only {comparison.only_a_correct}, "
        f"{arm_b} only {comparison.only_b_correct} (total {comparison.n_discordant})",
        "- McNemar exact two-sided p: "
        + (f"{test.p_value:.4f}" if test.p_value is not None else "N/A"),
        f"- Significant at α=0.05: **{'YES' if test.significant else 'NO'}**",
    ]
    if test.note:
        lines.append(f"- ⚠️ {test.note}")
    if comparison.n_discordant < floor:
        lines.append(
            f"- ⚠️ **This run could not have produced a significant result.** "
            f"{floor} discordant items are required before a two-sided exact test can "
            f"reach p<0.05 even if EVERY one favours the same arm; this run had "
            f"{comparison.n_discordant}. Read the direction, not the p-value, and do "
            f"not report 'no difference'."
        )
    lines += [
        "",
        "## How to read this",
        "",
        "Accuracy percentages per arm are the weak comparison: most items are graded",
        "identically by both, so the difference between two rates is mostly noise.",
        "The discordant counts above are the evidence. A non-significant result means",
        "'not detectable at this sample size' — never 'the two are equivalent'.",
        "",
    ]
    markdown = "\n".join(lines)
    print("\n" + markdown)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "live" if live else "fake"
    out = REPORT_DIR / f"{datetime.now(UTC).date().isoformat()}-ab-{arm_a}-vs-{arm_b}-{suffix}.md"
    out.write_text(markdown, encoding="utf-8")
    print(f"Report written to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fake", action="store_true", help="zero-quota mechanics run")
    mode.add_argument("--live", action="store_true", help="real Gemini — spends quota")
    parser.add_argument("--a", default="v1", help="baseline prompt version")
    parser.add_argument("--b", default="v2", help="candidate prompt version")
    args = parser.parse_args()
    asyncio.run(run(live=args.live, arm_a=args.a, arm_b=args.b))


if __name__ == "__main__":
    main()
