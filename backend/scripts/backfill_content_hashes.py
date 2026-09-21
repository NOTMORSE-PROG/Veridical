"""Recover SHA-256 identities for a bounded batch of legacy manuscripts.

Run from ``backend/``. Preview is the default; pass ``--apply`` only after
checking the fixed id ceiling and database/storage environment.

    uv run python -m scripts.backfill_content_hashes --through-id 120
    uv run python -m scripts.backfill_content_hashes --through-id 120 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import sqlalchemy_url
from app.maintenance.content_hashes import (
    ContentHashStorageInitializationError,
    backfill_content_hash_batch,
)


async def _dispose_engine(engine) -> None:
    await engine.dispose()


async def run(
    *,
    through_id: int,
    after_id: int,
    batch_size: int | None,
    apply: bool,
) -> int:
    engine = None
    result = None
    fatal_code: str | None = None
    post_result_failure = False
    try:
        settings = get_settings()
        engine = create_async_engine(sqlalchemy_url(settings.database_url), hide_parameters=True)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            result = await backfill_content_hash_batch(
                session,
                settings,
                through_id=through_id,
                after_id=after_id,
                batch_size=batch_size,
                apply=apply,
            )
    except ContentHashStorageInitializationError:
        fatal_code = "configuration_or_storage_initialization_failure"
    except Exception:
        if result is None:
            fatal_code = "database_or_command_failure"
        else:
            post_result_failure = True
    finally:
        if engine is not None:
            try:
                await _dispose_engine(engine)
            except Exception:
                if result is None:
                    fatal_code = fatal_code or "cleanup_failure"
                else:
                    post_result_failure = True

    if result is None:
        print(
            json.dumps(
                {
                    "status": "fatal",
                    "code": fatal_code or "database_or_command_failure",
                    "message": "Backfill could not produce a trustworthy batch result.",
                }
            )
        )
        return 2

    payload = asdict(result)
    payload["items"] = [
        {"manuscript_id": item.manuscript_id, "outcome": item.outcome.value}
        for item in result.items
    ]
    payload["counts"] = result.counts
    payload["post_result_failure"] = post_result_failure
    payload["status"] = "partial" if result.has_unrecovered or post_result_failure else "ok"
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if result.has_unrecovered or post_result_failure else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through-id", type=int, required=True)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist recovered hashes; without this flag the command is a preview",
    )
    args = parser.parse_args()
    exit_code = asyncio.run(
        run(
            through_id=args.through_id,
            after_id=args.after_id,
            batch_size=args.batch_size,
            apply=args.apply,
        )
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    main()
