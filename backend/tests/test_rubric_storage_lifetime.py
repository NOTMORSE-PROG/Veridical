"""F2: durable publication follows actual work through failures and cancellation."""

import asyncio
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.ingest.admission import PreparationAdmission
from app.rubric import service


class Session:
    def __init__(self, fail_commit=False):
        self.commits = 0
        self.deleted = False
        self.row = None
        self.fail_commit = fail_commit

    def add(self, row):
        self.row = row
        row.id = 71
        row.criteria = []

    def add_all(self, rows):
        pass

    async def commit(self):
        self.commits += 1
        if self.fail_commit and self.commits == 2:
            raise OSError("Controlled commit failure")

    async def refresh(self, *args, **kwargs):
        pass

    async def rollback(self):
        pass

    async def delete(self, row):
        self.deleted = True


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    admission = PreparationAdmission(settings)
    objects = {}
    entered, release = threading.Event(), threading.Event()
    state = SimpleNamespace(block=False, active=False, closed=0, fail_ack=False, fail_extract=False)

    class Store:
        def put_file(self, path, key):
            state.active = True
            entered.set()
            try:
                if state.block:
                    assert release.wait(5)
                objects[key] = path.read_bytes()
                if state.fail_ack:
                    raise OSError("Controlled acknowledgement loss")
            finally:
                state.active = False

        def delete(self, key):
            objects.pop(key, None)

        def close(self):
            assert not state.active
            state.closed += 1

    @contextmanager
    def scope(_settings):
        with admission.acquire() as lease:
            yield lease

    def extract(*args):
        if state.fail_extract:
            raise ValueError("Controlled malformed document")
        return SimpleNamespace(blocks=[])

    monkeypatch.setattr(service, "preparation_scope", scope)
    monkeypatch.setattr(service, "get_storage", lambda _: Store())
    monkeypatch.setattr(service, "select_extractor", lambda _: extract)
    monkeypatch.setattr(service, "raw_text_for_decomposition", lambda _: "Control source")
    monkeypatch.setattr(
        service,
        "attempt_decomposition",
        AsyncMock(return_value=SimpleNamespace(criteria=[], needs_review=True, issues=["control"])),
    )

    async def chunks():
        yield (Path(__file__).parent / "fixtures/ingest/native.docx").read_bytes()

    async def create(session):
        return await service.create_rubric_from_upload(
            session, chunks(), "control.docx", "Control", object(), settings, instructor_id=1
        )

    yield settings, admission, objects, state, entered, release, create
    release.set()
    admission.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extract", "ack", "commit"])
async def test_failed_format_publication_has_no_unowned_durable_original(runtime, failure):
    settings, admission, objects, state, _, _, create = runtime
    state.fail_extract, state.fail_ack = failure == "extract", failure == "ack"
    session = Session(fail_commit=failure == "commit")
    with pytest.raises((ValueError, OSError)):
        await create(session)
    assert session.deleted and not objects
    assert not list(settings.data_dir.rglob("*.docx"))
    assert admission.active_owners == 0


@pytest.mark.asyncio
async def test_cancelled_format_waits_for_publication_and_keeps_recoverable_draft(runtime):
    _, admission, objects, state, entered, release, create = runtime
    state.block = True
    session = Session()
    task = asyncio.create_task(create(session))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and state.closed == 0 and admission.active_owners == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not session.deleted and session.row.source_file
    assert objects and state.closed == 1 and admission.active_owners == 0
