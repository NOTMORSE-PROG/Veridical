"""V-062 migration 0025's own live-DB tests: the backfill that creates one
`Group` per distinct (instructor_id, normalized group_label) already in
`manuscript`, and points `manuscript.group_id` at it. Requires a scratch
DB migrated only to 0024 (BEFORE 0025 runs) so the backfill actually has
pre-existing rows to act on, not an empty table -- upgrading straight to
'head' the way `test_schema.py`'s shared fixture does would make the
backfill run against nothing, proving nothing about it.
"""

import asyncio
import os

import asyncpg
import pytest

from alembic import command
from app.groups.service import normalize_group_name
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_backfill0025test"


@pytest.fixture()
def pre_0025_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "0024")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


async def _seed_pre_migration_manuscripts(dsn: str) -> dict[str, int]:
    """Inserts instructor + manuscript rows directly via asyncpg, exactly
    the shape they'd have under the OLD (pre-0025) schema -- no
    `group_id` column exists yet at this point in the migration chain."""
    conn = await asyncpg.connect(dsn)
    try:
        instructor_id = await conn.fetchval(
            "INSERT INTO instructor (email, display_name)"
            " VALUES ('backfill@demo.local', 'Backfill Test') RETURNING id"
        )
        ids = {}
        for key, label in [
            ("mixed_case_a", "Group 4"),
            ("mixed_case_b", "group   4"),  # same team, different casing/whitespace
            ("distinct", "Group 5"),
        ]:
            ids[key] = await conn.fetchval(
                "INSERT INTO manuscript (instructor_id, group_label, file_ref)"
                " VALUES ($1, $2, 'x.pdf') RETURNING id",
                instructor_id,
                label,
            )
        return {"instructor_id": instructor_id, **ids}
    finally:
        await conn.close()


async def _fetch_row(dsn: str, sql: str, *args):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await conn.close()


async def _fetch_all(dsn: str, sql: str, *args):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def test_backfill_merges_differently_cased_labels_into_one_group(pre_0025_url):
    ids = asyncio.run(_seed_pre_migration_manuscripts(pre_0025_url))
    command.upgrade(_alembic_config(pre_0025_url), "0025")

    groups = asyncio.run(
        _fetch_all(
            pre_0025_url,
            "SELECT id, name, program_id FROM manuscript_group WHERE instructor_id = $1",
            ids["instructor_id"],
        )
    )
    # Exactly two groups: "Group 4"/"group   4" merged into one, "Group 5" distinct.
    assert len(groups) == 2
    by_name = {g["name"]: g for g in groups}
    assert "Group 4" in by_name  # first-submitted casing wins
    assert "group   4" not in by_name
    assert "Group 5" in by_name
    # Backfill NEVER guesses a program (same convention as ingest_failure_reason).
    assert all(g["program_id"] is None for g in groups)

    group_id_sql = "SELECT group_id FROM manuscript WHERE id = $1"
    row_a = asyncio.run(_fetch_row(pre_0025_url, group_id_sql, ids["mixed_case_a"]))
    row_b = asyncio.run(_fetch_row(pre_0025_url, group_id_sql, ids["mixed_case_b"]))
    row_c = asyncio.run(_fetch_row(pre_0025_url, group_id_sql, ids["distinct"]))
    assert row_a["group_id"] == row_b["group_id"] == by_name["Group 4"]["id"]
    assert row_c["group_id"] == by_name["Group 5"]["id"]


@pytest.mark.parametrize(
    "raw",
    [
        "Group 4",
        "  Group   4  ",
        "GROUP 4",
        "\tTab Team\t",  # backend-critic's live repro: a boundary TAB
        "\tTab Team ",  # mixed tab/space boundary
        "Line1\nLine2",  # embedded newline, not just boundary whitespace
        "Ungrouped",
        "",
    ],
)
def test_python_normalization_matches_the_real_sql_exactly(pre_0025_url, raw):
    """`backend-critic` (V-062 review): don't trust the "byte-for-byte in
    sync" claim in either docstring -- ask the REAL Postgres engine what
    its own expression computes and compare against the REAL Python
    function, for the exact input class (boundary tabs) that broke this
    before the collapse-then-trim fix."""

    async def sql_normalize(value: str) -> str:
        conn = await asyncpg.connect(pre_0025_url)
        try:
            return await conn.fetchval(
                "SELECT lower(trim(regexp_replace($1::text, '\\s+', ' ', 'g')))", value
            )
        finally:
            await conn.close()

    assert asyncio.run(sql_normalize(raw)) == normalize_group_name(raw)


def test_program_table_is_seeded_with_cs_and_it(pre_0025_url):
    command.upgrade(_alembic_config(pre_0025_url), "0025")
    rows = asyncio.run(_fetch_all(pre_0025_url, "SELECT name FROM program ORDER BY name"))
    assert [r["name"] for r in rows] == ["CS", "IT"]


def test_downgrade_from_0025_drops_the_new_tables_and_column(pre_0025_url):
    cfg = _alembic_config(pre_0025_url)
    asyncio.run(_seed_pre_migration_manuscripts(pre_0025_url))
    command.upgrade(cfg, "0025")
    command.downgrade(cfg, "0024")

    tables = asyncio.run(
        _fetch_all(
            pre_0025_url,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            " AND tablename IN ('manuscript_group', 'program')",
        )
    )
    assert tables == []
    columns = asyncio.run(
        _fetch_all(
            pre_0025_url,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'manuscript' AND column_name = 'group_id'",
        )
    )
    assert columns == []

    # Re-upgrade must succeed too (CODING.md §2: up AND down tested).
    command.upgrade(cfg, "0025")
    groups_after = asyncio.run(_fetch_all(pre_0025_url, "SELECT id FROM manuscript_group"))
    assert len(groups_after) > 0
