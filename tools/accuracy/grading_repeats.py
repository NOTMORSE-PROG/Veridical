"""Cache-bypassed grading repeats on the local theses. SPENDS REAL GEMINI QUOTA.

Each repeat runs the product's own check pipeline on every thesis with the
response cache bypassed, so a repeat is an independent regrading rather than
a replay. Only the grading calls are allowed through: any other model call
(internal agreement, for example) is refused, so a run ends at the integrity
stage by design after its criterion results are stored. The lane then
compares the stored grades across repeats.

Guards, all checked before the first call:
- a real model and the cache bypass are both required (a fake client or a
  cached answer would report perfect agreement by construction);
- the database must be local, and storage must be the local folder;
- the key must not be the production key kept in the env file for reference;
- one pinned model, taken from the product's own model pool so its rate and
  token limits are the deployed ones, and no failover to another model;
- every transport attempt, retries included, is reserved in the shared daily
  budget first; a 429 from the provider stops the whole run.

Usage, from the repository root, against a migrated scratch database:
  uv run --project backend python -m tools.accuracy.grading_repeats setup --golden <dir>
  uv run --project backend python -m tools.accuracy.grading_repeats repeat 1 --golden <dir>
Settings come from <golden>/grading/config.json, key "repeats".
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from tools.accuracy.budget import Budget, BudgetExceeded  # noqa: E402
from tools.accuracy.core import git_commit  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.db import sqlalchemy_url  # noqa: E402
from app.llm.base import LLMClient  # noqa: E402
from app.llm.client import GeminiLLMClient  # noqa: E402
from app.llm.pool import load_model_pool  # noqa: E402
from app.llm.queue import (  # noqa: E402
    LLMQueue,
    TransportDailyQuotaExhausted,
    TransportRateLimited,
)
from app.models.audit import AuditLog  # noqa: E402
from app.models.instructor import Instructor  # noqa: E402
from app.models.manuscript import Manuscript  # noqa: E402
from app.models.rubric import Criterion, Rubric  # noqa: E402
from app.models.run import CheckResult, CheckRun  # noqa: E402

LANE = "grading"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class HarnessStop(RuntimeError):
    """The run must end now: the provider said 429, or a guard failed."""


class HarnessRefused(RuntimeError):
    """A model call this harness does not measure; nothing was spent."""


class BudgetedTransport:
    """Wraps the real transport so every attempt is paid for in the shared
    budget before it is sent, and a rate limit ends the run instead of being
    retried or failed over."""

    def __init__(self, inner: Any, budget: Budget, model: str) -> None:
        self._inner = inner
        self._budget = budget
        self._model = model
        self.attempts = 0
        self.stop_reason: str | None = None

    async def generate(self, *, model: str, prompt: str, temperature: float, **context: Any):
        if self.stop_reason:
            raise HarnessStop(self.stop_reason)
        if model != self._model:
            self.stop_reason = f"a call asked for {model}, but this run is pinned to {self._model}"
            raise HarnessStop(self.stop_reason)
        try:
            self._budget.reserve(LANE)
        except BudgetExceeded as exc:
            self.stop_reason = f"daily budget reached: {exc}"
            raise HarnessStop(self.stop_reason) from exc
        self.attempts += 1
        try:
            return await self._inner.generate(
                model=model, prompt=prompt, temperature=temperature, **context
            )
        except (TransportRateLimited, TransportDailyQuotaExhausted) as exc:
            self.stop_reason = f"429 from the provider ({type(exc).__name__})"
            raise HarnessStop(self.stop_reason) from exc


class MeasuredCallsOnly(LLMClient):
    """Lets through only the prompt types this harness measures."""

    def __init__(self, inner: LLMClient, allowed: set[str]) -> None:
        self._inner = inner
        self._allowed = allowed
        self.refused: list[str] = []

    async def complete(
        self, prompt_type: str, prompt: str, *, prompt_version: str = "unversioned", **context: Any
    ) -> dict[str, Any]:
        if prompt_type not in self._allowed:
            self.refused.append(prompt_type)
            raise HarnessRefused(f"{prompt_type} is not measured by this harness")
        return await self._inner.complete(
            prompt_type, prompt, prompt_version=prompt_version, **context
        )


def _env_value(name: str) -> str | None:
    env = ROOT / ".env"
    if not env.exists():
        env = Path("C:/PROJECTS/Veridical/.env")
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return None


def check_guards(settings: Settings, config: dict) -> None:
    problems = []
    if settings.veridical_fake_llm:
        problems.append("VERIDICAL_FAKE_LLM is on: a fake client agrees with itself by design")
    if not settings.llm_cache_bypass:
        problems.append("LLM_CACHE_BYPASS is off: repeats would replay cached answers")
    if not settings.gemini_api_key:
        problems.append("GEMINI_API_KEY is not set")
    prod_key = _env_value(config.get("production_key_variable", "GEMINI_API_KEY_PROD"))
    if prod_key and settings.gemini_api_key == prod_key:
        problems.append("GEMINI_API_KEY is the production key; use the local test key")
    host = urlsplit(sqlalchemy_url(settings.database_url)).hostname
    if host not in LOCAL_HOSTS:
        problems.append(f"the database host {host!r} is not local")
    if settings.storage_backend != "local":
        problems.append("STORAGE_BACKEND must be local for a harness run")
    if settings.pipeline_worker_autostart:
        problems.append("PIPELINE_WORKER_AUTOSTART must be off")
    if problems:
        raise SystemExit("Refusing to spend quota:\n- " + "\n- ".join(problems))


def build_client(settings: Settings, session_factory, budget: Budget, config: dict):
    from app.llm.transport import GeminiTransport

    model = config["model"]
    pool = tuple(
        spec for spec in load_model_pool(settings.llm_model_pool_file) if spec.model == model
    )
    if len(pool) != 1:
        raise SystemExit(f"{model} is not exactly one entry of the model pool")
    transport = BudgetedTransport(
        GeminiTransport(
            settings.gemini_api_key, timeout_seconds=settings.gemini_request_timeout_seconds
        ),
        budget,
        model,
    )
    queue = LLMQueue(
        transport=transport,
        session_factory=session_factory,
        pool=pool,
        temperature=settings.gemini_temperature,
        max_retries=settings.llm_max_retries,
        retry_base_seconds=settings.llm_retry_base_seconds,
        reset_timezone=settings.llm_quota_reset_timezone,
        cache_bypass=True,
        cache_contract_version=settings.llm_cache_contract_version,
    )
    client = MeasuredCallsOnly(GeminiLLMClient(queue), set(config["allowed_prompt_types"]))
    return client, transport


def _metadata(
    settings: Settings, config: dict, transport: BudgetedTransport, budget: Budget
) -> dict:
    return {
        "at": datetime.now(UTC).isoformat(),
        "code_commit": git_commit(ROOT),
        "model": config["model"],
        "key_project": config.get("key_project_label", "local test project"),
        "temperature": settings.gemini_temperature,
        "cache_bypass": True,
        "transport_attempts": transport.attempts,
        "budget_today": budget.spent(),
        "stop_reason": transport.stop_reason,
    }


def _dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    yield path.read_bytes()


async def setup(session_factory, settings: Settings, client, transport, budget, config, out: Path):
    from app.ingest.service import ingest_manuscript
    from app.rubric.service import activate_rubric, create_rubric_from_upload

    state_file = out / "setup.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    async with session_factory() as session:
        if "instructor_id" not in state:
            instructor = Instructor(
                email=config["instructor_email"], display_name="Grading harness"
            )
            session.add(instructor)
            await session.commit()
            state["instructor_id"] = instructor.id
        if "rubric_id" not in state:
            pdf = Path(config["rubric_pdf"])
            rubric = await create_rubric_from_upload(
                session,
                _chunks(pdf),
                pdf.name,
                config["rubric_title"],
                client,
                settings,
                instructor_id=state["instructor_id"],
            )
            state["rubric_id"] = rubric.id
            state["rubric_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
            _dump(state_file, state)
        rubric = await session.get(Rubric, state["rubric_id"])
        if not rubric.is_active:
            await activate_rubric(session, rubric.id, state["instructor_id"])
        criteria = (
            await session.scalars(
                select(Criterion)
                .where(Criterion.rubric_id == rubric.id)
                .order_by(Criterion.position)
            )
        ).all()
        # The level order is the rubric's own, so within-one-level agreement
        # follows the format rather than any assumed scale.
        state["criteria"] = [
            {"id": c.id, "text": c.text, "type": c.type.value, "levels": c.levels} for c in criteria
        ]
        state.setdefault("manuscripts", {})
        for name, file in config["theses"].items():
            if name in state["manuscripts"]:
                continue
            path = Path(file)
            manuscript = Manuscript(
                instructor_id=state["instructor_id"],
                group_label=name,
                file_ref=str(path),
                original_filename=path.name,
            )
            session.add(manuscript)
            await session.commit()
            await ingest_manuscript(session, manuscript, path, settings, read_images=False)
            state["manuscripts"][name] = {
                "id": manuscript.id,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "ingest_status": manuscript.ingest_status.value,
            }
            _dump(state_file, state)
    state["setup"] = _metadata(settings, config, transport, budget)
    _dump(state_file, state)


async def _run_record(session, run_id: int) -> dict:
    run = await session.get(CheckRun, run_id)
    results = (
        await session.scalars(select(CheckResult).where(CheckResult.check_run_id == run_id))
    ).all()
    events = (
        await session.scalars(
            select(AuditLog).where(AuditLog.check_run_id == run_id).order_by(AuditLog.id)
        )
    ).all()
    return {
        "check_run": {"id": run.id, "status": run.status.value, "stage_status": run.stage_status},
        "results": [
            {
                "criterion_id": r.criterion_id,
                "kind": r.kind.value,
                "outcome": r.outcome.value,
                "detail": r.detail,
            }
            for r in results
        ],
        "llm_events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "prompt_version": e.prompt_version,
                "payload": e.payload,
            }
            for e in events
            if e.event_type.startswith("llm")
        ],
    }


async def repeat(
    number: int, session_factory, settings: Settings, client, transport, budget, config, out: Path
):
    from app.pipeline.machine import run_check_run
    from app.pipeline.service import create_check_run

    state = json.loads((out / "setup.json").read_text(encoding="utf-8"))
    folder = out / "repeats" / f"r{number}"
    for name, manuscript in state["manuscripts"].items():
        target = folder / f"{name}.json"
        if target.exists():
            continue
        async with session_factory() as session:
            run = await create_check_run(
                session, state["instructor_id"], manuscript["id"], state["rubric_id"], settings
            )
            try:
                await run_check_run(session, run, settings, llm=client)
            except (HarnessRefused, HarnessStop):
                await session.rollback()
        async with session_factory() as session:
            record = await _run_record(session, run.id)
        record["document"] = name
        record["repeat"] = number
        record["refused_prompt_types"] = sorted(set(client.refused))
        record["meta"] = _metadata(settings, config, transport, budget)
        client.refused.clear()
        if transport.stop_reason:
            # The run was cut short; keep it apart so it is never scored.
            _dump(folder / "stopped" / f"{name}.json", record)
            raise SystemExit(f"Stopped: {transport.stop_reason}")
        _dump(target, record)
        print(f"r{number} {name}: run {run.id}, {transport.attempts} attempts so far", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["setup", "repeat"])
    parser.add_argument("number", nargs="?", type=int)
    parser.add_argument("--golden", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.golden / LANE / "config.json").read_text(encoding="utf-8"))["repeats"]
    settings = get_settings()
    check_guards(settings, config)
    if args.command == "repeat" and not args.number:
        raise SystemExit("repeat needs a number, e.g. repeat 1")
    out = args.golden / LANE
    budget = Budget.from_golden(args.golden)

    async def go() -> None:
        engine = create_async_engine(sqlalchemy_url(settings.database_url))
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        client, transport = build_client(settings, session_factory, budget, config)
        try:
            if args.command == "setup":
                await setup(session_factory, settings, client, transport, budget, config, out)
            else:
                await repeat(
                    args.number, session_factory, settings, client, transport, budget, config, out
                )
        finally:
            await engine.dispose()
            print(f"transport attempts this process: {transport.attempts}; today: {budget.spent()}")

    asyncio.run(go())


if __name__ == "__main__":
    main()
