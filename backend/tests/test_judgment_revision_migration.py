"""V-085 real upgrade/downgrade preserves grades/JSON/audit and recognizes identity."""

import asyncio
import json
import os
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest

from alembic import command
from app.models.run import CheckResult
from app.report.judgment_snapshot import manual_resolution
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

SCRATCH_DB = "veridical_judgment_migration_aeac_test"


def test_nullable_judgment_token_has_no_fictional_default_and_nonnegative_constraint():
    column = CheckResult.__table__.c.judgment_revision
    assert column.nullable and column.server_default is None
    assert any(
        c.name == "ck_check_result_judgment_revision_nonnegative"
        for c in CheckResult.__table__.constraints
    )


@pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="integration: own Postgres required")
def test_migration_baselines_match_runtime_without_rewriting_any_record():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    cfg = _alembic_config(url)
    try:
        command.upgrade(cfg, "0039")

        async def seed():
            c = await asyncpg.connect(url)
            try:
                actor = await c.fetchval(
                    "INSERT INTO instructor(email,display_name) "
                    "VALUES('legacy@test.local','Earlier instructor') RETURNING id"
                )
                rubric = await c.fetchval(
                    "INSERT INTO rubric(instructor_id,title) "
                    "VALUES($1,'Producing rubric') RETURNING id",
                    actor,
                )
                other = await c.fetchval(
                    "INSERT INTO rubric(instructor_id,title) "
                    "VALUES($1,'Other rubric') RETURNING id",
                    actor,
                )
                manuscript = await c.fetchval(
                    "INSERT INTO manuscript(instructor_id,group_label,file_ref) "
                    "VALUES($1,'Synthetic legacy','synthetic.pdf') RETURNING id",
                    actor,
                )
                cases = [
                    ("mark_pass", "passed", 100, "Earlier reason", None),
                    ("mark_fail", "failed", 0, "Earlier reason", None),
                    ("mark_level", "passed", 33.33, "Earlier reason", None),
                    ("accept_majority", "passed", 50, "Earlier reason", None),
                    ("accept_majority", "failed", 0, "Earlier reason", None),
                    ("needs_document", "not_applicable", None, "Earlier reason", None),
                    ("mark_pass", "passed", 100, "old", None),
                    ("mark_pass", "passed", 100, "x" * 1500, None),
                    ("mark_pass", "passed", 100, "\t\u2003\n", None),
                    ("mark_pass", "passed", 100, "", None),
                    ("mark_fail", "failed", 0, "", None),
                    ("mark_level", "passed", 33.33, "", None),
                    ("accept_majority", "passed", 100, "", None),
                    ("needs_document", "not_applicable", None, "", None),
                    ("mark_pass", "passed", 100, 42, None),
                    ("unknown", "passed", 100, "Earlier reason", None),
                    ("mark_pass", "passed", 50, "Earlier reason", None),
                    ("mark_pass", "escalated", None, "Earlier reason", None),
                    ("mark_pass", "passed", 100, "Earlier reason", "wrong_rubric"),
                    ("mark_pass", "passed", 100, "Earlier reason", "integrity"),
                    ("mark_pass", "passed", 100, "Earlier reason", "ai_only"),
                    ("mark_pass", "passed", 100, "Earlier reason", "malformed"),
                ]
                expected = []
                for position, (verb, outcome, score, reason, variant) in enumerate(cases):
                    criterion = await c.fetchval(
                        "INSERT INTO criterion(rubric_id,type,text,weight,position) "
                        "VALUES($1,'semantic',$2,1,$3) RETURNING id",
                        other if variant == "wrong_rubric" else rubric,
                        f"Synthetic criterion {position}",
                        position,
                    )
                    run = await c.fetchval(
                        "INSERT INTO check_run(manuscript_id,rubric_id,status) "
                        "VALUES($1,$2,'done') RETURNING id",
                        manuscript,
                        rubric,
                    )
                    detail = {
                        "reason": "Original analysis retained",
                        "resolution": {"type": verb, "reason": reason},
                    }
                    if variant == "ai_only":
                        detail.pop("resolution")
                    if variant == "malformed":
                        detail["resolution"] = True
                    kind = "internal_agreement" if variant == "integrity" else "semantic"
                    criterion_id = None if variant == "integrity" else criterion
                    identifier = await c.fetchval(
                        "INSERT INTO check_result"
                        "(check_run_id,criterion_id,kind,outcome,score,detail) "
                        "VALUES($1,$2,$3,$4,$5,$6::jsonb) RETURNING id",
                        run,
                        criterion_id,
                        kind,
                        outcome,
                        score,
                        json.dumps(detail),
                    )
                    row = SimpleNamespace(
                        criterion_id=criterion_id,
                        kind=kind,
                        outcome=outcome,
                        score=Decimal(str(score)) if score is not None else None,
                        detail=detail,
                    )
                    expected.append(
                        (
                            identifier,
                            0
                            if variant != "wrong_rubric" and manual_resolution(row) is not None
                            else None,
                        )
                    )
                    await c.execute(
                        "INSERT INTO audit_log(check_run_id,event_type,payload) "
                        "VALUES($1,'original_legacy',$2::jsonb)",
                        run,
                        json.dumps({"old": True, "detail": detail}),
                    )
                return expected, await snapshots(c)
            finally:
                await c.close()

        async def snapshots(c):
            return {
                "results": [
                    r["body"]
                    for r in await c.fetch(
                        "SELECT (to_jsonb(r)-'judgment_revision')::text AS body "
                        "FROM check_result r ORDER BY id"
                    )
                ],
                "audits": [
                    r["body"]
                    for r in await c.fetch(
                        "SELECT to_jsonb(a)::text AS body FROM audit_log a ORDER BY id"
                    )
                ],
            }

        expected, before = asyncio.run(seed())
        # Applied 0040 retains its original reason contract; the additive 0043
        # migration aligns optional ordinary comments without changing facts.
        command.upgrade(cfg, "head")

        async def verify():
            c = await asyncpg.connect(url)
            try:
                assert await snapshots(c) == before
                assert [
                    (r["id"], r["judgment_revision"])
                    for r in await c.fetch(
                        "SELECT id,judgment_revision FROM check_result ORDER BY id"
                    )
                ] == expected
                with pytest.raises(asyncpg.CheckViolationError):
                    await c.execute(
                        "UPDATE check_result SET judgment_revision=-1 WHERE id=$1", expected[0][0]
                    )
                await c.execute(
                    "UPDATE check_result SET judgment_revision=$1 WHERE id=$2",
                    2**32,
                    expected[0][0],
                )
                assert (
                    await c.fetchval(
                        "SELECT judgment_revision FROM check_result WHERE id=$1", expected[0][0]
                    )
                    == 2**32
                )
            finally:
                await c.close()

        asyncio.run(verify())
        command.downgrade(cfg, "0039")

        async def verify_downgrade():
            c = await asyncpg.connect(url)
            try:
                assert await snapshots(c) == before
            finally:
                await c.close()

        asyncio.run(verify_downgrade())
        command.upgrade(cfg, "head")
        asyncio.run(verify())
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{SCRATCH_DB}" WITH (FORCE)'))
