"""BUG-546: when an upload attempt ends as failed, its status says whether a
record was kept. `manuscript_id` is the failed record's id when one was saved
(the upload dialog links it) and null when nothing was saved, so the dialog
never has to call a known outcome "unconfirmed"."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.ingest import compute_client
from app.models.enums import IngestStatus
from app.models.upload_attempt import UploadAttempt
from tests.test_fragment_guard import _pieces_pdf, _upload
from tests.test_large_upload_transport import (  # noqa: F401
    _attempt,
    _process,
    live,
    scratch_url,
    wired,
)


def _status(env, attempt_id):
    response = env.client("relay").get(f"/manuscripts/uploads/{attempt_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _expire_processing(env, attempt_id):
    """What a restart leaves behind: an attempt still marked processing whose
    worker is gone and whose lease has run out."""

    async def run():
        env.db._engine = None
        try:
            async with env.db.get_session_factory()() as session:
                await session.execute(
                    update(UploadAttempt)
                    .where(UploadAttempt.id == attempt_id)
                    .values(
                        status="processing", lease_until=datetime.now(UTC) - timedelta(minutes=1)
                    )
                )
                await session.commit()
        finally:
            await env.db.get_engine().dispose()
            env.db._engine = None

    asyncio.run(run())


@live
def test_a_refused_upload_names_the_failed_record_it_kept(wired, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PREPARATION_MAX_IMAGE_FRAGMENTS", "10")
    tc = wired.client("relay")
    attempt_id = _upload(tc, _pieces_pdf(30))
    tc.__exit__(None, None, None)
    assert _process(wired) is True

    status = _status(wired, attempt_id)
    attempt, manuscript = _attempt(wired, attempt_id)

    assert status["status"] == "failed"
    assert status["error"].startswith("This PDF contains 30 small image pieces")
    assert status["manuscript_id"] is not None
    assert status["manuscript_id"] == attempt.manuscript_id == manuscript.id
    assert manuscript.ingest_status == IngestStatus.failed


@live
def test_an_interrupted_upload_names_the_failed_record_it_kept(wired, monkeypatch):  # noqa: F811
    tc = wired.client("relay")
    attempt_id = _upload(tc, _pieces_pdf(1))
    tc.__exit__(None, None, None)

    def killed(*_args, **_kwargs):
        # Escapes the worker's own error handling, as a killed process would.
        raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr(compute_client.httpx, "post", killed)
        with pytest.raises(asyncio.CancelledError):
            _process(wired)
    attempt, manuscript = _attempt(wired, attempt_id)
    assert attempt.status == "processing" and manuscript is not None

    _expire_processing(wired, attempt_id)
    _process(wired)

    status = _status(wired, attempt_id)
    attempt, manuscript = _attempt(wired, attempt_id)
    assert status["status"] == "failed"
    assert status["error"].startswith("Document processing was interrupted.")
    assert status["manuscript_id"] == manuscript.id
    assert manuscript.ingest_status == IngestStatus.failed


@live
def test_an_interrupted_upload_that_saved_nothing_says_so(wired):  # noqa: F811
    tc = wired.client("relay")
    attempt_id = _upload(tc, _pieces_pdf(1))
    tc.__exit__(None, None, None)

    _expire_processing(wired, attempt_id)
    _process(wired)

    status = _status(wired, attempt_id)
    attempt, manuscript = _attempt(wired, attempt_id)
    assert status["status"] == "failed"
    assert status["manuscript_id"] is None
    assert attempt.manuscript_id is None and manuscript is None
