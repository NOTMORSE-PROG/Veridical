"""Record source-check issues on an already-stored format version (BUG-566).

Run from ``backend/``. A preview is the default and changes nothing; pass
``--apply`` only after reading the preview. Criteria and levels are never
changed and no model call is made. A version that already has structured
issues is left alone.

    uv run python -m scripts.annotate_rubric_issues --rubric-id 6
    uv run python -m scripts.annotate_rubric_issues --rubric-id 6 --apply
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
from app.rubric.annotate import annotate_rubric


async def run(rubric_id: int, *, apply: bool) -> int:
    settings = get_settings()
    engine = create_async_engine(sqlalchemy_url(settings.database_url), hide_parameters=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            result = await annotate_rubric(session, settings, rubric_id, apply=apply)
    finally:
        await engine.dispose()
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric-id", type=int, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the issues; without this flag the command only previews them",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.rubric_id, apply=args.apply)))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    main()
