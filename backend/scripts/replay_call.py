"""Reconstruct and (optionally) re-execute a recorded `audit_log` call —
the V-024 debugging tool for tracing a bad grade back to exactly what was
sent to the model.

Every field a call needs (model, temperature, prompt, context, prompt
version) is stored on the audit row itself (V-022 widened `LLMQueue`'s
audit write to include `prompt` + `context`, not just the response) —
this script never has to guess or reconstruct a prompt from scratch.

Usage (from backend/, DB migrated):

    uv run python -m scripts.replay_call 42                # print only
    uv run python -m scripts.replay_call 42 --fake          # replay through FakeLLMClient
    uv run python -m scripts.replay_call 42 --live           # replay through REAL Gemini
                                                               (spends one real call)

`--fake` and `--live` both hash their own response the same way
`LLMQueue._input_hash` would and compare it to the STORED `input_hash` —
"byte-identical" here means the reconstructed CALL (model, temperature,
prompt, context) hashes identically to what was actually sent, which is
what the stored hash actually attests to; the fake path additionally
prints the exact fixture response, and the live path prints Gemini's
fresh response for side-by-side inspection (temperature=0 does not
guarantee byte-identical generation on a live model — that promise is
about the CALL, not about model determinism, which VERIDICAL doesn't
control).
"""

import argparse
import asyncio
import hashlib
import json
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import sqlalchemy_url
from app.llm.fake import FakeLLMClient
from app.models.audit import AuditLog


def _input_hash(prompt_version: str, model: str, prompt: str, context: dict) -> str:
    # Mirrors `LLMQueue._input_hash` exactly (app/llm/queue.py) — kept as
    # a literal copy rather than an import so this debug tool never
    # accidentally shares mutable state with the live queue.
    payload = json.dumps(
        {"prompt_version": prompt_version, "model": model, "prompt": prompt, "context": context},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def run(audit_id: int, *, fake: bool, live: bool) -> None:
    settings = get_settings()
    engine = create_async_engine(sqlalchemy_url(settings.database_url))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        row = await session.scalar(select(AuditLog).where(AuditLog.id == audit_id))
    await engine.dispose()

    if row is None:
        sys.exit(f"No audit_log row with id {audit_id}.")

    payload = row.payload or {}
    prompt_type = payload.get("prompt_type", "?")
    model = payload.get("model", "?")
    temperature = payload.get("temperature")
    context = payload.get("context", {})
    prompt = payload.get("prompt", "")
    prompt_version = row.prompt_version or "unversioned"

    print(f"audit_log id={row.id}  event={row.event_type}  check_run_id={row.check_run_id}")
    print(f"prompt_type={prompt_type}  model={model}  temperature={temperature}")
    print(f"prompt_version={prompt_version}  context={context}")
    print(f"stored input_hash={row.input_hash}")
    if not prompt:
        print(
            "\n(no `prompt` field on this row — it predates V-022's audit widening; "
            "nothing to replay, only the response below.)"
        )
    recomputed = _input_hash(prompt_version, model, prompt, context)
    match = "MATCH" if recomputed == row.input_hash else "MISMATCH"
    print(f"recomputed input_hash={recomputed}  ({match})")
    print(f"\n--- prompt ---\n{prompt}")
    print(f"\n--- stored response ---\n{json.dumps(payload.get('response'), indent=2)}")

    if fake:
        response = await FakeLLMClient().complete(
            prompt_type, prompt, prompt_version=prompt_version, **context
        )
        print(f"\n--- fake-replay response ---\n{json.dumps(response, indent=2)}")

    if live:
        if not settings.gemini_api_key:
            sys.exit("--live requires GEMINI_API_KEY to be set (spends one real Gemini call).")
        from app.llm.transport import GeminiTransport

        print("\n(--live: spending one real Gemini call against the pinned model/temperature)")
        transport = GeminiTransport(
            settings.gemini_api_key, timeout_seconds=settings.gemini_request_timeout_seconds
        )
        response = await transport.generate(
            model=model, prompt=prompt, temperature=temperature or 0.0, **context
        )
        print(f"\n--- live-replay response ---\n{json.dumps(response, indent=2)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit_id", type=int)
    parser.add_argument("--fake", action="store_true", help="replay through FakeLLMClient")
    parser.add_argument(
        "--live", action="store_true", help="replay through real Gemini (spends quota)"
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    asyncio.run(run(args.audit_id, fake=args.fake, live=args.live))


if __name__ == "__main__":
    main()
