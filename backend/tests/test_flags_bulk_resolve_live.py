"""V-089 HTTP surface: POST /check-runs/{id}/flags/bulk-resolve.

One confirm or dismiss for many findings of one run: all or nothing, one
audit entry per finding carrying the batch id, one rescore, and each
finding changed exactly as its single-flag action changes it (BUG-518
confirm-finding, V-026 override).
"""

import asyncio
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.config import get_settings
from app.report.service import aggregate_and_score

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_flagsbulktest"
PASSWORD = "s3cret!"


@pytest.fixture(scope="module")
def api_scratch_url():
    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture()
def client(api_scratch_url, tmp_path, monkeypatch):
    import app.db as db

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    from app.main import app

    with TestClient(app) as c:
        yield c
    db._engine = None
    auth_service._rate_limiter = None
    get_settings.cache_clear()


async def _with_session(url, work):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(url))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await work(session)
    finally:
        await engine.dispose()


def _db(url, work):
    return asyncio.run(_with_session(url, work))


@pytest.fixture()
def seeded(client, api_scratch_url):
    """Two instructors. Mine: run A (done) with open / dismissed / confirmed /
    source-confirmed findings, run B with one finding. Theirs: one run, one
    finding."""
    from sqlalchemy import text

    from app.models.enums import CheckKind, CheckRunStatus, FlagSeverity, ResultOutcome
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Rubric
    from app.models.run import CheckResult, CheckRun, Flag

    async def run_with_flags(session, instructor, label, flags):
        manuscript = Manuscript(instructor_id=instructor.id, group_label=label, file_ref="x.pdf")
        rubric = Rubric(instructor_id=instructor.id, title="Format", source_file="r.pdf")
        session.add_all([manuscript, rubric])
        await session.commit()
        run = CheckRun(manuscript_id=manuscript.id, rubric_id=rubric.id, status=CheckRunStatus.done)
        session.add(run)
        await session.commit()
        result = CheckResult(
            check_run_id=run.id,
            criterion_id=None,
            kind=CheckKind.citation_integrity,
            outcome=ResultOutcome.failed,
            score=None,
            detail={"basis": "external-api", "verdict": "not_supported"},
        )
        session.add(result)
        await session.commit()
        rows = [
            Flag(
                check_result_id=result.id,
                confidence=Decimal("0.667"),
                evidence_excerpt=f"Claim {index}.",
                page_anchor=f"page {index + 1}",
                **fields,
            )
            for index, fields in enumerate(flags)
        ]
        session.add_all(rows)
        await session.commit()
        await aggregate_and_score(session, run.id, commit=True)
        return run.id, [row.id for row in rows]

    async def seed(session):
        await session.execute(
            text(
                "TRUNCATE audit_log, flag, readiness_report, check_result, "
                "check_run, rubric, manuscript, instructor RESTART IDENTITY CASCADE"
            )
        )
        mine = Instructor(
            email="bulk@tip.edu.ph", display_name="Bulk Prof", password_hash=hash_password(PASSWORD)
        )
        theirs = Instructor(
            email="other@tip.edu.ph",
            display_name="Other Prof",
            password_hash=hash_password(PASSWORD),
        )
        session.add_all([mine, theirs])
        await session.commit()
        run_a, flags_a = await run_with_flags(
            session,
            mine,
            "G-1",
            [
                {"severity": FlagSeverity.high},
                {"severity": FlagSeverity.med},
                {
                    "severity": FlagSeverity.med,
                    "overridden": True,
                    "override_reason": "Earlier dismissal.",
                },
                {
                    "severity": FlagSeverity.low,
                    "finding_confirmed": True,
                    "finding_confirm_note": "Table 3 total is off by 12; group told.",
                },
                {
                    "severity": FlagSeverity.low,
                    "overridden": True,
                    "override_reason": "Found in the library.",
                    "confirmed_citation_source": True,
                },
            ],
        )
        run_b, flags_b = await run_with_flags(
            session, mine, "G-2", [{"severity": FlagSeverity.med}]
        )
        run_t, flags_t = await run_with_flags(
            session, theirs, "G-9", [{"severity": FlagSeverity.med}]
        )
        return {
            "run": run_a,
            "open_high": flags_a[0],
            "open_med": flags_a[1],
            "dismissed": flags_a[2],
            "confirmed": flags_a[3],
            "source_confirmed": flags_a[4],
            "other_run": run_b,
            "other_run_flag": flags_b[0],
            "their_run": run_t,
            "their_flag": flags_t[0],
        }

    ids = _db(api_scratch_url, seed)
    client.post("/auth/login", json={"email": "bulk@tip.edu.ph", "password": PASSWORD})
    return client, ids


def _revision(client, run_id):
    response = client.get(f"/check-runs/{run_id}/report")
    assert response.status_code == 200, response.text
    return response.json()["assessment_revision"]


def _report(client, run_id):
    response = client.get(f"/check-runs/{run_id}/report")
    assert response.status_code == 200, response.text
    return response.json()


def _flag_rows(url, flag_ids):
    from sqlalchemy import select

    from app.models.run import Flag

    async def read(session):
        rows = (await session.execute(select(Flag).where(Flag.id.in_(flag_ids)))).scalars()
        return {
            row.id: {
                "overridden": row.overridden,
                "override_reason": row.override_reason,
                "finding_confirmed": row.finding_confirmed,
                "finding_confirm_note": row.finding_confirm_note,
                "finding_confirmed_at": row.finding_confirmed_at,
                "confirmed_citation_source": row.confirmed_citation_source,
            }
            for row in rows
        }

    return _db(url, read)


def _audit(url, event_type=None):
    from sqlalchemy import select

    from app.models.audit import AuditLog

    async def read(session):
        query = select(AuditLog).order_by(AuditLog.id)
        if event_type is not None:
            query = query.where(AuditLog.event_type == event_type)
        return [
            {"event_type": row.event_type, "check_run_id": row.check_run_id, **row.payload}
            for row in (await session.execute(query)).scalars()
        ]

    return _db(url, read)


def _resolution_audit(url):
    return [
        row
        for row in _audit(url)
        if row["event_type"] in ("flag_overridden", "flag_finding_confirmed")
    ]


def _post(client, run_id, **body):
    return client.post(f"/check-runs/{run_id}/flags/bulk-resolve", json=body)


def test_bulk_resolve_requires_auth(client):
    response = client.post(
        "/check-runs/1/flags/bulk-resolve",
        json={"action": "confirm", "flag_ids": [1], "expected_revision": 0},
    )
    assert response.status_code == 401


def test_bulk_confirm_mixed_states_rescores_once_and_audits_each(
    seeded, api_scratch_url, monkeypatch
):
    import app.flags.service as flag_service

    client, ids = seeded
    run = ids["run"]
    changed = [ids["open_high"], ids["dismissed"]]
    targets = [ids["open_high"], ids["dismissed"], ids["confirmed"]]
    before = _report(client, run)
    calls = []
    real = flag_service.aggregate_and_score

    async def counting(session, check_run_id, *args, **kwargs):
        calls.append(check_run_id)
        return await real(session, check_run_id, *args, **kwargs)

    monkeypatch.setattr(flag_service, "aggregate_and_score", counting)

    response = _post(
        client,
        run,
        action="confirm",
        flag_ids=targets,
        expected_revision=before["assessment_revision"],
        note="  Checked against the manuscript.  ",
        unopened_count=1,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == [run]
    assert sorted(row["id"] for row in body["flags"]) == sorted(changed)
    assert body["unchanged_ids"] == [ids["confirmed"]]
    assert all(row["finding_confirmed"] and not row["overridden"] for row in body["flags"])
    assert body["report"]["assessment_revision"] == before["assessment_revision"] + 1
    batch_id = body["batch_id"]
    assert isinstance(batch_id, str) and batch_id

    stored = _flag_rows(api_scratch_url, targets)
    assert {stored[i]["finding_confirm_note"] for i in changed} == {
        "Checked against the manuscript."
    }
    assert all(stored[i]["override_reason"] is None for i in changed)
    assert len({stored[i]["finding_confirmed_at"] for i in changed}) == 1
    # Already confirmed: its own note is kept, not replaced by the batch note.
    assert stored[ids["confirmed"]]["finding_confirm_note"] == (
        "Table 3 total is off by 12; group told."
    )

    audit = _resolution_audit(api_scratch_url)
    assert sorted(row["flag_id"] for row in audit) == sorted(changed)
    for row in audit:
        assert row["event_type"] == "flag_finding_confirmed"
        assert row["check_run_id"] == run
        assert row["batch_id"] == batch_id
        assert row["client_reported_unopened_count"] == 1
        assert row["note"] == "Checked against the manuscript."
        assert row["assessment_revision_before"] == before["assessment_revision"]
        assert row["assessment_revision"] == before["assessment_revision"] + 1

    # Confirmed findings still count for readiness: reopening the earlier
    # dismissal puts its deduction back, and no finding stopped counting.
    after = body["report"]
    assert after["flag_deduction"] > before["flag_deduction"]
    assert after["unresolved_high_flag_count"] == before["unresolved_high_flag_count"]
    assert _report(client, run)["flag_deduction"] == after["flag_deduction"]


def test_bulk_dismiss_copies_reason_clears_confirmations_and_rescores(seeded, api_scratch_url):
    client, ids = seeded
    run = ids["run"]
    targets = [ids["open_high"], ids["open_med"], ids["confirmed"]]
    before = _report(client, run)

    response = _post(
        client,
        run,
        action="dismiss",
        flag_ids=[*targets, ids["dismissed"]],
        expected_revision=before["assessment_revision"],
        reason="  Reviewed with the group; not an issue.  ",
        unopened_count=4,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unchanged_ids"] == [ids["dismissed"]]
    kept = _flag_rows(api_scratch_url, [ids["dismissed"]])[ids["dismissed"]]
    assert kept["override_reason"] == "Earlier dismissal."
    assert {
        row["client_reported_unopened_count"] for row in _resolution_audit(api_scratch_url)
    } == {4}
    assert all(row["overridden"] and not row["finding_confirmed"] for row in body["flags"])
    stored = _flag_rows(api_scratch_url, targets)
    for row in stored.values():
        assert row["override_reason"] == "Reviewed with the group; not an issue."
        assert row["finding_confirmed"] is False
        assert row["finding_confirm_note"] is None and row["finding_confirmed_at"] is None
    audit = _resolution_audit(api_scratch_url)
    assert sorted(row["flag_id"] for row in audit) == sorted(targets)
    assert {row["event_type"] for row in audit} == {"flag_overridden"}
    assert {row["batch_id"] for row in audit} == {body["batch_id"]}
    assert {row["reason"] for row in audit} == {"Reviewed with the group; not an issue."}
    assert body["report"]["flag_deduction"] < before["flag_deduction"]
    assert body["report"]["unresolved_high_flag_count"] == 0


def test_bulk_and_single_confirm_write_the_same_flag_state_and_audit_shape(seeded, api_scratch_url):
    client, ids = seeded
    run = ids["run"]
    single = client.post(
        f"/flags/{ids['open_high']}/confirm-finding",
        json={"expected_revision": _revision(client, run), "note": "Real."},
    )
    assert single.status_code == 200, single.text
    bulk = _post(
        client,
        run,
        action="confirm",
        flag_ids=[ids["open_med"]],
        expected_revision=_revision(client, run),
        note="Real.",
    )
    assert bulk.status_code == 200, bulk.text

    stored = _flag_rows(api_scratch_url, [ids["open_high"], ids["open_med"]])
    one, many = stored[ids["open_high"]], stored[ids["open_med"]]
    for key in ("overridden", "override_reason", "finding_confirmed", "finding_confirm_note"):
        assert one[key] == many[key]
    single_row, bulk_row = _resolution_audit(api_scratch_url)
    assert single_row["event_type"] == bulk_row["event_type"] == "flag_finding_confirmed"
    assert set(bulk_row) - set(single_row) == {"batch_id", "batch_selected_count"}
    assert "batch_id" not in single_row


def test_duplicate_ids_change_and_audit_each_finding_once(seeded, api_scratch_url):
    client, ids = seeded
    run = ids["run"]
    response = _post(
        client,
        run,
        action="confirm",
        flag_ids=[ids["open_med"], ids["open_med"], ids["open_high"], ids["open_med"]],
        expected_revision=_revision(client, run),
    )
    assert response.status_code == 200, response.text
    assert sorted(row["id"] for row in response.json()["flags"]) == sorted(
        [ids["open_med"], ids["open_high"]]
    )
    assert sorted(row["flag_id"] for row in _resolution_audit(api_scratch_url)) == sorted(
        [ids["open_med"], ids["open_high"]]
    )


def test_re_resolving_leaves_findings_untouched_and_a_replay_is_refused(
    seeded, api_scratch_url, monkeypatch
):
    import app.flags.service as flag_service

    client, ids = seeded
    run = ids["run"]
    targets = [ids["open_high"], ids["open_med"]]
    revision = _revision(client, run)
    first = _post(
        client, run, action="confirm", flag_ids=targets, expected_revision=revision, note="Real."
    )
    assert first.status_code == 200, first.text
    assert first.json()["unchanged_ids"] == []
    stored = _flag_rows(api_scratch_url, targets)

    replay = _post(client, run, action="confirm", flag_ids=targets, expected_revision=revision)
    assert replay.status_code == 409
    assert len(_resolution_audit(api_scratch_url)) == 2

    calls = []

    async def counting(*args, **kwargs):
        calls.append(1)

    monkeypatch.setattr(flag_service, "aggregate_and_score", counting)
    again = _post(client, run, action="confirm", flag_ids=targets, expected_revision=revision + 1)
    assert again.status_code == 200, again.text
    body = again.json()
    # Nothing to change: nothing written, no batch, no rescore, same revision.
    assert body["flags"] == [] and body["unchanged_ids"] == targets
    assert body["batch_id"] is None
    assert body["report"]["assessment_revision"] == revision + 1
    assert calls == []
    assert _revision(client, run) == revision + 1
    assert len(_resolution_audit(api_scratch_url)) == 2
    assert _flag_rows(api_scratch_url, targets) == stored


def _assert_nothing_changed(client, api_scratch_url, ids, revision):
    assert _revision(client, ids["run"]) == revision
    assert _resolution_audit(api_scratch_url) == []
    stored = _flag_rows(api_scratch_url, [ids["open_high"], ids["open_med"]])
    assert all(not row["finding_confirmed"] and not row["overridden"] for row in stored.values())


@pytest.mark.parametrize(
    "foreign_key", ["their_flag", "other_run_flag", "missing"], ids=lambda key: key
)
def test_any_id_outside_the_run_or_account_is_404_and_changes_nothing(
    seeded, api_scratch_url, foreign_key
):
    client, ids = seeded
    run = ids["run"]
    revision = _revision(client, run)
    foreign = 999_999 if foreign_key == "missing" else ids[foreign_key]
    response = _post(
        client,
        run,
        action="dismiss",
        flag_ids=[ids["open_high"], foreign, ids["open_med"]],
        expected_revision=revision,
        reason="Not an issue.",
    )
    assert response.status_code == 404
    assert "Nothing was changed" in response.text
    _assert_nothing_changed(client, api_scratch_url, ids, revision)


def test_another_instructors_run_is_404(seeded, api_scratch_url):
    client, ids = seeded
    response = _post(
        client,
        ids["their_run"],
        action="confirm",
        flag_ids=[ids["their_flag"]],
        expected_revision=0,
    )
    assert response.status_code == 404
    assert (
        _flag_rows(api_scratch_url, [ids["their_flag"]])[ids["their_flag"]]["finding_confirmed"]
        is False
    )
    assert _resolution_audit(api_scratch_url) == []


def test_stale_revision_is_409_and_changes_nothing(seeded, api_scratch_url):
    client, ids = seeded
    revision = _revision(client, ids["run"])
    response = _post(
        client,
        ids["run"],
        action="confirm",
        flag_ids=[ids["open_high"], ids["open_med"]],
        expected_revision=revision + 3,
    )
    assert response.status_code == 409
    _assert_nothing_changed(client, api_scratch_url, ids, revision)


def test_confirming_a_source_confirmed_finding_refuses_the_whole_batch(seeded, api_scratch_url):
    """Confirming would clear its recorded reason, the source verification."""
    client, ids = seeded
    revision = _revision(client, ids["run"])
    response = _post(
        client,
        ids["run"],
        action="confirm",
        flag_ids=[ids["open_high"], ids["source_confirmed"], ids["open_med"]],
        expected_revision=revision,
    )
    assert response.status_code == 409
    assert "1 of the selected findings" in response.text
    _assert_nothing_changed(client, api_scratch_url, ids, revision)


def test_dismissing_leaves_a_source_confirmed_finding_unchanged(seeded, api_scratch_url):
    client, ids = seeded
    response = _post(
        client,
        ids["run"],
        action="dismiss",
        flag_ids=[ids["open_high"], ids["source_confirmed"]],
        expected_revision=_revision(client, ids["run"]),
        reason="Select-all cleanup.",
        unopened_count=2,
    )
    assert response.status_code == 200, response.text
    assert response.json()["unchanged_ids"] == [ids["source_confirmed"]]
    kept = _flag_rows(api_scratch_url, [ids["source_confirmed"]])[ids["source_confirmed"]]
    assert kept["override_reason"] == "Found in the library."
    assert kept["confirmed_citation_source"] is True
    (row,) = _resolution_audit(api_scratch_url)
    assert row["flag_id"] == ids["open_high"]
    # The unopened count is read against the whole selection, skipped included.
    assert row["batch_selected_count"] == 2
    assert row["client_reported_unopened_count"] == 2


def test_nothing_to_change_after_a_concurrent_change_is_409(seeded, api_scratch_url, monkeypatch):
    """The unchanged judgment was made at the sent revision; if another change
    lands before the response is read, the instructor must reload."""
    from sqlalchemy import select

    import app.flags.service as flag_service
    from app.models.run import Flag, ReadinessReport

    client, ids = seeded
    run = ids["run"]
    revision = _revision(client, run)
    real = flag_service.get_report

    async def concurrent_dismissal(session):
        flag = await session.get(Flag, ids["confirmed"])
        flag.overridden, flag.override_reason = True, "Concurrent dismissal."
        flag.finding_confirmed = False
        report = await session.scalar(
            select(ReadinessReport).where(ReadinessReport.check_run_id == run)
        )
        report.assessment_revision += 1
        await session.commit()

    async def racing(session, check_run_id, instructor_id):
        await _with_session(api_scratch_url, concurrent_dismissal)
        return await real(session, check_run_id, instructor_id)

    monkeypatch.setattr(flag_service, "get_report", racing)
    response = _post(
        client, run, action="confirm", flag_ids=[ids["confirmed"]], expected_revision=revision
    )
    assert response.status_code == 409
    assert "changed after you reviewed it" in response.text
    assert _resolution_audit(api_scratch_url) == []


def test_a_failure_after_findings_changed_rolls_everything_back(
    seeded, api_scratch_url, monkeypatch
):
    import app.flags.service as flag_service

    client, ids = seeded
    revision = _revision(client, ids["run"])

    async def failing(*args, **kwargs):
        raise RuntimeError("rescore failed")

    monkeypatch.setattr(flag_service, "aggregate_and_score", failing)
    with pytest.raises(RuntimeError, match="rescore failed"):
        _post(
            client,
            ids["run"],
            action="dismiss",
            flag_ids=[ids["open_high"], ids["open_med"]],
            expected_revision=revision,
            reason="Not an issue.",
        )
    monkeypatch.undo()
    _assert_nothing_changed(client, api_scratch_url, ids, revision)


def test_a_decided_report_refuses_bulk_changes(seeded, api_scratch_url):
    from sqlalchemy import select

    from app.models.enums import ReportDecision
    from app.models.run import ReadinessReport

    client, ids = seeded

    async def decide(session):
        report = await session.scalar(
            select(ReadinessReport).where(ReadinessReport.check_run_id == ids["run"])
        )
        report.decision = next(iter(ReportDecision))
        await session.commit()

    _db(api_scratch_url, decide)
    revision = _revision(client, ids["run"])
    response = _post(
        client,
        ids["run"],
        action="confirm",
        flag_ids=[ids["open_high"]],
        expected_revision=revision,
    )
    assert response.status_code == 409
    assert _resolution_audit(api_scratch_url) == []


@pytest.mark.parametrize(
    "body",
    [
        {"action": "confirm", "flag_ids": []},
        {"action": "dismiss", "flag_ids": [1]},
        {"action": "dismiss", "flag_ids": [1], "reason": "   "},
        {"action": "dismiss", "flag_ids": [1], "reason": "Fine.", "note": "x"},
        {"action": "confirm", "flag_ids": [1], "reason": "Fine."},
        {"action": "accept", "flag_ids": [1]},
        {"action": "confirm", "flag_ids": [1], "expected_revision": "0"},
        {"action": "confirm", "flag_ids": [1], "surprise": True},
        {"action": "confirm", "flag_ids": [True]},
        {"action": "confirm", "flag_ids": ["1"]},
        {"action": "confirm", "flag_ids": [1.0]},
        {"action": "confirm", "flag_ids": [0]},
        {"action": "confirm", "flag_ids": [-1]},
        {"action": "confirm", "flag_ids": [2**63]},
        {"action": "confirm", "flag_ids": [1], "note": "x" * 2001},
        {"action": "dismiss", "flag_ids": [1], "reason": "x" * 2001},
        {"action": "confirm", "flag_ids": [1], "note": "a\x00b"},
        {"action": "dismiss", "flag_ids": [1], "reason": "a\x00b"},
        {"action": "dismiss", "flag_ids": [1], "reason": "Fine.", "unopened_count": -1},
        {"action": "dismiss", "flag_ids": [1], "reason": "Fine.", "unopened_count": 2},
        {"action": "dismiss", "flag_ids": [1], "reason": "Fine.", "unopened_count": "1"},
    ],
    ids=[
        "empty",
        "dismiss-no-reason",
        "dismiss-blank-reason",
        "dismiss-with-note",
        "confirm-with-reason",
        "unknown-action",
        "string-revision",
        "unknown-field",
        "bool-id",
        "string-id",
        "float-id",
        "zero-id",
        "negative-id",
        "id-beyond-bigint",
        "note-too-long",
        "reason-too-long",
        "nul-in-note",
        "nul-in-reason",
        "negative-unopened",
        "unopened-above-selection",
        "string-unopened",
    ],
)
def test_malformed_requests_are_422(seeded, api_scratch_url, body):
    client, ids = seeded
    payload = {"expected_revision": _revision(client, ids["run"]), **body}
    assert _post(client, ids["run"], **payload).status_code == 422
    assert _resolution_audit(api_scratch_url) == []


def test_more_than_the_configured_maximum_is_422(seeded, api_scratch_url, monkeypatch):
    client, ids = seeded
    monkeypatch.setenv("FLAG_BULK_RESOLVE_MAX_FLAGS", "2")
    get_settings.cache_clear()
    revision = _revision(client, ids["run"])
    three = [ids["open_high"], ids["open_med"], ids["confirmed"]]
    too_many = _post(
        client, ids["run"], action="confirm", flag_ids=three, expected_revision=revision
    )
    assert too_many.status_code == 422
    assert "at most 2" in too_many.text
    # The list as sent is bounded too, duplicates included.
    repeated = _post(
        client,
        ids["run"],
        action="confirm",
        flag_ids=[ids["open_high"], ids["open_med"], ids["open_high"]],
        expected_revision=revision,
    )
    assert repeated.status_code == 422
    at_limit = _post(
        client,
        ids["run"],
        action="confirm",
        flag_ids=[ids["open_high"], ids["open_med"]],
        expected_revision=revision,
    )
    assert at_limit.status_code == 200, at_limit.text


def test_the_text_limit_comes_from_config(seeded, api_scratch_url, monkeypatch):
    client, ids = seeded
    monkeypatch.setenv("FLAG_BULK_RESOLVE_MAX_TEXT_CHARS", "10")
    get_settings.cache_clear()
    revision = _revision(client, ids["run"])
    body = {"action": "confirm", "flag_ids": [ids["open_high"]], "expected_revision": revision}
    too_long = _post(client, ids["run"], **body, note="eleven char")
    assert too_long.status_code == 422
    assert "10 characters" in too_long.text
    assert _post(client, ids["run"], **body, note="  ten chars  ").status_code == 200
