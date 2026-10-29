"""V-090: a background upload can start its check on the server, and the
upload tray can list recent uploads again after a page reload."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.config import get_settings
from tests.test_large_upload_transport import (  # noqa: F401 -- shared fixtures
    _attempt,
    _noise_pdf,
    _process,
    _seed_instructor,
    live,
    scratch_url,
    wired,
)


def _rubric(stack, *, active=True, instructor_id=None):
    from app.models.rubric import Rubric

    async def create():
        stack.db._engine = None
        try:
            async with stack.db.get_session_factory()() as session:
                rubric = Rubric(
                    instructor_id=instructor_id or stack.instructor_id,
                    title="Official rubric",
                    is_active=active,
                )
                session.add(rubric)
                await session.commit()
                return rubric.id
        finally:
            await stack.db.get_engine().dispose()
            stack.db._engine = None

    return asyncio.run(create())


def _run_rows(stack, manuscript_id):
    from app.models.run import CheckRun

    async def load():
        stack.db._engine = None
        try:
            async with stack.db.get_session_factory()() as session:
                return list(
                    await session.scalars(
                        select(CheckRun).where(CheckRun.manuscript_id == manuscript_id)
                    )
                )
        finally:
            await stack.db.get_engine().dispose()
            stack.db._engine = None

    return asyncio.run(load())


def _set_rubric_active(stack, rubric_id, active):
    from app.models.rubric import Rubric

    async def change():
        stack.db._engine = None
        try:
            async with stack.db.get_session_factory()() as session:
                await session.execute(
                    update(Rubric).where(Rubric.id == rubric_id).values(is_active=active)
                )
                await session.commit()
        finally:
            await stack.db.get_engine().dispose()
            stack.db._engine = None

    asyncio.run(change())


def _begin(tc, stack, data, filename="thesis.pdf", **extra):
    response = tc.post(
        "/manuscripts/uploads",
        json={"attempt_id": str(uuid4()), "filename": filename, "size": len(data), **extra},
        headers={
            "X-Veridical-Expected-Instructor": str(stack.instructor_id),
            "X-Veridical-Upload-Transports": "stream",
        },
    )
    return response


def _send(tc, attempt_id, data):
    response = tc.put(f"/manuscripts/uploads/{attempt_id}/body", content=data)
    assert response.status_code == 200, response.text
    return response.json()


@live
def test_the_requested_check_starts_when_the_manuscript_is_ready(wired):  # noqa: F811 -- imported pytest fixtures
    rubric_id = _rubric(wired)
    tc = wired.client("relay")
    data = _noise_pdf(6)
    begun = _begin(tc, wired, data, start_check={"rubric_id": rubric_id})
    assert begun.status_code == 200, begun.text
    state = begun.json()
    assert state["check_run_id"] is None and state["progress"] is None
    assert state["filename"] == "thesis.pdf" and state["size"] == len(data)
    _send(tc, state["attempt_id"], data)
    tc.__exit__(None, None, None)

    assert _process(wired) is True
    attempt, manuscript = _attempt(wired, state["attempt_id"])
    assert attempt.status == "done", attempt.error
    runs = _run_rows(wired, manuscript.id)
    assert len(runs) == 1 and runs[0].rubric_id == rubric_id
    assert attempt.check_run_id == runs[0].id and attempt.check_start_error is None


@live
def test_a_rubric_that_cannot_run_is_refused_before_any_byte_moves(wired):  # noqa: F811 -- imported pytest fixtures
    inactive = _rubric(wired, active=False)
    other_instructor = _seed_instructor(wired.scratch_url, f"other-{uuid4().hex[:8]}@tip.edu.ph")
    foreign = _rubric(wired, instructor_id=other_instructor)
    tc = wired.client("relay")
    data = b"%PDF-1.7 small"
    assert _begin(tc, wired, data, start_check={"rubric_id": inactive}).status_code == 409
    assert _begin(tc, wired, data, start_check={"rubric_id": foreign}).status_code == 404
    assert _begin(tc, wired, data, start_check={"rubric_id": "1"}).status_code == 422
    assert wired.store.calls == []


@live
def test_resuming_an_attempt_skips_its_check_request(wired):  # noqa: F811 -- imported pytest fixtures
    # The check request (rate limit and rubric refusal) belongs to the
    # attempt's creation; resuming the attempt leaves it as it was.
    rubric_id = _rubric(wired)
    tc = wired.client("relay")
    data = b"%PDF-1.7 small"
    attempt_id = str(uuid4())
    body = {
        "attempt_id": attempt_id,
        "filename": "thesis.pdf",
        "size": len(data),
        "start_check": {"rubric_id": rubric_id},
    }
    headers = {"X-Veridical-Expected-Instructor": str(wired.instructor_id)}
    first = tc.post("/manuscripts/uploads", json=body, headers=headers)
    assert first.status_code == 200, first.text
    # The attempt keeps the rubric it began with; a resume changes nothing.
    _set_rubric_active(wired, rubric_id, False)
    resumed = tc.post("/manuscripts/uploads", json=body, headers=headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["attempt_id"] == attempt_id


@live
def test_a_check_that_cannot_start_keeps_the_upload_and_says_why(wired):  # noqa: F811 -- imported pytest fixtures
    rubric_id = _rubric(wired)
    tc = wired.client("relay")
    data = _noise_pdf(6)
    state = _begin(tc, wired, data, start_check={"rubric_id": rubric_id}).json()
    _send(tc, state["attempt_id"], data)
    tc.__exit__(None, None, None)
    _set_rubric_active(wired, rubric_id, False)

    assert _process(wired) is True
    attempt, manuscript = _attempt(wired, state["attempt_id"])
    assert attempt.status == "done", attempt.error
    assert attempt.check_run_id is None
    assert "active rubric version" in attempt.check_start_error
    assert _run_rows(wired, manuscript.id) == []


@live
def test_the_tray_lists_active_and_recent_uploads_for_this_instructor_only(wired):  # noqa: F811 -- imported pytest fixtures
    from app.models.upload_attempt import UploadAttempt

    tc = wired.client("relay")
    data = b"%PDF-1.7 small"
    # Different files: choosing the same file again would replace the attempt.
    pending = _begin(tc, wired, data, filename="chapter-1.pdf").json()["attempt_id"]
    finished = _begin(tc, wired, data, filename="chapter-2.pdf").json()["attempt_id"]
    stale = _begin(tc, wired, data, filename="chapter-3.pdf").json()["attempt_id"]
    other = _seed_instructor(wired.scratch_url, f"tray-{uuid4().hex[:8]}@tip.edu.ph")
    foreign = str(uuid4())

    async def arrange():
        wired.db._engine = None
        try:
            async with wired.db.get_session_factory()() as session:
                window = get_settings().direct_upload_tray_window_seconds
                await session.execute(
                    update(UploadAttempt)
                    .where(UploadAttempt.id == finished)
                    .values(status="failed", error="Document processing could not finish.")
                )
                await session.execute(
                    update(UploadAttempt)
                    .where(UploadAttempt.id == stale)
                    .values(
                        status="done",
                        created_at=datetime.now(UTC) - timedelta(seconds=window + 60),
                    )
                )
                session.add(
                    UploadAttempt(
                        id=foreign,
                        instructor_id=other,
                        filename="someone-else.pdf",
                        source_bytes=10,
                        staging_key=f"upload_staging/{uuid4()}",
                        # Finished, so no later test's worker run picks it up.
                        status="failed",
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
                await session.commit()
        finally:
            await wired.db.get_engine().dispose()
            wired.db._engine = None

    asyncio.run(arrange())
    listed = tc.get("/manuscripts/uploads?active=true")
    assert listed.status_code == 200, listed.text
    assert [item["attempt_id"] for item in listed.json()] == [finished, pending]
    assert [item["status"] for item in listed.json()] == ["failed", "pending"]
    assert listed.json()[0]["error"] == "Document processing could not finish."


def _delete_rubric(stack, rubric_id):
    from sqlalchemy import delete

    from app.models.rubric import Rubric

    async def remove():
        stack.db._engine = None
        try:
            async with stack.db.get_session_factory()() as session:
                await session.execute(delete(Rubric).where(Rubric.id == rubric_id))
                await session.commit()
        finally:
            await stack.db.get_engine().dispose()
            stack.db._engine = None

    asyncio.run(remove())


def _prepared_upload(wired, rubric_id):  # noqa: F811 -- imported pytest fixtures
    tc = wired.client("relay")
    data = _noise_pdf(6)
    state = _begin(tc, wired, data, start_check={"rubric_id": rubric_id}).json()
    assert state["check_requested"] is True
    _send(tc, state["attempt_id"], data)
    tc.__exit__(None, None, None)
    return state["attempt_id"]


@live
def test_a_deleted_rubric_is_recorded_as_the_reason_not_dropped(wired):  # noqa: F811 -- imported pytest fixtures
    import app.ingest.upload_worker as upload_worker

    rubric_id = _rubric(wired)
    attempt_id = _prepared_upload(wired, rubric_id)
    _delete_rubric(wired, rubric_id)
    assert _process(wired) is True
    attempt, manuscript = _attempt(wired, attempt_id)
    assert attempt.status == "done", attempt.error
    assert attempt.start_rubric_id == rubric_id
    assert attempt.check_run_id is None
    assert attempt.check_start_error == upload_worker.RUBRIC_GONE


@live
def test_an_unexpected_error_after_the_check_committed_still_links_it(wired, monkeypatch):  # noqa: F811 -- imported pytest fixtures
    import app.ingest.upload_worker as upload_worker

    real = upload_worker.create_check_run

    async def commits_then_fails(*args, **kwargs):
        await real(*args, **kwargs)
        raise RuntimeError("connection dropped after commit")

    monkeypatch.setattr(upload_worker, "create_check_run", commits_then_fails)
    rubric_id = _rubric(wired)
    attempt_id = _prepared_upload(wired, rubric_id)
    assert _process(wired) is True
    attempt, manuscript = _attempt(wired, attempt_id)
    runs = _run_rows(wired, manuscript.id)
    assert attempt.status == "done", attempt.error
    assert len(runs) == 1 and attempt.check_run_id == runs[0].id
    assert attempt.check_start_error is None


@live
def test_an_unexpected_error_before_the_check_keeps_the_upload_done(wired, monkeypatch):  # noqa: F811 -- imported pytest fixtures
    import app.ingest.upload_worker as upload_worker

    async def fails(*args, **kwargs):
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(upload_worker, "create_check_run", fails)
    rubric_id = _rubric(wired)
    attempt_id = _prepared_upload(wired, rubric_id)
    assert _process(wired) is True
    attempt, manuscript = _attempt(wired, attempt_id)
    assert attempt.status == "done", attempt.error
    assert manuscript.ingest_status.value == "done"
    assert attempt.check_run_id is None
    assert attempt.check_start_error == upload_worker.CHECK_NOT_STARTED


@live
def test_a_started_check_wakes_the_scheduler(wired):  # noqa: F811 -- imported pytest fixtures
    from app.ingest.upload_worker import process_next_upload

    rubric_id = _rubric(wired)
    _prepared_upload(wired, rubric_id)
    woken = []

    async def run():
        wired.db._engine = None
        try:
            return await process_next_upload(get_settings(), lambda: woken.append(True))
        finally:
            await wired.db.get_engine().dispose()
            wired.db._engine = None

    assert asyncio.run(run()) is True
    assert woken == [True]
