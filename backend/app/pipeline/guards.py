"""Shared short-transaction lock namespace for F3 claim coordination."""

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings


async def try_guard(session: AsyncSession, settings: Settings) -> bool:
    return bool(
        await session.scalar(
            select(
                func.pg_try_advisory_xact_lock(
                    cast(settings.pipeline_admission_lock_namespace, Integer),
                    cast(settings.pipeline_admission_lock_key, Integer),
                )
            )
        )
    )
