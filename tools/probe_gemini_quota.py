"""Measure the REAL free-tier limits of a Gemini API key, per model.

Why this exists: the documented free-tier numbers drift, and on 2026-07-26
they were wrong for this project by 70x — `gemini-3.5-flash` allows exactly
20 requests/day, not the ~1,500 the design had assumed. Google's public
rate-limit page no longer publishes a per-model table (it defers to the AI
Studio dashboard), so the only trustworthy number is a measured one.

This script is the measurement. Re-run it whenever a capacity claim needs to
be defended, or before adding a model to `app/llm/data/model_pool.json`.

    python tools/probe_gemini_quota.py --models gemini-3.5-flash --budget 25
    python tools/probe_gemini_quota.py --list

Method: send minimal `generateContent` calls spaced under the model's
per-minute limit, and count how many succeed before a per-DAY 429. `--budget`
caps the spend so a genuinely large quota is refuted cheaply (e.g. 25
successes disproves a 20/day cap) instead of being exhausted for the day.

Reads GEMINI_API_KEY from the environment or from a .env file. Stdlib only —
no dependency on the backend's virtualenv.
"""

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
# Google returns per-minute and per-day caps both as 429; only the quotaId
# tells them apart.
DAILY_MARKER = "PerDay"


def load_api_key(env_file: pathlib.Path | None) -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    if env_file and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def list_models(key: str) -> list[str]:
    with urllib.request.urlopen(f"{API_ROOT}/models?key={key}&pageSize=200", timeout=60) as r:
        data = json.loads(r.read())
    return sorted(
        m["name"].removeprefix("models/")
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    )


def call_once(key: str, model: str) -> tuple[int, str]:
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": "Reply with the single digit 1."}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 4},
        }
    ).encode()
    req = urllib.request.Request(
        f"{API_ROOT}/models/{model}:generateContent?key={key}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:3000]


def violations(raw: str) -> list[dict]:
    """The QuotaFailure details are the evidence — quotaId names WHICH limit
    was hit and quotaValue names its size."""
    try:
        details = json.loads(raw).get("error", {}).get("details", [])
    except json.JSONDecodeError:
        return []
    found: list[dict] = []
    for detail in details:
        if "QuotaFailure" in detail.get("@type", ""):
            found.extend(detail.get("violations", []))
    return found


def probe(key: str, model: str, budget: int, spacing: float) -> dict:
    print(f"\n=== {model} (budget {budget}, spacing {spacing}s) ===", flush=True)
    succeeded = 0
    for attempt in range(1, budget + 1):
        status, raw = call_once(key, model)
        if status == 200:
            succeeded += 1
            print(f"  {attempt:>4} ok", flush=True)
            if attempt < budget:
                time.sleep(spacing)
            continue

        found = violations(raw)
        daily = [v for v in found if DAILY_MARKER in v.get("quotaId", "")]
        print(f"  {attempt:>4} HTTP {status}", flush=True)
        for violation in found:
            print(f"       {json.dumps(violation)}", flush=True)
        if not found:
            print(f"       raw: {raw[:400]}", flush=True)
        return {
            "model": model,
            "succeeded": succeeded,
            "http_status": status,
            "hit_daily_cap": bool(daily),
            "daily_quota_value": (daily[0].get("quotaValue") if daily else None),
            "violations": found,
        }
    return {
        "model": model,
        "succeeded": succeeded,
        "http_status": 200,
        "hit_daily_cap": False,
        "daily_quota_value": None,
        "violations": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="*", default=[], help="model ids to probe")
    parser.add_argument("--list", action="store_true", help="list callable models and exit")
    parser.add_argument("--budget", type=int, default=25, help="max calls to spend per model")
    parser.add_argument(
        "--spacing", type=float, default=6.0, help="seconds between calls (stay under RPM)"
    )
    parser.add_argument("--env-file", type=pathlib.Path, default=pathlib.Path(".env"))
    parser.add_argument("--out", type=pathlib.Path, help="write results as JSON here")
    args = parser.parse_args()

    key = load_api_key(args.env_file)
    if not key:
        print("GEMINI_API_KEY not found (environment or --env-file).", file=sys.stderr)
        return 2

    if args.list:
        for model in list_models(key):
            print(model)
        return 0

    if not args.models:
        print("Nothing to probe: pass --models, or --list to see what is callable.")
        return 2

    results = [probe(key, model, args.budget, args.spacing) for model in args.models]

    print("\n===== measured =====")
    for result in results:
        if result["hit_daily_cap"]:
            verdict = f"daily cap = {result['daily_quota_value']}"
        elif result["http_status"] != 200:
            verdict = f"stopped on HTTP {result['http_status']} (not a per-day cap)"
        else:
            verdict = f"daily cap > {result['succeeded']} (budget spent, no wall)"
        print(f"  {result['model']:<30} {result['succeeded']:>5} ok   {verdict}")

    if args.out:
        args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
