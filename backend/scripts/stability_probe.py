"""BUG-162: a real, supported way to MEASURE run-to-run grading stability
against one already-ingested manuscript.

The response cache (D-011, `app/llm/client.py`'s own comment) is keyed on
prompt content, deliberately excluding `check_run_id` -- so an ordinary
re-run of the same manuscript against the same rubric version is a cache
REPLAY, not an independent regrading, and agreement between the two runs
is 1.0 by construction. That's correct for saving quota, but it means
there was no supported way to actually measure the thing V3's exit
checkpoint claims: does the model itself grade consistently? This script
is that measurement, using `Settings.llm_cache_bypass` (`app/llm/queue.py`)
to force every LLM call to skip its cached answer.

**Spends REAL Gemini quota** -- once per criterion per run, the same as
any real check. `--runs 5` against a rubric with ~4 semantic criteria
costs roughly what 5 ordinary first-time checks would (see D-011's own
addendum for the pool's daily capacity math before running a large N).

Never run this with `VERIDICAL_FAKE_LLM=1`: the fake client returns
byte-identical fixture responses by construction, which would report
"perfect stability" trivially and dishonestly -- the whole point is
measuring the REAL model's variance. Refuses to run if either
`VERIDICAL_FAKE_LLM` or `LLM_CACHE_BYPASS` isn't set correctly, rather
than silently producing a meaningless or tautological result.

Usage (from backend/, DB migrated, against a manuscript/rubric that has
already been through a real, successful first check so ingestion is
already done):

    LLM_CACHE_BYPASS=1 uv run python -m scripts.stability_probe \\
        --manuscript-id 12 --rubric-id 3 --instructor-id 1 --runs 5

Reports, per criterion, the DISTRIBUTION of verdicts across the N runs --
never a binary "agrees". "4/5 passed, 1/5 escalated" is a real, checkable
finding; "5/5 passed" is genuine agreement, not assumed.
"""

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db import sqlalchemy_url
from app.models.enums import CheckRunStatus
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun
from app.pipeline.service import create_check_run
from app.pipeline.worker import advance_once


async def _run_one(
    session_factory, *, instructor_id: int, manuscript_id: int, rubric_id: int, settings: Settings
) -> tuple[dict[int, str], CheckRunStatus]:
    async with session_factory() as session:
        check_run = await create_check_run(
            session, instructor_id, manuscript_id, rubric_id, settings
        )
        check_run_id = check_run.id

    # backend-critic finding (BUG-162 review, P1): calling `run_check_run`
    # directly (no claim, no heartbeat) raced `worker_loop` in exactly the
    # shape BUG-144 exists to prevent -- `worker_loop` starts unconditionally
    # in production (`app/main.py`) and is a normal, documented local-dev
    # opt-in too (`PIPELINE_WORKER_AUTOSTART`). `advance_once` is the SAME
    # claim+heartbeat+release path `worker_loop`/the create-route's "kick it
    # now" call already use -- if a live worker gets there first, this
    # becomes a silent, correct no-op instead of a second execution that
    # would duplicate real, quota-spending work and corrupt this exact
    # measurement (BUG-144's own production incident: every finding
    # appeared exactly twice).
    await advance_once(check_run_id, session_factory, settings)

    async with session_factory() as session:
        check_run = await session.get(CheckRun, check_run_id)
        status = check_run.status

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(CheckResult.criterion_id, CheckResult.outcome).where(
                    CheckResult.check_run_id == check_run_id,
                    CheckResult.criterion_id.is_not(None),
                )
            )
        ).all()
    return {row.criterion_id: row.outcome.value for row in rows}, status


async def run(*, manuscript_id: int, rubric_id: int, instructor_id: int, runs: int) -> None:
    settings = get_settings()
    if settings.veridical_fake_llm:
        raise SystemExit(
            "VERIDICAL_FAKE_LLM=1 is set -- a fake client returns byte-identical "
            "fixture responses by construction, which would report perfect "
            "stability trivially and dishonestly. Unset it to measure the real model."
        )
    if not settings.llm_cache_bypass:
        raise SystemExit(
            "LLM_CACHE_BYPASS is not set -- every run after the first would replay "
            "a cached answer instead of independently regrading, exactly the "
            "tautology this script exists to avoid. Set LLM_CACHE_BYPASS=1."
        )
    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is not set -- this script only measures the real model.")

    engine = create_async_engine(sqlalchemy_url(settings.database_url))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # backend-critic finding (BUG-162 review, P2): `run_check_run`'s own
    # broad `except Exception`/`ClaimLost`/`PipelineBlockedError` handling
    # can leave a check_run non-`done` (or, via `advance_once`, a no-op if
    # a live worker claimed it first) WITHOUT raising -- silently folding
    # "this run never finished" into the exact same "MISSING" bucket as
    # "this criterion doesn't apply," which would misreport a stability
    # measurement's own reliability. Every run's terminal status is
    # printed as it happens and only `done` runs count toward the
    # per-criterion distribution below.
    per_run: list[dict[int, str]] = []
    for i in range(1, runs + 1):
        print(f"--- run {i}/{runs} ---", flush=True)
        outcomes, status = await _run_one(
            session_factory,
            instructor_id=instructor_id,
            manuscript_id=manuscript_id,
            rubric_id=rubric_id,
            settings=settings,
        )
        print(f"    terminal status: {status.value}", flush=True)
        if status == CheckRunStatus.done:
            per_run.append(outcomes)
        else:
            print(
                "    NOT counted toward the distribution below (did not reach 'done' -- "
                "either it failed/was blocked, or a live worker already owned it).",
                flush=True,
            )

    if not per_run:
        raise SystemExit(
            f"None of the {runs} run(s) reached 'done' -- no distribution to report. "
            "See each run's terminal status above."
        )
    if len(per_run) < runs:
        print(
            f"\nWARNING: only {len(per_run)}/{runs} runs reached 'done'. The "
            f"distribution below reflects those {len(per_run)} run(s) only.",
            flush=True,
        )

    criterion_ids = sorted({cid for result in per_run for cid in result})
    async with session_factory() as session:
        criteria = {
            c.id: c.text
            for c in (
                await session.scalars(select(Criterion).where(Criterion.id.in_(criterion_ids)))
            ).all()
        }

    completed = len(per_run)
    print(f"\n===== stability across {completed} independent, completed runs =====")
    for cid in criterion_ids:
        outcomes = [result.get(cid, "MISSING") for result in per_run]
        counts: dict[str, int] = {}
        for outcome in outcomes:
            counts[outcome] = counts.get(outcome, 0) + 1
        distribution = ", ".join(
            f"{count}/{completed} {outcome}"
            for outcome, count in sorted(counts.items(), key=lambda kv: -kv[1])
        )
        label = criteria.get(cid, f"criterion #{cid}")
        print(f"  [{cid}] {label[:70]}\n      {distribution}")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manuscript-id", type=int, required=True)
    parser.add_argument("--rubric-id", type=int, required=True)
    parser.add_argument(
        "--instructor-id", type=int, required=True, help="owner of both the manuscript and rubric"
    )
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(
        run(
            manuscript_id=args.manuscript_id,
            rubric_id=args.rubric_id,
            instructor_id=args.instructor_id,
            runs=args.runs,
        )
    )


if __name__ == "__main__":
    main()
