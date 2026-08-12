import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.config import get_settings
from app.db import sqlalchemy_url
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: the default (True) silently disables
    # every already-registered logger NOT named in alembic.ini's [loggers]
    # section — including every `app.*` logger, since app/main.py imports
    # `app.pipeline.worker` (creating its module-level logger) before ever
    # calling this. In production (D-021: migrations auto-run at boot),
    # that means BUG-032's new `logger.exception()` calls would be
    # permanently silenced immediately after the very first migration run,
    # with no error and no way to tell short of reading this file.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    # Tests inject a scratch-database URL via the config option; normal runs
    # use DATABASE_URL from settings (alembic.ini keeps it empty on purpose).
    return config.get_main_option("sqlalchemy.url") or sqlalchemy_url(get_settings().database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection (`alembic upgrade --sql`)."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    # NullPool: migration runs are one-shot; don't hold connections open
    # against Neon's autosuspending compute.
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
