"""V-084 database foundation preserves historical assessments and audit records."""

import asyncio
import json
import os

import asyncpg
import pytest

from alembic import command
from app.models.run import ReadinessReport
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

SCRATCH_DB = "veridical_assessment_revision_test"


def test_revision_model_matches_database_contract():
    column = ReadinessReport.__table__.c.assessment_revision
    assert column.nullable is False
    assert str(column.server_default.arg) == "0"
    assert any(
        constraint.name == "ck_readiness_report_assessment_revision_nonnegative"
        for constraint in ReadinessReport.__table__.constraints
    )


@pytest.fixture(scope="module")
def migration_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    try:
        yield _swap_db(base, SCRATCH_DB)
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="integration: requires owned Postgres")
def test_upgrade_preserves_decisions_scores_and_audits_and_reverses_cleanly(migration_url):
    cfg = _alembic_config(migration_url)
    command.upgrade(cfg, "0038")

    async def seed():
        connection = await asyncpg.connect(migration_url)
        try:
            instructor = await connection.fetchval(
                "INSERT INTO instructor(email, display_name) "
                "VALUES('revision@test.local','Instructor') RETURNING id"
            )
            manuscript = await connection.fetchval(
                "INSERT INTO manuscript(instructor_id,group_label,file_ref) "
                "VALUES($1,'Original group','original.pdf') RETURNING id",
                instructor,
            )
            rubric = await connection.fetchval(
                "INSERT INTO rubric(instructor_id,title,source_file) "
                "VALUES($1,'Original format','format.pdf') RETURNING id",
                instructor,
            )
            for decision in (None, "returned"):
                run = await connection.fetchval(
                    "INSERT INTO check_run(manuscript_id,rubric_id,status) "
                    "VALUES($1,$2,'done') RETURNING id",
                    manuscript,
                    rubric,
                )
                await connection.execute(
                    "INSERT INTO readiness_report(check_run_id,composite_score,status,decision,"
                    "decision_note,decided_at,decided_by_instructor_id) "
                    "VALUES($1,33.33,'conditionally_ready',$2::varchar,$3,"
                    "CASE WHEN $2::varchar IS NULL THEN NULL ELSE now() END,$4)",
                    run,
                    decision,
                    "Original reason" if decision else None,
                    instructor if decision else None,
                )
                await connection.execute(
                    "INSERT INTO audit_log(check_run_id,event_type,payload) "
                    "VALUES($1,'original_assessment',$2::jsonb)",
                    run,
                    json.dumps({"original": True}),
                )
            return await snapshots(connection)
        finally:
            await connection.close()

    async def snapshots(connection):
        reports = await connection.fetch(
            "SELECT to_jsonb(r) - 'assessment_revision' AS body FROM readiness_report r ORDER BY id"
        )
        audits = await connection.fetch("SELECT to_jsonb(a) AS body FROM audit_log a ORDER BY id")
        return (
            [json.loads(row["body"]) for row in reports],
            [json.loads(row["body"]) for row in audits],
        )

    before = asyncio.run(seed())
    command.upgrade(cfg, "0039")

    async def verify():
        connection = await asyncpg.connect(migration_url)
        try:
            assert await snapshots(connection) == before
            assert await connection.fetchval(
                "SELECT array_agg(assessment_revision ORDER BY id) FROM readiness_report"
            ) == [0, 0]
            column = await connection.fetchrow(
                "SELECT data_type,is_nullable,column_default FROM information_schema.columns "
                "WHERE table_name='readiness_report' AND column_name='assessment_revision'"
            )
            assert column["data_type"] == "bigint" and column["is_nullable"] == "NO"
            assert column["column_default"] == "'0'::bigint", dict(column)
            assert await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_constraint "
                "WHERE conname='ck_readiness_report_assessment_revision_nonnegative')"
            )
            new_run = await connection.fetchval(
                "INSERT INTO check_run(manuscript_id,rubric_id,status) "
                "SELECT manuscript_id,rubric_id,'done' FROM check_run WHERE id=1 RETURNING id"
            )
            assert (
                await connection.fetchval(
                    "INSERT INTO readiness_report(check_run_id,status) "
                    "VALUES($1,'needs_review') RETURNING assessment_revision",
                    new_run,
                )
                == 0
            )
            await connection.execute("DELETE FROM readiness_report WHERE check_run_id=$1", new_run)
            await connection.execute("DELETE FROM check_run WHERE id=$1", new_run)
            large_revision = 2**32
            await connection.execute(
                "UPDATE readiness_report SET assessment_revision=$1 WHERE id=1", large_revision
            )
            with pytest.raises(asyncpg.CheckViolationError):
                async with connection.transaction():
                    await connection.execute(
                        "UPDATE readiness_report SET assessment_revision=99 WHERE id=1"
                    )
                    await connection.execute(
                        "UPDATE readiness_report SET assessment_revision=-1 WHERE id=1"
                    )
            with pytest.raises(asyncpg.NotNullViolationError):
                await connection.execute(
                    "UPDATE readiness_report SET assessment_revision=NULL WHERE id=1"
                )
            assert (
                await connection.fetchval(
                    "SELECT assessment_revision FROM readiness_report WHERE id=1"
                )
                == large_revision
            )
            assert await snapshots(connection) == before
        finally:
            await connection.close()

    asyncio.run(verify())
    command.downgrade(cfg, "0038")

    async def verify_downgrade():
        connection = await asyncpg.connect(migration_url)
        try:
            assert await snapshots(connection) == before
        finally:
            await connection.close()

    asyncio.run(verify_downgrade())
    command.upgrade(cfg, "0039")

    # Re-upgrade establishes a baseline; it does not restore removed version data.
    async def verify_reupgrade():
        connection = await asyncpg.connect(migration_url)
        try:
            assert await snapshots(connection) == before
            assert await connection.fetchval(
                "SELECT array_agg(assessment_revision ORDER BY id) FROM readiness_report"
            ) == [0, 0]
        finally:
            await connection.close()

    asyncio.run(verify_reupgrade())
