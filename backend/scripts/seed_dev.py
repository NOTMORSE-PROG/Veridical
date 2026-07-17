"""Seed the demo instructor account for local development.

Idempotent: safe to run repeatedly. Usage (from backend/):

    uv run python -m scripts.seed_dev

The account values are dev fixtures (no password until auth lands, V-014);
production accounts are created through the app, never by this script.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import sqlalchemy_url
from app.models import Instructor

DEMO_EMAIL = "instructor@demo.local"
DEMO_NAME = "Demo Instructor"


async def seed() -> str:
    engine = create_async_engine(sqlalchemy_url(get_settings().database_url))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        existing = await session.scalar(select(Instructor).where(Instructor.email == DEMO_EMAIL))
        if existing is not None:
            outcome = f"demo instructor already present (id={existing.id})"
        else:
            instructor = Instructor(email=DEMO_EMAIL, display_name=DEMO_NAME)
            session.add(instructor)
            await session.commit()
            outcome = f"demo instructor created (id={instructor.id})"
    await engine.dispose()
    return outcome


if __name__ == "__main__":
    print(asyncio.run(seed()))
