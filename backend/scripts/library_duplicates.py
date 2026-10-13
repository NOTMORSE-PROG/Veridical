"""Preview, then optionally apply, the V-077 Library duplicate cleanup.

Run from ``backend/``. Preview is the default and changes nothing:

    uv run python -m scripts.library_duplicates
    uv run python -m scripts.library_duplicates --apply
    uv run python -m scripts.library_duplicates --apply --remove-ids 41,57

``--apply`` removes exact copies (keeping one per file) and corpus rows that
belong to no active Library entry. Same-text pairs are only listed; pass the
ids you decide to remove with ``--remove-ids``. No stored file is deleted.
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
from app.maintenance.library_duplicates import apply_cleanup, preview_library_duplicates


def _ids(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(",") if part.strip())


async def run(*, apply: bool, remove_ids: tuple[int, ...]) -> int:
    settings = get_settings()
    engine = create_async_engine(sqlalchemy_url(settings.database_url), hide_parameters=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if apply:
                report = await apply_cleanup(session, settings, remove_named=remove_ids)
            else:
                report = await preview_library_duplicates(session, settings)
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "mode": "apply" if apply else "preview",
                "exact_copy_removals": report.exact_copy_removals,
                **asdict(report),
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="execute instead of previewing")
    parser.add_argument(
        "--remove-ids",
        type=_ids,
        default=(),
        help="comma-separated manuscript ids of same-text duplicates to remove (needs --apply)",
    )
    args = parser.parse_args(argv)
    if args.remove_ids and not args.apply:
        parser.error("--remove-ids only takes effect with --apply")
    return asyncio.run(run(apply=args.apply, remove_ids=args.remove_ids))


if __name__ == "__main__":
    sys.exit(main())
