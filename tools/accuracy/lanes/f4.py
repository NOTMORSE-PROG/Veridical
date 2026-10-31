"""F4 internal agreement on planted known-answer cases. SPENDS REAL GEMINI QUOTA.

The case set comes from `tools.accuracy.f4_cases` (`<golden>/f4/cases.json`).
For every planted copy the product's own statement extraction and local
pairing step run unchanged; a plant is "reached" only when its objective is
paired with the planted sentence. Reached pairs are then judged by the
product's own batch judge (`_judge_batch`: same prompt file, same response
validation), shuffled across kinds into batches of the product's batch size.

What differs from production, on purpose: the response cache, retries and
model failover are off (one attempt per batch, a failed batch is reported,
not retried), the model is pinned, and batches mix plants from several copies
instead of one manuscript's pairs. The reading-order source gate that stops F4
on some real theses is not applied, so this measures pairing and judgement,
not coverage.

Guards before the first call: the local test key, never the production key;
a planned call count within the lane's cap; every attempt reserved in the
shared daily budget; a 429 ends the run. Verdicts are cached under
`<golden>/f4/verdicts`, so a rerun spends nothing for batches already judged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from app.checks.agreement.extract import extract_statements
from app.config import Settings
from app.llm.base import LLMClient
from app.llm.queue import TransportDailyQuotaExhausted, TransportRateLimited

LANE = "f4"
SCOPE = (
    "Known-answer cases planted into stored extractions of the permitted theses, judged by "
    "the product's own F4 prompt on the local test key: tests pairing and judgement, not PDF "
    "extraction and not the reading-order source gate. Labels come from templates, NOT "
    "instructors."
)
FLAGGED = ("contradictory", "partial", "cannot_determine")
DEFAULTS = {
    "model": "gemini-3.5-flash-lite",
    "max_calls": 45,
    "env_file": "C:/PROJECTS/Veridical/.env",
    "key_variable": "GEMINI_API_KEY",
    "production_key_variable": "GEMINI_API_KEY_PROD",
    "bootstrap_resamples": 2000,
}


class HarnessStop(RuntimeError):
    """The run must end now: a 429, the budget, or a guard."""


def _env_value(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


class BudgetedModel(LLMClient):
    """One attempt per call, paid for in the shared budget before it is sent."""

    def __init__(self, transport: Any, budget: Budget, model: str, temperature: float):
        self._transport, self._budget = transport, budget
        self._model, self._temperature = model, temperature
        self.attempts = 0
        self.stop_reason: str | None = None

    async def complete(self, prompt_type, prompt, *, prompt_version="unversioned", **context):
        if self.stop_reason:
            raise HarnessStop(self.stop_reason)
        try:
            self._budget.reserve(LANE)
        except BudgetExceeded as exc:
            self.stop_reason = f"daily budget reached: {exc}"
            raise HarnessStop(self.stop_reason) from exc
        self.attempts += 1
        try:
            result = await self._transport.generate(
                model=self._model, prompt=prompt, temperature=self._temperature
            )
        except (TransportRateLimited, TransportDailyQuotaExhausted) as exc:
            self.stop_reason = f"429 from the provider ({type(exc).__name__})"
            raise HarnessStop(self.stop_reason) from exc
        return result.payload


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def reach(copies: list[dict], hosts: dict[str, Path], settings: Settings) -> list[dict]:
    """Run extraction and local pairing on every copy; one record per plant."""
    records = []
    for copy in copies:
        planted = apply_case(load_extraction(hosts[copy["host"]]), copy)
        found = extract_statements(planted, settings=settings)
        candidates, _, _ = pair.generate_candidates(
            found.intents,
            found.outcomes,
            similarity_floor=settings.agreement_pairing_similarity_floor,
            max_candidates_per_intent=settings.agreement_pairing_max_candidates_per_intent,
            settings=settings,
        )
        planted_text = {
            _norm(b["text"])[:60]: p["plant_id"] for p in copy["plants"] for b in p["blocks"]
        }
        for plant in copy["plants"]:
            result = next(b for b in plant["blocks"] if b["role"] == "result")
            key = _norm(result["text"])[:60]
            mine = [c for c in candidates if _norm(c.intent.text) == _norm(plant["intent_text"])]
            hit = next((c for c in mine if key in _norm(c.outcome.text)), None)
            # Losing the pairing to another plant in the same copy is an artefact of
            # planting three cases together; losing it to the thesis's own sentence
            # is how the product behaves.
            rival = next(
                (
                    planted_text[k]
                    for c in mine
                    for k in planted_text
                    if not hit and k in _norm(c.outcome.text)
                ),
                None,
            )
            records.append(
                {
                    "plant_id": plant["plant_id"],
                    "kind": plant["kind"],
                    "label": plant["label"],
                    "split": plant["split"],
                    "objective": f"{plant['host']}:{plant['objective_index']}",
                    "reached": hit is not None,
                    "not_reached_because": None
                    if hit
                    else "objective not extracted"
                    if not any(_norm(s.text) == _norm(plant["intent_text"]) for s in found.intents)
                    else "no pair above the similarity floor"
                    if not mine
                    else f"paired with another plant ({rival})"
                    if rival
                    else "paired with a sentence of the thesis",
                    "paired_with": mine[0].outcome.text if mine and not hit else None,
                    "similarity": round(hit.similarity, 3) if hit else None,
                    "_candidate": hit,
                }
            )
    return records


def _batches(records, size, seed):
    reached = [r for r in records if r["reached"]]
    random.Random(f"{seed}-batches").shuffle(reached)
    return [reached[i : i + size] for i in range(0, len(reached), size)]


async def judge(batches, client, cache_dir: Path, model: str) -> list[str]:
    notes = []
    cache_dir.mkdir(parents=True, exist_ok=True)
    for batch in batches:
        candidates = [r["_candidate"] for r in batch]
        prompt = pair._build_prompt(candidates)
        digest = hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()
        path = cache_dir / f"{digest}.json"
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
        else:
            try:
                verdicts = await pair._judge_batch(client, candidates, check_run_id=None)
            except HarnessStop as stop:
                notes.append(f"stopped: {stop}")
                break
            except pair.PairingError as exc:
                notes.append(f"batch of {len(batch)} failed validation: {exc}")
                for record in batch:
                    record["verdict"] = "parse_failure"
                continue
            except Exception as exc:  # noqa: BLE001 - one failed batch is reported, not retried
                notes.append(f"batch of {len(batch)} failed: {type(exc).__name__}")
                for record in batch:
                    record["verdict"] = "call_failed"
                continue
            stored = {
                "model": model,
                "verdicts": {str(i): v.model_dump() for i, v in verdicts.items()},
            }
            path.write_text(json.dumps(stored, indent=1), encoding="utf-8")
        for i, record in enumerate(batch):
            verdict = stored["verdicts"].get(str(i))
            record["verdict"] = verdict["verdict"] if verdict else "missing"
            record["reasoning"] = verdict["reasoning"] if verdict else None
    return notes


def _rows(records, *, split, seed, resamples, always_one):
    rows: list[MetricRow] = []
    mine = [r for r in records if r["split"] == split]

    def add(metric, unit, cases, success, note, *, unreached_is_miss=False):
        # A plant the judge never saw is a miss for recall; anything sent but
        # left unanswered (a stop or a failed batch) is not scored at all.
        judged = [
            r
            for r in cases
            if r.get("verdict") in (*FLAGGED, "consistent")
            or (unreached_is_miss and not r["reached"])
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
                LANE, metric, unit, len(hits), len(judged), split=split, note=note, extra=extra
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
        "recall: any flag (secondary)",
        "planted unmet objective",
        recall,
        lambda r: r["reached"] and r.get("verdict") in FLAGGED,
        "contradictory, partial or cannot_determine.",
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
            else "Pre-declared probe: the judge never sees the reconciling context.",
        )
        add(
            f"{label} {kind}: judged consistent (secondary)",
            "reached plant",
            cases,
            lambda r: r["verdict"] == "consistent",
            "No flag of any kind.",
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

    records = reach(cases["copies"], ctx.hosts(), settings)
    batches = _batches(records, settings.agreement_pairing_max_pairs_per_call, ctx.seed)
    if len(batches) > config["max_calls"]:
        raise SystemExit(
            f"{len(batches)} batches planned, over the cap of {config['max_calls']}; "
            "nothing was sent"
        )

    if config.get("dry_run"):
        # Pairing reach and the call plan only; nothing is sent.
        client, notes = None, ["dry run: no model call made"]
    else:
        from app.llm.transport import GeminiTransport

        client = BudgetedModel(
            GeminiTransport(key, timeout_seconds=settings.gemini_request_timeout_seconds),
            Budget.from_golden(ctx.golden),
            config["model"],
            settings.gemini_temperature,
        )
        notes = asyncio.run(judge(batches, client, folder / "verdicts", config["model"]))

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
    rows += real_precision_rows(ctx.golden, LANE)
    for record in records:
        record.pop("_candidate", None)
    verdict_counts = defaultdict(lambda: defaultdict(int))
    for record in records:
        verdict_counts[record["kind"]][record.get("verdict", "not_reached")] += 1
    return rows, {
        "model": config["model"],
        "batches": len(batches),
        "attempts_sent": client.attempts if client else 0,
        "stop_reason": client.stop_reason if client else None,
        "notes": notes,
        "always_one_label_rate": always_one,
        "verdicts_by_kind": {k: dict(v) for k, v in verdict_counts.items()},
        "plants": records,
    }
