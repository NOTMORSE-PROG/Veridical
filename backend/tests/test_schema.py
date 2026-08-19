"""V-003 schema tests.

Unit tests run anywhere; the integration tests need a live Postgres
(DATABASE_URL, same convention as test_health.py) and run the real
migrations against a scratch database created for the module — the dev
database is never touched.
"""

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config

from alembic import command
from app.config import get_settings
from app.db import sqlalchemy_url
from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRATCH_DB = "veridical_migtest"

EXPECTED_TABLES = {
    "instructor",
    "rubric",
    "criterion",
    "manuscript",
    "check_run",
    "check_result",
    "flag",
    "readiness_report",
    "manuscript_archive",
    "audit_log",
    "citation",  # V-006 (migration 0002)
    "llm_quota_counter",  # V-009 (migration 0003)
    "llm_response_cache",  # V-009 (migration 0003)
    "session",  # V-014 (migration 0005)
    "citation_cache",  # V-028 (migration 0012)
    "manuscript_chapter_archive",  # V-036 (migration 0014)
    "share_link",  # V-040 (migration 0018)
    "program",  # V-062 (migration 0025)
    "manuscript_group",  # V-062 (migration 0025)
    "group_member",  # V-063 (migration 0027)
}


def test_sqlalchemy_url_names_the_async_driver():
    assert sqlalchemy_url("postgresql://u:p@h:5433/db") == "postgresql+asyncpg://u:p@h:5433/db"
    # Legacy scheme still issued by some hosts (Neon among them).
    assert sqlalchemy_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    # Already-qualified URLs pass through untouched.
    assert sqlalchemy_url("postgresql+asyncpg://u@h/db") == "postgresql+asyncpg://u@h/db"


def test_sqlalchemy_url_translates_neon_libpq_query_params():
    """V-048: Neon's pooled DSN adds sslmode=require&channel_binding=require.

    SQLAlchemy's asyncpg dialect forwards query params verbatim as connect()
    kwargs (no libpq translation) — found live: `TypeError: connect() got an
    unexpected keyword argument 'sslmode'`. channel_binding has no asyncpg
    equivalent (dropped); sslmode becomes asyncpg's own `ssl` param."""
    url = sqlalchemy_url("postgresql://u:p@h/db?sslmode=require&channel_binding=require")
    assert url == "postgresql+asyncpg://u:p@h/db?ssl=require"


def test_metadata_declares_exactly_the_expected_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


# --- integration: real migrations on a scratch database ---------------------

pytestmark_live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)


def _swap_db(dsn: str, dbname: str) -> str:
    # Our DSNs never carry query params (compose/CI); last path segment is the db.
    return dsn.rsplit("/", 1)[0] + "/" + dbname


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(db_url))
    return cfg


async def _admin_execute(dsn: str, sql: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def scratch_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    yield _swap_db(base, SCRATCH_DB)
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture(scope="module")
def migrated(scratch_url):
    """Acceptance criterion: `alembic upgrade head` from an empty DB succeeds."""
    command.upgrade(_alembic_config(scratch_url), "head")
    return scratch_url


async def _fetch(dsn: str, sql: str, *args):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _tables(dsn: str) -> set[str]:
    rows = await _fetch(
        dsn,
        "SELECT tablename FROM pg_tables"
        " WHERE schemaname = 'public' AND tablename <> 'alembic_version'",
    )
    return {r["tablename"] for r in rows}


@pytestmark_live
def test_upgrade_creates_all_v1_tables(migrated):
    assert asyncio.run(_tables(migrated)) == EXPECTED_TABLES


@pytestmark_live
def test_migration_does_not_disable_pre_existing_app_loggers(migrated):
    """BUG-032 regression: `alembic/env.py`'s `fileConfig()` call defaults to
    `disable_existing_loggers=True`, which silently disables every logger
    already registered (via `logging.getLogger(__name__)` at import time)
    that isn't named in alembic.ini's own `[loggers]` section — in
    production this runs on every boot (D-021: migrations auto-run at
    startup), AFTER `app.main` has already imported `app.pipeline.worker`
    and created its module-level logger. An undetected regression here
    would silently swallow every `logger.exception()` call the pipeline
    worker makes, with no error anywhere. `migrated` already ran a real
    `alembic upgrade head` (the only way this code path actually executes),
    so this simulates the exact import-then-migrate order main.py uses."""
    import logging

    # No manual setup needed: pytest already imported `app.pipeline.worker`
    # (and thus created this logger) during test collection, well before
    # the `migrated` fixture's `command.upgrade()` call above — the same
    # import-then-migrate order `app/main.py` uses in production.
    assert logging.getLogger("app.pipeline.worker").disabled is False


@pytestmark_live
def test_up_down_up_cycle(migrated):
    """Downgrade must return the DB to empty, and re-upgrade must succeed
    (CODING.md §2: up AND down tested). Self-restoring: ends at head."""
    cfg = _alembic_config(migrated)
    command.downgrade(cfg, "base")
    assert asyncio.run(_tables(migrated)) == set()
    command.upgrade(cfg, "head")
    assert asyncio.run(_tables(migrated)) == EXPECTED_TABLES


@pytestmark_live
def test_audit_log_rejects_update_and_delete_at_db_level(migrated):
    async def scenario():
        conn = await asyncpg.connect(migrated)
        try:
            row_id = await conn.fetchval(
                "INSERT INTO audit_log (event_type, payload)"
                " VALUES ('test_event', '{}'::jsonb) RETURNING id"
            )
            with pytest.raises(asyncpg.PostgresError, match="append-only"):
                await conn.execute(
                    "UPDATE audit_log SET event_type = 'tampered' WHERE id = $1", row_id
                )
            with pytest.raises(asyncpg.PostgresError, match="append-only"):
                await conn.execute("DELETE FROM audit_log WHERE id = $1", row_id)
            # The row itself must be intact after both rejected attempts.
            kept = await conn.fetchval("SELECT event_type FROM audit_log WHERE id = $1", row_id)
            assert kept == "test_event"
        finally:
            await conn.close()

    asyncio.run(scenario())


@pytestmark_live
def test_pgvector_column_index_and_similarity_query(migrated):
    dim = get_settings().embedding_dim

    async def scenario():
        conn = await asyncpg.connect(migrated)
        try:
            indexdef = await conn.fetchval(
                "SELECT indexdef FROM pg_indexes"
                " WHERE indexname = 'ix_manuscript_archive_embedding'"
            )
            assert indexdef is not None and "hnsw" in indexdef

            instructor_id = await conn.fetchval(
                "INSERT INTO instructor (email, display_name)"
                " VALUES ('vector-test@test.local', 'Vector Test') RETURNING id"
            )
            manuscript_id = await conn.fetchval(
                "INSERT INTO manuscript (instructor_id, group_label, file_ref)"
                " VALUES ($1, 'Group 1', 'test.pdf') RETURNING id",
                instructor_id,
            )
            near = "[" + ",".join(["0.1"] * dim) + "]"
            probe = "[" + ",".join(["0.11"] * dim) + "]"
            await conn.execute(
                "INSERT INTO manuscript_archive (manuscript_id, embedding, model_id)"
                " VALUES ($1, $2::vector, 'test-model')",
                manuscript_id,
                near,
            )
            match = await conn.fetchrow(
                "SELECT manuscript_id, embedding <=> $1::vector AS distance"
                " FROM manuscript_archive ORDER BY distance LIMIT 1",
                probe,
            )
            assert match["manuscript_id"] == manuscript_id
            assert match["distance"] < 0.01  # parallel vectors: cosine distance ≈ 0
        finally:
            await conn.close()

    asyncio.run(scenario())


@pytestmark_live
def test_migrated_embedding_columns_are_exactly_256_dim_regardless_of_env(migrated):
    """V-043 (production deployment) finding, live-reproduced: migration
    0014 used to read `get_settings().embedding_dim` at migration-run
    time instead of hardcoding the value it was meant to lock in
    permanently. Every environment that never carried a stray
    `EMBEDDING_DIM` env var override (this test's own `migrated` fixture
    included) got the right answer by COINCIDENCE, not because the
    migration was actually deterministic -- production had a leftover
    `EMBEDDING_DIM=768` env var (provisional value from before this
    migration existed) and silently ended up with a `vector(768)` column
    while every other environment showed `vector(256)`, exactly the kind
    of drift `test_pgvector_column_index_and_similarity_query` (which
    derives its own dimension FROM `get_settings().embedding_dim`) cannot
    catch, since it's tautological with respect to the same value that
    was wrong. This test asserts the LITERAL migrated dimension instead,
    independent of whatever `EMBEDDING_DIM` this test process happens to
    have set -- the real, permanent, code-decided value (potion-base-8M,
    D-011), not a re-read of the same settings a bug could also affect.
    """

    async def scenario():
        conn = await asyncpg.connect(migrated)
        try:
            for table in ("manuscript_archive", "manuscript_chapter_archive"):
                typ = await conn.fetchval(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
                    " WHERE attrelid = $1::regclass AND attname = 'embedding'",
                    table,
                )
                assert typ == "vector(256)", f"{table}.embedding is {typ!r}, expected vector(256)"
        finally:
            await conn.close()

    asyncio.run(scenario())


@pytestmark_live
def test_rubric_family_version_must_be_unique(migrated):
    async def scenario():
        conn = await asyncpg.connect(migrated)
        try:
            instructor_id = await conn.fetchval(
                "INSERT INTO instructor (email, display_name)"
                " VALUES ('rubric-test@test.local', 'Rubric Test') RETURNING id"
            )
            family = uuid.uuid4()
            await conn.execute(
                "INSERT INTO rubric (instructor_id, rubric_family_id, version, title)"
                " VALUES ($1, $2, 1, 'TIP format v1')",
                instructor_id,
                family,
            )
            # Same family, new version: allowed (this IS rubric versioning, F2.4).
            await conn.execute(
                "INSERT INTO rubric (instructor_id, rubric_family_id, version, title)"
                " VALUES ($1, $2, 2, 'TIP format v2')",
                instructor_id,
                family,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO rubric (instructor_id, rubric_family_id, version, title)"
                    " VALUES ($1, $2, 2, 'duplicate version')",
                    instructor_id,
                    family,
                )
        finally:
            await conn.close()

    asyncio.run(scenario())


@pytestmark_live
def test_v7_reserved_columns_exist_and_are_nullable(migrated):
    """D-005: schema ready for V7, feature absent — both columns nullable."""
    rows = asyncio.run(
        _fetch(
            migrated,
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'manuscript'"
            " AND column_name IN ('version', 'submitted_by')",
        )
    )
    assert {r["column_name"]: r["is_nullable"] for r in rows} == {
        "version": "YES",
        "submitted_by": "YES",
    }


@pytestmark_live
def test_seed_script_is_idempotent(migrated, monkeypatch):
    from scripts.seed_dev import seed

    monkeypatch.setenv("DATABASE_URL", migrated)
    get_settings.cache_clear()
    first = asyncio.run(seed())
    second = asyncio.run(seed())
    assert "created" in first
    assert "already present" in second


@pytestmark_live
def test_seed_programs_is_idempotent_and_picks_up_a_new_name(migrated, monkeypatch):
    """V-062: migration 0025 already seeded CS/IT directly, so re-running
    this against a freshly-migrated DB must add nothing (idempotent) --
    but adding a name to `default_programs` that ISN'T in the DB yet must
    be picked up, since that's the whole point of keeping this
    config-driven rather than another migration per new program."""
    from app.groups.service import seed_default_programs

    monkeypatch.setenv("DATABASE_URL", migrated)
    get_settings.cache_clear()
    first = asyncio.run(seed_default_programs())
    assert first == []  # CS/IT already present from the migration itself

    monkeypatch.setenv("DEFAULT_PROGRAMS", "CS,IT,ECE")
    get_settings.cache_clear()
    second = asyncio.run(seed_default_programs())
    assert second == ["ECE"]
    third = asyncio.run(seed_default_programs())
    assert third == []  # idempotent: ECE is already there now
    get_settings.cache_clear()


def test_seed_script_refuses_to_run_outside_dev(monkeypatch):
    """BUG-006/D-020: writes a publicly-known password straight from its
    own source — must never touch a non-dev database. No DATABASE_URL
    needed: the guard fires before any engine is created."""
    from scripts.seed_dev import RefusedInProductionError, seed

    monkeypatch.setenv("VERIDICAL_ENV", "prod")
    get_settings.cache_clear()
    with pytest.raises(RefusedInProductionError):
        asyncio.run(seed())
