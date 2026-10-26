"""V-085 saved assignment uses actual PostgreSQL locks and normal cookie auth."""

import asyncio
import os
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.auth.router import router as auth_router
from app.auth.security import hash_password
from app.config import get_settings
from app.db import get_session, sqlalchemy_url
from app.errors import HTTP_STATUS, ConflictError, NotFoundError, VeridicalError
from app.groups.router import router as groups_router
from app.groups.selection import SelectGroupRequest, get_saved_group, select_saved_group
from app.ingest.router import router as ingest_router
from app.ingest.service import confirm_manuscript_group, get_manuscript_group
from app.models.audit import AuditLog
from app.models.enums import ManuscriptOrigin
from app.models.group import Group, GroupMember, Program
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript
from tests.test_schema import _admin_execute, _alembic_config, _swap_db

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")
SCRATCH_DB = "veridical_saved_group_selection_test"


@pytest.fixture(scope="module")
def scratch_url():
    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    try:
        url = _swap_db(base, SCRATCH_DB)
        command.upgrade(_alembic_config(url), "head")
        yield url
    finally:
        asyncio.run(_admin_execute(base, f'DROP DATABASE "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture
async def setup(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("TRUNCATE audit_log, instructor RESTART IDENTITY CASCADE"))
            owner = Instructor(
                email="saved-group@example.test",
                display_name="Saved group control",
                password_hash=hash_password("saved-group-test-password"),
            )
            other = Instructor(email="other@example.test", display_name="Other")
            session.add_all([owner, other])
            await session.flush()
            program = await session.scalar(select(Program).order_by(Program.id).limit(1))
            initial = Group(instructor_id=owner.id, name="Initial", name_normalized="initial")
            saved = Group(
                instructor_id=owner.id,
                name="Practice team",
                name_normalized="practice team",
                proposal_name_normalized="practice team",
                program_id=program.id,
                title="Saved academic title",
            )
            foreign = Group(instructor_id=other.id, name="Foreign", name_normalized="foreign")
            session.add_all([initial, saved, foreign])
            await session.flush()
            session.add_all(
                [
                    GroupMember(group_id=saved.id, name="Dela Cruz, Maria"),
                    GroupMember(group_id=saved.id, name="Santos, Ana"),
                ]
            )
            manuscript = Manuscript(
                instructor_id=owner.id,
                group_id=initial.id,
                group_label=initial.name,
                file_ref="unused.docx",
                origin=ManuscriptOrigin.check_upload,
            )
            session.add(manuscript)
            await session.commit()
            ids = owner.id, other.id, manuscript.id, initial.id, saved.id, foreign.id
        yield factory, ids
    finally:
        await engine.dispose()


async def request_for(factory, ids, *, expected="initial"):
    owner, _, _, initial, saved, _ = ids
    async with factory() as session:
        snapshot = await get_saved_group(session, saved, owner)
    return SelectGroupRequest(
        saved_group_id=saved,
        fingerprint=snapshot.fingerprint,
        expected_group_id=initial if expected == "initial" else expected,
    )


async def test_explicit_saved_identity_assigns_without_mutating_facts_and_replay_is_noop(setup):
    factory, ids = setup
    owner, _, manuscript_id, initial, saved, _ = ids
    body = await request_for(factory, ids)
    async with factory() as session:
        before = await get_saved_group(session, saved, owner)
        response = await select_saved_group(session, manuscript_id, owner, body)
        assert response.outcome == "selected" and response.receipt.effective == before
    async with factory() as session:
        replay = await select_saved_group(session, manuscript_id, owner, body)
        assert replay.outcome == "unchanged" and replay.receipt == response.receipt
        current = await get_manuscript_group(session, manuscript_id, owner)
        assert current.confirmation == "recorded" and current.group.id == saved
        assert await get_saved_group(session, saved, owner) == before
        assert await session.scalar(select(func.count()).select_from(Group)) == 3
        assert await session.scalar(select(func.count()).select_from(GroupMember)) == 2
        audits = list((await session.scalars(select(AuditLog))).all())
        assert len(audits) == 1
        assert audits[0].event_type == "manuscript_group_selected"
        assert audits[0].payload["before"] == {"group_id": initial, "group_label": "Initial"}
        assert audits[0].payload["selection"] == response.receipt.model_dump(mode="json")


async def test_unassigned_and_already_current_without_receipt_record_explicit_confirmation(setup):
    factory, ids = setup
    owner, _, manuscript_id, _, saved, _ = ids
    for expected in (None, saved):
        async with factory() as session:
            manuscript = await session.get(Manuscript, manuscript_id)
            manuscript.group_id = expected
            manuscript.group_label = "Ungrouped" if expected is None else "Practice team"
            manuscript.group_confirmation = None
            await session.commit()
        body = await request_for(factory, ids, expected=expected)
        async with factory() as session:
            result = await select_saved_group(session, manuscript_id, owner, body)
            assert result.outcome == "selected"
            assert (await get_manuscript_group(session, manuscript_id, owner)).confirmation == (
                "recorded"
            )


async def test_foreign_missing_wrong_origin_and_corrupt_current_group_fail_closed(setup):
    factory, ids = setup
    owner, other, manuscript_id, _, saved, foreign = ids
    body = await request_for(factory, ids)
    for requested_id, actor in ((saved, other), (foreign, owner), (999999, owner)):
        async with factory() as session:
            with pytest.raises(NotFoundError):
                await get_saved_group(session, requested_id, actor)
            with pytest.raises(NotFoundError):
                await select_saved_group(
                    session,
                    manuscript_id,
                    actor,
                    body.model_copy(update={"saved_group_id": requested_id}),
                )
    async with factory() as session:
        manuscript = await session.get(Manuscript, manuscript_id)
        manuscript.group_id = foreign
        await session.commit()
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await select_saved_group(session, manuscript_id, owner, body)
        manuscript = await session.get(Manuscript, manuscript_id)
        manuscript.origin = ManuscriptOrigin.library_submission
        await session.commit()
    async with factory() as session:
        with pytest.raises(NotFoundError):
            await select_saved_group(session, manuscript_id, owner, body)
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_stale_details_and_expected_assignment_never_overwrite_or_certify_replay(setup):
    factory, ids = setup
    owner, _, manuscript_id, initial, saved, _ = ids
    body = await request_for(factory, ids)
    async with factory() as session:
        with pytest.raises(ConflictError, match="assignment changed"):
            await select_saved_group(
                session, manuscript_id, owner, body.model_copy(update={"expected_group_id": None})
            )
    async with factory() as session:
        await select_saved_group(session, manuscript_id, owner, body)
    async with factory() as session:
        group = await session.get(Group, saved)
        group.title = "Later stored title"
        await session.commit()
    async with factory() as session:
        with pytest.raises(ConflictError, match="details changed"):
            await select_saved_group(session, manuscript_id, owner, body)
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert (await session.get(Manuscript, manuscript_id)).group_id != initial
        # Historical confirmation remains valid; it does not certify current facts.
        assert (
            await get_manuscript_group(session, manuscript_id, owner)
        ).confirmation == "recorded"


async def test_same_request_race_records_once_and_different_choice_conflicts(setup):
    factory, ids = setup
    owner, _, manuscript_id, initial, saved, _ = ids
    body = await request_for(factory, ids)

    async def save(request):
        async with factory() as session:
            try:
                return (await select_saved_group(session, manuscript_id, owner, request)).outcome
            except ConflictError:
                return "conflict"

    results = await asyncio.gather(*(save(body) for _ in range(5)))
    assert results.count("selected") == 1 and results.count("unchanged") == 4
    async with factory() as session:
        manuscript = await session.get(Manuscript, manuscript_id)
        manuscript.group_id, manuscript.group_label, manuscript.group_confirmation = (
            initial,
            "Initial",
            None,
        )
        competitor = Group(
            instructor_id=owner, name="Other saved team", name_normalized="other saved team"
        )
        session.add(competitor)
        await session.commit()
        competitor_snapshot = await get_saved_group(session, competitor.id, owner)
    second = SelectGroupRequest(
        saved_group_id=competitor.id,
        fingerprint=competitor_snapshot.fingerprint,
        expected_group_id=initial,
    )
    results = await asyncio.gather(save(body), save(second))
    assert results.count("selected") == results.count("conflict") == 1


async def test_waiting_selection_rechecks_parent_locked_member_union(setup):
    factory, ids = setup
    owner, _, manuscript_id, _, saved, _ = ids
    body = await request_for(factory, ids)
    async with factory() as writer:
        await writer.scalar(select(Group).where(Group.id == saved).with_for_update())
        writer.add(GroupMember(group_id=saved, name="Later member"))
        await writer.flush()

        async def waiting():
            async with factory() as reader:
                with pytest.raises(ConflictError, match="details changed"):
                    await select_saved_group(reader, manuscript_id, owner, body)

        pending = asyncio.create_task(waiting())
        await asyncio.sleep(0.05)
        assert not pending.done()
        await writer.commit()
        await asyncio.wait_for(pending, timeout=5)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_selection_failure_rolls_back_assignment_and_audit_together(setup, monkeypatch):
    import app.groups.selection as selection

    factory, ids = setup
    owner, _, manuscript_id, initial, _, _ = ids
    body = await request_for(factory, ids)

    async def fail(*args, **kwargs):
        raise RuntimeError("Deliberate audit control")

    monkeypatch.setattr(selection, "write_audit_event", fail)
    async with factory() as session:
        with pytest.raises(RuntimeError, match="Deliberate audit control"):
            await select_saved_group(session, manuscript_id, owner, body)
        await session.rollback()
    async with factory() as session:
        manuscript = await session.get(Manuscript, manuscript_id)
        assert manuscript.group_id == initial and manuscript.group_confirmation is None
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_v1_proposal_remains_separate_and_can_follow_explicit_selection(setup):
    factory, ids = setup
    owner, _, manuscript_id, _, saved, _ = ids
    body = await request_for(factory, ids)
    async with factory() as session:
        await select_saved_group(session, manuscript_id, owner, body)
    async with factory() as session:
        group, outcome = await confirm_manuscript_group(
            session,
            manuscript_id,
            owner,
            "Another memberless team",
            [],
            None,
            expected_group_id=saved,
            expected_provided=True,
        )
        assert outcome == "created" and group.id != saved
        manuscript = await session.get(Manuscript, manuscript_id)
        assert manuscript.group_confirmation["version"] == 1
        assert (
            await get_manuscript_group(session, manuscript_id, owner)
        ).confirmation == "recorded"


async def test_normal_cookie_routes_validate_identity_and_owner_without_an_auth_override(
    setup, monkeypatch
):
    factory, ids = setup
    owner, _, manuscript_id, _, saved, foreign = ids
    body = await request_for(factory, ids)
    import app.auth.service as auth_service

    monkeypatch.setattr(auth_service, "_rate_limiter", None)
    get_settings.cache_clear()
    app = FastAPI()

    async def session_dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_dependency
    app.include_router(auth_router)
    app.include_router(groups_router)
    app.include_router(ingest_router)

    @app.exception_handler(VeridicalError)
    async def error_handler(request: Request, exc: VeridicalError):
        return JSONResponse(
            status_code=HTTP_STATUS[exc.code],
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        path = f"/manuscripts/{manuscript_id}/group-selection"
        assert (await client.post(path, json=body.model_dump())).status_code == 401
        login = await client.post(
            "/auth/login",
            json={"email": "saved-group@example.test", "password": "saved-group-test-password"},
        )
        assert login.status_code == 200, login.text
        snapshot = await client.get(f"/groups/{saved}")
        assert snapshot.status_code == 200 and snapshot.json()["member_names"] == [
            "Dela Cruz, Maria",
            "Santos, Ana",
        ]
        assert (await client.get(f"/groups/{foreign}")).status_code == 404
        assert (await client.get("/groups/collision-check?short_name=Missing")).status_code == 200
        malformed = deepcopy(body.model_dump())
        del malformed["expected_group_id"]
        assert (await client.post(path, json=malformed)).status_code == 422
        assert (
            await client.post(path, json={**body.model_dump(), "title": "Override"})
        ).status_code == 422
        denied = {**body.model_dump(), "saved_group_id": foreign}
        assert (await client.post(path, json=denied)).status_code == 404
        stale_details = await client.post(
            path, json={**body.model_dump(), "fingerprint": "0" * 64}
        )
        assert stale_details.status_code == 409
        assert stale_details.json()["error"]["code"] == "saved_group_changed"
        stale_assignment = await client.post(
            path, json={**body.model_dump(), "expected_group_id": None}
        )
        assert stale_assignment.status_code == 409
        assert stale_assignment.json()["error"]["code"] == "group_assignment_changed"
        saved_response = await client.post(path, json=body.model_dump())
        assert saved_response.status_code == 200, saved_response.text
        assert saved_response.json()["receipt"]["instructor_id"] == owner
        assert (await client.get(f"/manuscripts/{manuscript_id}/group")).json()["confirmation"] == (
            "recorded"
        )
        assert (await client.post("/auth/logout")).status_code == 200
        assert (await client.get(f"/groups/{saved}")).status_code == 401
