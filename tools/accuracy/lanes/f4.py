"""F4 internal agreement on planted known-answer cases. SPENDS REAL GEMINI QUOTA.

The case set comes from `tools.accuracy.f4_cases` (`<golden>/f4/cases.json`).
For every planted copy the product's own statement extraction, pairing and
context building run unchanged; a plant is "reached" only when its objective
is paired with the planted sentence. The reached pairs then go through the
product's own judgment tier (`judge_pairs`: same prompt, same response
validation, same quote checks and confirmation re-ask), in the product's
batch size.

What differs from production, on purpose: the response cache, retries and
model failover are off (one attempt per call; a failed batch is reported, not
retried), the model is pinned, and the pairs are shuffled across copies into
batches instead of one manuscript's pairs per call. Context is built per pair,
so mixing copies does not change what each pair is shown. The reading-order
source gate that stops F4 on some real theses is not applied, so this
measures pairing and judgement, not coverage.

Guards before the first call: the local test key, never the production key;
a planned call count within the lane's cap; every attempt reserved in the
shared daily budget; a 429 or the budget ends the run, and the pairs not yet
judged are reported as not judged. Model answers are cached by prompt under
`<golden>/f4/responses`, so a rerun of the same prompts spends nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.accuracy.budget import Budget, BudgetExceeded
from tools.accuracy.core import (
    Context,
    MetricRow,
    cluster_bootstrap,
    load_extraction,
    rate_row,
    real_precision_rows,
)
from tools.accuracy.f4_cases import KIND_RECALL, KIND_RESTATE, PROBE_KINDS, apply_case

from app.checks.agreement import pair
from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import extract_statements
from app.config import Settings
from app.errors import QuotaExhaustedError
from app.llm.base import LLMClient
from app.llm.queue import TransportDailyQuotaExhausted, TransportRateLimited

LANE = "f4"
SCOPE = (
    "Known-answer cases planted into stored extractions of the permitted theses, judged by "
    "the product's own F4 judgment tier on the local test key: tests pairing and judgement, "
    "not PDF extraction and not the reading-order source gate. Labels come from templates, "
    "NOT instructors."
)
FLAGGED = ("contradictory", "partial")
LIMITATIONS = (
    "Labels come from templates, not instructors. Recall plants say outright that the "
    "objective was not met and reuse the objective's own verb phrase, so they are easier "
    "than real contradictions; this is not general detection of unmet objectives.",
    "28 objectives from four theses, most plants on one host that is a public doctoral "
    "dissertation, not a capstone. Rates are marked indicative because the objectives, not "
    "the plants, are the independent units.",
    "This holdout is no longer pristine: the version 2 design (two matches per objective, "
    "scope ranking, the re-ask) was shaped by the version 1 misses in both halves. A fresh, "
    "sealed holdout on objectives not used here is needed for a claim.",
    "Scope retrieval is measured where the explaining sentence repeats the finding's own "
    "words, as the templates do; real theses may explain a gap in other words.",
    "Some 'explained in context' answers on recall plants cite the host thesis's real "
    "limitations; whether those are right is for two raters, not this lane.",
    "Each plant is judged in a copy holding only that plant; batches mix copies, and the "
    "reading-order source gate is not applied.",
)
# Shorter pieces of a planted context frame are heading words, not content.
CONTEXT_SENTENCE_MIN = 25
ANSWERED = (*FLAGGED, *pair.SILENT_VERDICTS, "cannot_determine")
DEFAULTS = {
    "model": "gemini-3.5-flash-lite",
    "max_calls": 45,
    "env_file": "C:/PROJECTS/Veridical/.env",
    "key_variable": "GEMINI_API_KEY",
    "production_key_variable": "GEMINI_API_KEY_PROD",
    "bootstrap_resamples": 2000,
}


def _env_value(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


class BudgetedModel(LLMClient):
    """One attempt per call, paid for in the shared budget before it is sent;
    answers are cached by prompt. A stop is raised as quota exhaustion, which
    the product already treats as the end of the run."""

    def __init__(self, transport: Any, budget: Budget, model: str, temperature: float, cache):
        self._transport, self._budget = transport, budget
        self._model, self._temperature, self._cache = model, temperature, cache
        self._cache.mkdir(parents=True, exist_ok=True)
        self.attempts = self.cache_hits = 0
        self.stop_reason: str | None = None

    async def complete(self, prompt_type, prompt, *, prompt_version="unversioned", **context):
        path = self._cache / (
            hashlib.sha256(
                f"{self._model}\n{self._temperature}\n{prompt_version}\n{prompt}".encode()
            ).hexdigest()
            + ".json"
        )
        if path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["payload"]
        if self.stop_reason:
            raise QuotaExhaustedError(self.stop_reason)
        try:
            self._budget.reserve(LANE)
        except BudgetExceeded as exc:
            self.stop_reason = f"daily budget reached: {exc}"
            raise QuotaExhaustedError(self.stop_reason) from exc
        self.attempts += 1
        try:
            result = await self._transport.generate(
                model=self._model, prompt=prompt, temperature=self._temperature
            )
        except (TransportRateLimited, TransportDailyQuotaExhausted) as exc:
            self.stop_reason = f"429 from the provider ({type(exc).__name__})"
            raise QuotaExhaustedError(self.stop_reason) from exc
        path.write_text(
            json.dumps({"model": self._model, "payload": result.payload}), encoding="utf-8"
        )
        return result.payload


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def reach(copies: list[dict], hosts: dict[str, Path], settings: Settings, cues) -> list[dict]:
    """Run extraction, pairing and context on every plant; one record per plant.

    Each plant is judged in a copy of the thesis that holds that plant alone.
    With several plants in one copy, a scope sentence planted for one case was
    shown as context for another and "explained" it, which no real manuscript
    would do."""
    records = []
    singles = [{**copy, "plants": [plant]} for copy in copies for plant in copy["plants"]]
    for copy in singles:
        planted = apply_case(load_extraction(hosts[copy["host"]]), copy)
        found = extract_statements(planted, settings=settings, cues=cues)
        candidates, _, _ = pair.generate_candidates(
            found.intents,
            found.outcomes,
            similarity_floor=settings.agreement_pairing_similarity_floor,
            max_candidates_per_intent=settings.agreement_pairing_max_candidates_per_intent,
            settings=settings,
            status_patterns=cues.objective_status_patterns,
        )
        planted_text = {
            _norm(b["text"])[:60]: p["plant_id"] for p in copy["plants"] for b in p["blocks"]
        }
        wanted = []
        for plant in copy["plants"]:
            result = next(b for b in plant["blocks"] if b["role"] == "result")
            key = _norm(result["text"])[:60]
            mine = [c for c in candidates if _norm(c.intent.text) == _norm(plant["intent_text"])]
            hit = next((c for c in mine if key in _norm(c.outcome.text)), None)
            # Losing the pairing to another plant in the same copy is an artefact of
            # planting three cases together; losing it to the thesis's own sentence
            # is how the product behaves.
            rival = next(
                (planted_text[k] for c in mine for k in planted_text if k in _norm(c.outcome.text)),
                None,
            )
            reason = None
            if not hit:
                reason = (
                    "objective not extracted"
                    if not any(_norm(s.text) == _norm(plant["intent_text"]) for s in found.intents)
                    else "no pair above the similarity floor"
                    if not mine
                    else f"paired with another plant ({rival})"
                    if rival
                    else "paired with a sentence of the thesis"
                )
            record = {
                "plant_id": plant["plant_id"],
                "kind": plant["kind"],
                "label": plant["label"],
                "split": plant["split"],
                "objective": f"{plant['host']}:{plant['objective_index']}",
                "reached": hit is not None,
                "not_reached_because": reason,
                "similarity": round(hit.similarity, 3) if hit else None,
                "forced_pair": hit.forced if hit else None,
            }
            records.append(record)
            if hit:
                wanted.append((record, hit))
        with_context = pair.with_context([c for _, c in wanted], planted, cues, settings)
        plants = {p["plant_id"]: p for p in copy["plants"]}
        for (record, _), candidate in zip(wanted, with_context, strict=True):
            record["_candidate"] = candidate
            # Did the judge see the context this plant was built with? A frame can
            # open with a heading word ("Limitations."), so any substantial
            # sentence of it counts.
            planted_context = [
                sentence
                for b in plants[record["plant_id"]]["blocks"]
                if b["role"] == "context"
                for sentence in _norm(b["text"]).split(". ")
                if len(sentence) >= CONTEXT_SENTENCE_MIN
            ]
            if planted_context:
                shown = _norm(candidate.context.text())
                record["planted_context_shown"] = any(
                    sentence[:60] in shown for sentence in planted_context
                )
    return records


def _rows(records, *, split, seed, resamples, always_one):
    rows: list[MetricRow] = []
    mine = [r for r in records if r["split"] == split]
    # A dry run judged nothing: only the local pairing figures mean anything,
    # and a recall made only of unreached plants would read as a real 0%.
    judged_any = any("verdict" in r for r in records)

    def add(metric, unit, cases, success, note, *, unreached_is_miss=False):
        if not judged_any:
            return
        # A plant the judge never saw is a miss for recall; a pair sent but left
        # unanswered (a stop or a failed batch) is not scored at all.
        judged = [
            r
            for r in cases
            if r.get("verdict") in ANSWERED or (unreached_is_miss and not r["reached"])
        ]
        hits = [r for r in judged if success(r)]
        interval = cluster_bootstrap(
            [(r["objective"], success(r)) for r in judged], seed=seed, resamples=resamples
        )
        extra = {"objectives": len({r["objective"] for r in judged})}
        if interval:
            extra["cluster_bootstrap"] = [round(interval[0], 3), round(interval[1], 3)]
            note += (
                f" Bootstrap by objective {interval[0] * 100:.1f}% to "
                f"{interval[1] * 100:.1f}% ({extra['objectives']} objectives)."
            )
        rows.append(
            rate_row(
                LANE,
                metric,
                unit,
                len(hits),
                len(judged),
                split=split,
                note=note,
                extra=extra,
                clusters=extra["objectives"],
            )
        )

    recall = [r for r in mine if r["kind"] == KIND_RECALL]
    add(
        "recall: judged contradictory (primary)",
        "planted unmet objective",
        recall,
        lambda r: r["reached"] and r.get("verdict") == "contradictory",
        (
            f"Not reached counts as a miss. A constant answer scores {always_one:.1%} "
            "accuracy on recall plus controls and 50% balanced accuracy."
        ),
        unreached_is_miss=True,
    )
    add(
        "recall: contradictory or partial (secondary)",
        "planted unmet objective",
        recall,
        lambda r: r["reached"] and r.get("verdict") in FLAGGED,
        "Either finding puts the gap in front of the instructor.",
        unreached_is_miss=True,
    )
    for kind in (KIND_RECALL, KIND_RESTATE, *PROBE_KINDS):
        cases = [r for r in mine if r["kind"] == kind]
        rows.append(
            rate_row(
                LANE,
                f"pairing reach {kind}",
                "plant",
                sum(r["reached"] for r in cases),
                len(cases),
                split=split,
                note="The objective was paired with the planted sentence (local, no call).",
            )
        )
    for kind, label in ((KIND_RESTATE, "specificity"), *((k, "probe") for k in PROBE_KINDS)):
        cases = [r for r in mine if r["kind"] == kind and r["reached"]]
        add(
            f"{label} {kind}: not judged contradictory (primary)",
            "reached plant",
            cases,
            lambda r: r["verdict"] != "contradictory",
            "A high-severity contradiction here would be a false accusation."
            if label == "specificity"
            else "Pre-declared probe of a gap the manuscript explains.",
        )
        add(
            f"{label} {kind}: no finding (secondary)",
            "reached plant",
            cases,
            lambda r: r["verdict"] in pair.SILENT_VERDICTS,
            "Silent: consistent, explained in context, or unrelated.",
        )
    return rows


def run(ctx: Context) -> tuple[list[MetricRow], dict]:
    config = {**DEFAULTS, **ctx.lane_config(LANE)}
    folder = ctx.golden / LANE
    cases = json.loads((folder / "cases.json").read_text(encoding="utf-8"))
    env_file = Path(config["env_file"])
    key = _env_value(env_file, config["key_variable"])
    production_key = _env_value(env_file, config["production_key_variable"])
    if not key:
        raise SystemExit(f"{config['key_variable']} is not set in {env_file}")
    if production_key and key == production_key:
        raise SystemExit("The configured key is the production key; use the local test key")
    settings = Settings(_env_file=None, gemini_api_key=key)
    cues = load_cues(settings.agreement_cues_file)

    records = reach(cases["copies"], ctx.hosts(), settings, cues)
    reached = [r for r in records if r["reached"]]
    random.Random(f"{ctx.seed}-batches").shuffle(reached)
    batches = math.ceil(len(reached) / max(1, settings.agreement_pairing_max_pairs_per_call))
    # Confirmation calls are bounded by the same batching over the pairs judged
    # contradictory, at most every pair once per confirmation round.
    planned = batches * (1 + settings.agreement_contradiction_confirmations)
    if planned > config["max_calls"]:
        raise SystemExit(
            f"Up to {planned} calls planned, over the cap of {config['max_calls']}; "
            "nothing was sent"
        )

    client = None
    if config.get("dry_run"):
        notes = ["dry run: no model call made"]
    else:
        from app.llm.transport import GeminiTransport

        client = BudgetedModel(
            GeminiTransport(key, timeout_seconds=settings.gemini_request_timeout_seconds),
            Budget.from_golden(ctx.golden),
            config["model"],
            settings.gemini_temperature,
            folder / "responses",
        )
        judged, counters = asyncio.run(
            pair.judge_pairs(
                client,
                [r["_candidate"] for r in reached],
                check_run_id=None,
                settings=settings,
                cues=cues,
            )
        )
        for record, item in zip(reached, judged, strict=True):
            if item.verdict is None:
                record["verdict"] = "not_judged"
                continue
            record["verdict"] = item.final
            record["model_verdict"] = item.verdict.verdict
            record["withheld_because"] = item.downgrade
            record["confirmations"] = item.confirmations
            record["reasoning"] = item.verdict.reasoning
        notes = [f"skipped {k}: {v}" for k, v in counters.items() if v]

    scored = [r for r in records if r["kind"] in (KIND_RECALL, KIND_RESTATE)]
    labels = defaultdict(int)
    for record in scored:
        labels[record["label"]] += 1
    always_one = max(labels.values()) / len(scored)
    rows = []
    for split in ("dev", "holdout"):
        rows += _rows(
            records,
            split=split,
            seed=ctx.seed,
            resamples=config["bootstrap_resamples"],
            always_one=always_one,
        )
    # The judge does not always give the same answer twice; a contradiction
    # that changes when asked again is escalated, not reported, and how often
    # that happens is a figure of its own.
    reasked = [r for r in records if r.get("confirmations")]
    if reasked:
        rows.append(
            rate_row(
                LANE,
                "judge stability: contradiction repeated when asked again",
                "re-asked contradiction",
                sum(r["confirmations"][0] == "contradictory" for r in reasked),
                len(reasked),
                note="Any other second answer turns the pair into 'could not determine'.",
                clusters=len({r["objective"] for r in reasked}),
            )
        )
    rows += real_precision_rows(ctx.golden, LANE)
    for record in records:
        record.pop("_candidate", None)
    verdict_counts = defaultdict(lambda: defaultdict(int))
    for record in records:
        default = "not_reached" if not record["reached"] else "not_judged"
        verdict_counts[record["kind"]][record.get("verdict", default)] += 1
    unjudged = sum(1 for r in records if r["reached"] and r.get("verdict") not in ANSWERED)
    warnings = []
    if config.get("dry_run"):
        warnings.append("DRY RUN: no model call was made; only pairing figures are given.")
    elif unjudged:
        warnings.append(
            f"INCOMPLETE RUN: {unjudged} reached pairs were not judged "
            f"({(client.stop_reason if client else None) or 'failed batches'}). They are left "
            "out of every rate, so the rates describe only the judged part."
        )
    return rows, {
        "warnings": warnings,
        "model": config["model"],
        "prompt_version": pair.PROMPT_VERSION,
        "batches": batches,
        "attempts_sent": client.attempts if client else 0,
        "cache_hits": client.cache_hits if client else 0,
        "stop_reason": client.stop_reason if client else None,
        "notes": notes,
        "always_one_label_rate": always_one,
        "verdicts_by_kind": {k: dict(v) for k, v in verdict_counts.items()},
        "plants": records,
    }
