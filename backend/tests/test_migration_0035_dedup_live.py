"""BUG-144 migration 0035's own live-DB tests: the dedup step that runs
BEFORE the new `check_result` unique constraints, against a scratch DB
migrated only to 0034 (before 0035) so it has real duplicate rows to act
on -- the exact gap `backend-critic` flagged (BUG-144 review): "no test
exercises the dedup SQL against actual duplicate rows." Mirrors
`test_migration_0025_backfill.py`'s own convention for testing migration
data-transformation logic in isolation from `test_schema.py`'s "upgrade
straight to head" shared fixture.
"""

import asyncio
import os

import asyncpg
import pytest

from alembic import command
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_migration0035test"


@pytest.fixture()
def pre_0035_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "0034")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


async def _fetch_all(dsn: str, sql: str, *args):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _seed_base(dsn: str) -> dict[str, int]:
    conn = await asyncpg.connect(dsn)
    try:
        instructor_id = await conn.fetchval(
            "INSERT INTO instructor (email, display_name)"
            " VALUES ('dedup-test@demo.local', 'Dedup Test') RETURNING id"
        )
        manuscript_id = await conn.fetchval(
            "INSERT INTO manuscript (instructor_id, group_label, file_ref)"
            " VALUES ($1, 'G', 'x.pdf') RETURNING id",
            instructor_id,
        )
        rubric_id = await conn.fetchval(
            "INSERT INTO rubric (instructor_id, title, source_file)"
            " VALUES ($1, 'Format', 'r.pdf') RETURNING id",
            instructor_id,
        )
        criterion_id = await conn.fetchval(
            "INSERT INTO criterion (rubric_id, type, text, evidence, weight, position)"
            " VALUES ($1, 'semantic', 'A criterion.', 'Some evidence', 1, 0) RETURNING id",
            rubric_id,
        )
        check_run_id = await conn.fetchval(
            "INSERT INTO check_run (manuscript_id, rubric_id) VALUES ($1, $2) RETURNING id",
            manuscript_id,
            rubric_id,
        )
        return {"check_run_id": check_run_id, "criterion_id": criterion_id}
    finally:
        await conn.close()


async def _insert_check_result(
    dsn: str, *, check_run_id: int, criterion_id: int | None, kind: str, detail: str | None
) -> int:
    """`detail=None` inserts a real SQL NULL in the column -- distinct from
    a JSONB `null` scalar -- the exact shape `backend-critic`'s NULL-sort
    finding (BUG-144 follow-up review) is about."""
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "INSERT INTO check_result (check_run_id, criterion_id, kind, outcome, detail)"
            " VALUES ($1, $2, $3, 'escalated', $4::jsonb) RETURNING id",
            check_run_id,
            criterion_id,
            kind,
            detail,
        )
    finally:
        await conn.close()


async def _insert_flag(dsn: str, *, check_result_id: int, overridden: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "INSERT INTO flag"
            " (check_result_id, severity, evidence_excerpt, page_anchor, overridden)"
            " VALUES ($1, 'high', 'Quoted text.', 'p. 1', $2) RETURNING id",
            check_result_id,
            overridden,
        )
    finally:
        await conn.close()


def test_dedup_keeps_the_resolved_twin_for_a_criterion_level_duplicate(pre_0035_url):
    """The common case: BUG-144's doubled execution produced two
    byte-identical `check_result` rows for one criterion, and only ONE was
    ever resolved by an instructor before the fix shipped. The resolved
    twin must survive; the untouched one must not."""
    ids = asyncio.run(_seed_base(pre_0035_url))
    unresolved_id = asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=ids["criterion_id"],
            kind="semantic",
            detail="{}",
        )
    )
    resolved_id = asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=ids["criterion_id"],
            kind="semantic",
            detail='{"resolution": {"type": "mark_pass", "reason": "Verified in Chapter 2."}}',
        )
    )

    command.upgrade(_alembic_config(pre_0035_url), "head")

    rows = asyncio.run(
        _fetch_all(
            pre_0035_url,
            "SELECT id FROM check_result WHERE check_run_id = $1 AND criterion_id = $2",
            ids["check_run_id"],
            ids["criterion_id"],
        )
    )
    assert [r["id"] for r in rows] == [resolved_id]
    assert unresolved_id not in [r["id"] for r in rows]


def test_dedup_keeps_the_resolved_twin_even_when_the_other_has_null_detail(pre_0035_url):
    """`backend-critic` finding (BUG-144 follow-up review, empirically
    reproduced against a live scratch DB before this fix): `detail ?
    'resolution'` is SQL NULL, not `false`, when `detail` itself is a real
    SQL NULL (not a JSONB `null` -- the column is nullable). Postgres sorts
    NULL FIRST under `ORDER BY ... DESC`, so an unengaged NULL-detail twin
    would rank ABOVE a genuinely resolved one and the ENGAGED twin would be
    the one silently deleted -- exactly the failure this migration exists
    to prevent, resurrected through the one path the original tie-break
    didn't guard. The dedup SQL now COALESCEs NULL to false explicitly."""
    ids = asyncio.run(_seed_base(pre_0035_url))
    null_detail_id = asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=ids["criterion_id"],
            kind="semantic",
            detail=None,
        )
    )
    resolved_id = asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=ids["criterion_id"],
            kind="semantic",
            detail='{"resolution": {"type": "mark_pass", "reason": "Verified in Chapter 2."}}',
        )
    )

    command.upgrade(_alembic_config(pre_0035_url), "head")

    rows = asyncio.run(
        _fetch_all(
            pre_0035_url,
            "SELECT id FROM check_result WHERE check_run_id = $1 AND criterion_id = $2",
            ids["check_run_id"],
            ids["criterion_id"],
        )
    )
    assert [r["id"] for r in rows] == [resolved_id]
    assert null_detail_id not in [r["id"] for r in rows]


def test_dedup_keeps_lowest_id_when_neither_twin_is_engaged(pre_0035_url):
    """Neither twin was ever touched by an instructor -- both are equally
    "safe" to delete, so the tie-break is deterministic (lowest id, the
    earliest-created row) rather than arbitrary."""
    ids = asyncio.run(_seed_base(pre_0035_url))
    first_id = asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=ids["criterion_id"],
            kind="semantic",
            detail="{}",
        )
    )
    asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=ids["criterion_id"],
            kind="semantic",
            detail="{}",
        )
    )

    command.upgrade(_alembic_config(pre_0035_url), "head")

    rows = asyncio.run(
        _fetch_all(
            pre_0035_url,
            "SELECT id FROM check_result WHERE check_run_id = $1 AND criterion_id = $2",
            ids["check_run_id"],
            ids["criterion_id"],
        )
    )
    assert [r["id"] for r in rows] == [first_id]


def test_dedup_leaves_a_kind_level_duplicate_alone_when_one_twin_has_an_overridden_flag(
    pre_0035_url,
):
    """Same engaged-twin-survives rule at the F4-F7 integrity-check
    granularity (criterion_id IS NULL, keyed on kind instead), using an
    overridden Flag as the engagement signal since these results have no
    `resolution` field of their own."""
    ids = asyncio.run(_seed_base(pre_0035_url))
    untouched_id = asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=None,
            kind="citation_integrity",
            detail="{}",
        )
    )
    engaged_id = asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=None,
            kind="citation_integrity",
            detail="{}",
        )
    )
    asyncio.run(_insert_flag(pre_0035_url, check_result_id=untouched_id, overridden=False))
    asyncio.run(_insert_flag(pre_0035_url, check_result_id=engaged_id, overridden=True))

    command.upgrade(_alembic_config(pre_0035_url), "head")

    rows = asyncio.run(
        _fetch_all(
            pre_0035_url,
            "SELECT id FROM check_result WHERE check_run_id = $1 AND kind = 'citation_integrity'",
            ids["check_run_id"],
        )
    )
    assert [r["id"] for r in rows] == [engaged_id]


def test_ambiguous_double_resolution_blocks_the_migration_instead_of_silently_deleting_one(
    pre_0035_url,
):
    """`backend-critic`'s more serious finding (BUG-144 review): if BOTH
    twins were independently resolved (possibly to DIFFERENT verdicts),
    there is no safe automatic answer, and this migration runs against
    real production data with no undo. The dedup step must leave BOTH
    rows untouched in that case, which makes the unique-index creation
    fail loudly -- an actionable migration error naming the real
    conflict, not a silent loss of one instructor's real decision."""
    ids = asyncio.run(_seed_base(pre_0035_url))
    asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=ids["criterion_id"],
            kind="semantic",
            detail='{"resolution": {"type": "mark_pass", "reason": "Verified in Chapter 2."}}',
        )
    )
    asyncio.run(
        _insert_check_result(
            pre_0035_url,
            check_run_id=ids["check_run_id"],
            criterion_id=ids["criterion_id"],
            kind="semantic",
            detail='{"resolution": {"type": "mark_fail", "reason": "Actually incomplete."}}',
        )
    )

    with pytest.raises(Exception, match="duplicate key|uq_check_result_run_criterion"):
        command.upgrade(_alembic_config(pre_0035_url), "head")
