"""Seed the demo instructor account for local development.

Idempotent: safe to run repeatedly. Usage (from backend/):

    uv run python -m scripts.seed_dev

The account values are dev fixtures; production accounts are created
through the app, never by this script. DEMO_PASSWORD is a LOCAL DEV
fixture only — never used for a real production account.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.security import hash_password
from app.config import get_settings
from app.db import sqlalchemy_url
from app.models import Instructor

DEMO_EMAIL = "instructor@demo.local"
DEMO_NAME = "Demo Instructor"
DEMO_PASSWORD = "veridical-dev"


async def seed() -> str:
    engine = create_async_engine(sqlalchemy_url(get_settings().database_url))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        existing = await session.scalar(select(Instructor).where(Instructor.email == DEMO_EMAIL))
        if existing is not None:
            outcome = f"demo instructor already present (id={existing.id})"
            if existing.password_hash is None:
                # Backfill: an instructor row seeded before V-014 had no
                # password at all — self-heal it instead of leaving a
                # dev account nobody can sign into.
                existing.password_hash = hash_password(DEMO_PASSWORD)
                await session.commit()
                outcome += " (password backfilled)"
        else:
            instructor = Instructor(
                email=DEMO_EMAIL, display_name=DEMO_NAME, password_hash=hash_password(DEMO_PASSWORD)
            )
            session.add(instructor)
            await session.commit()
            outcome = f"demo instructor created (id={instructor.id})"
    await engine.dispose()
    return outcome


if __name__ == "__main__":
    print(asyncio.run(seed()))
