"""Additive cache metadata preserves existing payloads and budget ledger rows."""

import asyncio
import json
import os
import uuid

import asyncpg
import pytest
from sqlalchemy import Text

from alembic import command
from app.models.llm import LLMResponseCache
from tests.test_schema import _alembic_config, _swap_db


def test_cache_version_column_has_no_inferred_legacy_default():
    column = LLMResponseCache.__table__.c.model_version
    assert column.nullable and column.server_default is None and isinstance(column.type, Text)


@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="integration: owned Postgres required")
def test_0042_preserves_existing_cache_and_budget_records():
    base = os.environ["DATABASE_URL"]
    name = "veridical_cacheversion_" + uuid.uuid4().hex
    url = _swap_db(base, name)

    async def admin(sql):
        connection = await asyncpg.connect(base)
        try:
            return await connection.execute(sql)
        finally:
            await connection.close()

    async def absent():
        connection = await asyncpg.connect(base)
        try:
            return (
                await connection.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name)
                is None
            )
        finally:
            await connection.close()

    assert asyncio.run(absent())
    asyncio.run(admin(f'CREATE DATABASE "{name}"'))
    try:
        cfg = _alembic_config(url)
        command.upgrade(cfg, "0041")

        async def seed_and_snapshot(seed=False, check_version=False):
            connection = await asyncpg.connect(url)
            try:
                if seed:
                    for index, payload in enumerate(
                        [
                            {"payload": "retained", "model_version": "untrusted-json"},
                            {"verdicts": [{"outcome": "example"}]},
                        ]
                    ):
                        await connection.execute(
                            "INSERT INTO llm_response_cache"
                            "(input_hash,prompt_version,model,response) "
                            "VALUES($1,$2,$3,$4::jsonb)",
                            f"{index:064x}",
                            "cache-version-control",
                            "old-requested-id",
                            json.dumps(payload),
                        )
                    for model, calls, hits in [
                        ("old-alias", 20, 3),
                        ("current", 4, 2),
                        ("retired", 8, 1),
                    ]:
                        await connection.execute(
                            "INSERT INTO llm_quota_counter"
                            "(quota_day,model,call_count,cache_hit_count) "
                            "VALUES('2000-01-01',$1,$2,$3)",
                            model,
                            calls,
                            hits,
                        )
                if check_version:
                    assert (
                        await connection.fetchval(
                            "SELECT count(*) FROM llm_response_cache "
                            "WHERE model_version IS NOT NULL"
                        )
                        == 0
                    )
                cache = await connection.fetch(
                    "SELECT id,input_hash,prompt_version,model,response,created_at "
                    "FROM llm_response_cache ORDER BY id"
                )
                counters = await connection.fetch("SELECT * FROM llm_quota_counter ORDER BY id")
                return [dict(row) for row in cache], [dict(row) for row in counters]
            finally:
                await connection.close()

        before = asyncio.run(seed_and_snapshot(seed=True))
        command.upgrade(cfg, "0042")
        assert asyncio.run(seed_and_snapshot(check_version=True)) == before
        command.downgrade(cfg, "0041")
        assert asyncio.run(seed_and_snapshot()) == before
        command.upgrade(cfg, "0042")
        assert asyncio.run(seed_and_snapshot(check_version=True)) == before
    finally:
        # Only the unique database created above, after every owned connection
        # has closed. Never drop or force-disconnect an existing database.
        asyncio.run(admin(f'DROP DATABASE "{name}"'))
    assert asyncio.run(absent())
