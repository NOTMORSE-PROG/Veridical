"""V-042 (screen 4t): GET /archive + DELETE /archive/{id} -- list the F7
embedding archive per manuscript and let an instructor purge one. Needs a
live Postgres (same convention as test_ingest_manuscripts_live.py).
"""

import os

import pytest
from fastapi.testclient import TestClient

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.config import get_settings

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_archiveapitest"


def _unit_vector(dim: int) -> list[float]:
    """A real unit vector (not the all-zero placeholder the other archive
    fixtures use) -- needed wherever a test relies on pgvector's cosine
    distance actually resolving to a real similarity instead of NaN
    (`<=>` on two zero vectors is NaN, which never clears a similarity
    threshold either way and would silently pass a test for the wrong
    reason)."""
    return [1.0] + [0.0] * (dim - 1)


@pytest.fixture(scope="module")
def api_scratch_url():
    import asyncio

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


@pytest.fixture()
def seeded(client, api_scratch_url, tmp_path):
    """Seeds two instructors, one manuscript each, real files on disk, and
    a whole-doc + chapter embedding archive for the first instructor's
    manuscript -- everything `purge_manuscript` must touch or must NOT
    touch. Own engine, not `app.db.get_engine()` (same Windows/asyncpg
    loop-binding reason `test_auth_router.py`'s `seeded` fixture documents).
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.ingest.service import raw_store_path
    from app.models.enums import CheckRunStatus, IngestStatus
    from app.models.instructor import Instructor
    from app.models.manuscript import (
        Manuscript,
        ManuscriptArchive,
        ManuscriptChapterArchive,
        ManuscriptPassageArchive,
    )
    from app.models.rubric import Rubric
    from app.models.run import CheckRun

    settings = get_settings()
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ids: dict[str, int] = {}

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "TRUNCATE manuscript_passage_archive, manuscript_chapter_archive, "
                        "manuscript_archive, check_run, manuscript, rubric, session, "
                        "instructor RESTART IDENTITY CASCADE"
                    )
                )
                owner = Instructor(
                    email="prof@tip.edu.ph",
                    display_name="Prof",
                    password_hash=hash_password("s3cret!"),
                )
                stranger = Instructor(
                    email="stranger@tip.edu.ph",
                    display_name="Stranger",
                    password_hash=hash_password("other!"),
                )
                session.add_all([owner, stranger])
                await session.commit()

                rubric = Rubric(instructor_id=owner.id, title="Format", source_file="r.pdf")
                session.add(rubric)
                await session.commit()

                mine = Manuscript(
                    instructor_id=owner.id,
                    group_label="G1",
                    file_ref="",
                    ingest_status=IngestStatus.done,
                )
                theirs = Manuscript(
                    instructor_id=stranger.id,
                    group_label="G2",
                    file_ref="",
                    ingest_status=IngestStatus.done,
                )
                session.add_all([mine, theirs])
                await session.commit()

                upload_path = upload_dir / f"{mine.id}.pdf"
                from tests.test_ingest_fixtures import FIXTURE_DIR

                upload_path.write_bytes((FIXTURE_DIR / "native.pdf").read_bytes())
                raw_path = raw_store_path(settings, mine.id)
                raw_path.write_text("{}")
                mine.file_ref = str(upload_path)
                session.add(
                    CheckRun(manuscript_id=mine.id, rubric_id=rubric.id, status=CheckRunStatus.done)
                )
                session.add(
                    ManuscriptArchive(
                        manuscript_id=mine.id,
                        embedding=[0.0] * settings.embedding_dim,
                        model_id="test-model",
                    )
                )
                session.add(
                    ManuscriptChapterArchive(
                        manuscript_id=mine.id,
                        chapter_index=0,
                        title="Chapter 1",
                        page=1,
                        embedding=[0.0] * settings.embedding_dim,
                        model_id="test-model",
                    )
                )
                # BUG-123: a real passage row with real body text -- what
                # purge must also delete, and what a purged-manuscript query
                # must never surface.
                session.add(
                    ManuscriptPassageArchive(
                        manuscript_id=mine.id,
                        passage_index=0,
                        chapter_index=0,
                        page=1,
                        char_start=0,
                        char_end=40,
                        text="Real body text that must not outlive purge.",
                        context_text="Real body text that must not outlive purge.",
                        embedding=_unit_vector(settings.embedding_dim),
                        # Unlike the two archive rows above (which are never
                        # run through `query_similar_passages` by any test
                        # here), this row's `model_id` MUST match the real
                        # `settings.embedding_model_id` -- `query.py` filters
                        # on it, and a mismatched literal would silently
                        # make BUG-123's regression tests pass for the wrong
                        # reason (the row simply never becomes a candidate).
                        model_id=settings.embedding_model_id,
                    )
                )
                await session.commit()

                ids["mine"] = mine.id
                ids["theirs"] = theirs.id
                ids["owner_id"] = owner.id
                ids["upload_path"] = str(upload_path)
                ids["raw_path"] = str(raw_path)
        finally:
            await engine.dispose()

    asyncio.run(seed())
    client.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
    return client, ids


def test_lists_archive_state_including_embedding_presence(seeded):
    client, ids = seeded
    resp = client.get("/archive")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1  # only the caller's own manuscript, never a stranger's
    item = body["items"][0]
    assert item["manuscript_id"] == ids["mine"]
    assert item["has_archive"] is True
    assert item["purged_at"] is None
    assert item["latest_check_run_status"] == "done"
    assert item["ingest_status"] == "done"


def test_ingest_failure_reads_as_failed_here_not_not_checked_yet(seeded, api_scratch_url):
    # V-071 (BUG-058): this endpoint used to omit ingest_status entirely, so
    # a manuscript whose ingestion itself failed was indistinguishable here
    # from one that simply hadn't been run yet, while the dashboard's
    # GET /manuscripts (same underlying column) correctly read "failed".
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.enums import IngestFailureReason, IngestStatus
    from app.models.manuscript import Manuscript
    from tests.test_schema import sqlalchemy_url

    client, ids = seeded

    async def seed_failed(owner_id: int) -> int:
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                failed = Manuscript(
                    instructor_id=owner_id,
                    group_label="G-failed",
                    file_ref="",
                    ingest_status=IngestStatus.failed,
                    ingest_failure_reason=IngestFailureReason.unreadable_format,
                )
                session.add(failed)
                await session.commit()
                return failed.id
        finally:
            await engine.dispose()

    failed_id = asyncio.run(seed_failed(ids["owner_id"]))

    resp = client.get("/archive")
    assert resp.status_code == 200
    items = {item["manuscript_id"]: item for item in resp.json()["items"]}
    assert items[failed_id]["ingest_status"] == "failed"
    assert items[failed_id]["latest_check_run_status"] is None


def test_purge_deletes_embeddings_and_files_but_keeps_the_manuscript_and_run(seeded):
    client, ids = seeded
    from pathlib import Path

    resp = client.delete(f"/archive/{ids['mine']}")
    assert resp.status_code == 200
    assert resp.json()["manuscript_id"] == ids["mine"]
    assert resp.json()["purged_at"] is not None

    assert not Path(ids["upload_path"]).exists()
    assert not Path(ids["raw_path"]).exists()

    after = client.get("/archive")
    item = after.json()["items"][0]
    assert item["has_archive"] is False
    assert item["purged_at"] is not None
    # The manuscript row and its check-run history are NOT gone -- only
    # the archive/files were purged, not the record of the manuscript.
    assert after.json()["total"] == 1


def test_purge_deletes_the_passage_level_archive_too(seeded, api_scratch_url):
    """BUG-123 part (a): `purge_manuscript` used to delete only
    `ManuscriptArchive`/`ManuscriptChapterArchive`, never
    `ManuscriptPassageArchive` (V-072, added after purge shipped) -- so a
    purged manuscript's real passage text and context kept existing in the
    DB. Confirm the row seeded in `seeded` is actually gone after purge,
    not just that the endpoint returned 200."""
    import asyncio

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.manuscript import ManuscriptPassageArchive

    client, ids = seeded

    async def count_passages() -> int:
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                return (
                    await session.scalar(
                        select(func.count())
                        .select_from(ManuscriptPassageArchive)
                        .where(ManuscriptPassageArchive.manuscript_id == ids["mine"])
                    )
                ) or 0
        finally:
            await engine.dispose()

    assert asyncio.run(count_passages()) == 1  # seeded fixture put exactly one row there

    resp = client.delete(f"/archive/{ids['mine']}")
    assert resp.status_code == 200

    assert asyncio.run(count_passages()) == 0


def test_purged_manuscripts_passages_are_never_returned_by_similarity_query(
    seeded, api_scratch_url
):
    """BUG-123 part (b), belt-and-suspenders: even if a passage row somehow
    survives purge (a future missed call site), `query_similar_passages`
    must not return it. Sets `purged_at` directly on the archived
    manuscript WITHOUT deleting its passage row, then queries a fresh
    manuscript's passage against the archive -- proves the query-level
    `Manuscript.purged_at IS NULL` filter works independently of the
    deletion in `purge_manuscript`."""
    import asyncio
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.checks.reuse.embed import PassageEmbedding
    from app.checks.reuse.query import query_similar_passages
    from app.config import get_settings
    from app.db import sqlalchemy_url
    from app.models.manuscript import Manuscript

    client, ids = seeded
    settings = get_settings()

    async def mark_purged_without_deleting_archive() -> None:
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                manuscript = await session.get(Manuscript, ids["mine"])
                manuscript.purged_at = datetime.now(UTC)
                await session.commit()
        finally:
            await engine.dispose()

    async def run_query() -> list:
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                querying_passage = PassageEmbedding(
                    passage_index=0,
                    chapter_index=0,
                    anchor_kind="page",
                    page=1,
                    paragraph=None,
                    char_start=0,
                    char_end=40,
                    text="Some other manuscript's own passage text.",
                    context_text="Some other manuscript's own passage text.",
                    is_reference_list=False,
                    is_block_quote=False,
                    # Identical to the archived passage's own vector
                    # (`_unit_vector`) -- an exact 1.0-similarity match, so
                    # without the purged_at filter this WOULD be returned.
                    embedding=_unit_vector(settings.embedding_dim),
                )
                result = await query_similar_passages(
                    session,
                    ids["theirs"],  # a DIFFERENT manuscript doing the querying
                    [querying_passage],
                    settings,
                )
                return result.matches
        finally:
            await engine.dispose()

    asyncio.run(mark_purged_without_deleting_archive())
    matches = asyncio.run(run_query())
    assert matches == []


def test_purging_twice_is_a_conflict_not_a_silent_noop(seeded):
    client, ids = seeded
    first = client.delete(f"/archive/{ids['mine']}")
    assert first.status_code == 200
    second = client.delete(f"/archive/{ids['mine']}")
    assert second.status_code == 409


def test_purging_a_strangers_manuscript_is_not_found(seeded):
    client, ids = seeded
    resp = client.delete(f"/archive/{ids['theirs']}")
    assert resp.status_code == 404


def test_out_of_range_pagination_is_a_clean_422_not_a_bare_500(seeded):
    """security-auditor (V-066 review): reproduced live as an unhandled
    500 before this bound existed."""
    client, _ = seeded
    assert client.get("/archive?page=0").status_code == 422
    assert client.get("/archive?page_size=0").status_code == 422
    assert client.get("/archive?page_size=99999").status_code == 422


def test_archive_route_requires_auth(client):
    assert client.get("/archive").status_code == 401
    assert client.delete("/archive/1").status_code == 401


def _seed_with(api_scratch_url, fn):
    """Runs `fn(session)` against the scratch DB on its own engine (same
    loop-binding reason as `seeded`)."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.test_schema import sqlalchemy_url

    async def run():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                result = await fn(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_stored_files_list_says_what_is_kept_and_links_the_latest_check(seeded, api_scratch_url):
    """V-078: the stored files page needs whether the file is still kept,
    whether it is a legacy Library record, and the newest check's id."""
    from sqlalchemy import select

    from app.models.run import CheckRun

    client, ids = seeded

    async def latest_run(session):
        return await session.scalar(
            select(CheckRun.id).where(CheckRun.manuscript_id == ids["mine"])
        )

    run_id = _seed_with(api_scratch_url, latest_run)
    item = client.get("/archive").json()["items"][0]
    assert item["has_stored_file"] is True
    assert item["in_library"] is False
    assert item["latest_check_run_id"] == run_id

    assert client.delete(f"/archive/{ids['mine']}").status_code == 200
    item = client.get("/archive").json()["items"][0]
    assert item["has_stored_file"] is False
    assert item["latest_check_run_id"] == run_id  # history is kept


def test_source_availability_observes_local_copy_then_absence(seeded):
    from pathlib import Path

    client, ids = seeded
    url = f"/archive/{ids['mine']}/availability"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["state"] == "found"
    assert response.json()["basis"] == "local_metadata"
    assert response.json()["checked_at"]
    assert set(response.json()) == {"manuscript_id", "state", "basis", "checked_at", "purged_at"}
    Path(ids["upload_path"]).unlink()
    response = client.get(url)
    assert response.json()["state"] == "missing"
    assert response.json()["basis"] is None
    assert not Path(ids["upload_path"]).exists()


def test_source_availability_never_probes_foreign_or_unrecorded_sources(
    seeded, api_scratch_url, monkeypatch
):
    from unittest.mock import Mock

    from sqlalchemy import update

    from app.models.enums import ManuscriptOrigin
    from app.models.manuscript import Manuscript

    client, ids = seeded
    probe = Mock(side_effect=AssertionError("unauthorized I/O"))
    monkeypatch.setattr("app.archive.service.probe_source", probe)
    assert client.get(f"/archive/{ids['theirs']}/availability").status_code == 404
    assert client.get("/archive/999999/availability").status_code == 404

    async def blank_source(session):
        await session.execute(
            update(Manuscript).where(Manuscript.id == ids["mine"]).values(file_ref="")
        )

    _seed_with(api_scratch_url, blank_source)
    assert client.get(f"/archive/{ids['mine']}/availability").json()["state"] == "not_recorded"

    async def library_source(session):
        await session.execute(
            update(Manuscript)
            .where(Manuscript.id == ids["mine"])
            .values(origin=ManuscriptOrigin.library_submission)
        )

    _seed_with(api_scratch_url, library_source)
    assert client.get(f"/archive/{ids['mine']}/availability").status_code == 404
    probe.assert_not_called()


def test_purged_source_never_probes_residual_cache(seeded, monkeypatch):
    from pathlib import Path
    from unittest.mock import Mock

    client, ids = seeded
    assert client.delete(f"/archive/{ids['mine']}").status_code == 200
    Path(ids["upload_path"]).write_bytes(b"stray old cache")
    probe = Mock(side_effect=AssertionError("purged source must not probe"))
    monkeypatch.setattr("app.archive.service.probe_source", probe)
    response = client.get(f"/archive/{ids['mine']}/availability")
    assert response.json()["state"] == "deleted"
    assert response.json()["purged_at"]
    probe.assert_not_called()


def test_source_availability_does_not_lock_out_purge_and_refreshes_deleted_state(
    seeded, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.storage.availability import SourceObservation

    client, ids = seeded
    entered, release = Event(), Event()

    def slow_probe(*_args):
        entered.set()
        assert release.wait(5)
        return SourceObservation("found", "object_metadata")

    monkeypatch.setattr("app.archive.service.probe_source", slow_probe)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(client.get, f"/archive/{ids['mine']}/availability")
        try:
            assert entered.wait(5)
            # A second DB request can complete while the probe is offloaded.
            assert client.get("/archive").status_code == 200
            assert client.delete(f"/archive/{ids['mine']}").status_code == 200
        finally:
            release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 200
    assert response.json()["state"] == "deleted"
    assert response.json()["basis"] is None


def test_source_availability_has_an_independent_rate_limit(seeded, monkeypatch):
    import app.ratelimit as limits

    client, ids = seeded
    monkeypatch.setattr(limits, "_limiters", {})
    monkeypatch.setenv("STORAGE_PROBE_RATE_LIMIT_MAX_ATTEMPTS", "1")
    get_settings.cache_clear()
    url = f"/archive/{ids['mine']}/availability"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 429
    # The independent scope does not disable list navigation or history.
    assert client.get("/archive").status_code == 200
    get_settings.cache_clear()


def test_purge_refuses_while_a_check_is_running(seeded, api_scratch_url):
    """V-078 edge case: deleting the files a running check reads would fail
    it for a reason the instructor never sees. Refuse, keep everything."""
    from pathlib import Path

    from sqlalchemy import update

    from app.models.enums import CheckRunStatus
    from app.models.run import CheckRun

    client, ids = seeded

    async def mark_running(session):
        await session.execute(
            update(CheckRun)
            .where(CheckRun.manuscript_id == ids["mine"])
            .values(status=CheckRunStatus.semantic)
        )

    _seed_with(api_scratch_url, mark_running)
    resp = client.delete(f"/archive/{ids['mine']}")
    assert resp.status_code == 409
    assert "still running" in resp.json()["error"]["message"]
    assert Path(ids["upload_path"]).exists()
    assert client.get("/archive").json()["items"][0]["purged_at"] is None


def test_purging_a_legacy_library_record_takes_it_out_of_the_library(seeded, api_scratch_url):
    """V-078 AC5: a check upload carried into the Library before V-077 leaves
    the Library when its stored file is deleted."""
    from sqlalchemy import select

    from app.models.enums import LibraryEntrySource, LibraryEntryStatus, LibraryRemovalReason
    from app.models.library import LibraryEntry

    client, ids = seeded

    async def add_entry(session):
        session.add(
            LibraryEntry(
                manuscript_id=ids["mine"],
                source=LibraryEntrySource.legacy_check_upload,
                status=LibraryEntryStatus.active,
            )
        )

    _seed_with(api_scratch_url, add_entry)
    assert client.get("/archive").json()["items"][0]["in_library"] is True
    assert client.delete(f"/archive/{ids['mine']}").status_code == 200

    async def read_entry(session):
        return await session.scalar(
            select(LibraryEntry).where(LibraryEntry.manuscript_id == ids["mine"])
        )

    entry = _seed_with(api_scratch_url, read_entry)
    assert entry.status == LibraryEntryStatus.removed
    assert entry.removal_reason == LibraryRemovalReason.purged
    assert client.get("/archive").json()["items"][0]["in_library"] is False
    assert client.get(f"/library/{ids['mine']}").status_code == 404


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_purge_refuses_while_source_preparation_is_running(seeded, api_scratch_url, status):
    from pathlib import Path

    from app.models.enums import IngestStatus
    from app.models.manuscript import Manuscript

    client, ids = seeded

    async def mark_preparing(session):
        manuscript = await session.get(Manuscript, ids["mine"])
        manuscript.ingest_status = IngestStatus(status)

    _seed_with(api_scratch_url, mark_preparing)
    response = client.delete(f"/archive/{ids['mine']}")
    assert response.status_code == 409
    assert "preparation" in response.json()["error"]["message"].lower()
    assert Path(ids["upload_path"]).is_file()
    assert Path(ids["raw_path"]).is_file()
    assert client.get("/archive").json()["items"][0]["purged_at"] is None


def test_stored_files_preserves_prior_report_after_new_cancelled_attempt(seeded, api_scratch_url):
    from sqlalchemy import select

    from app.models.enums import CheckRunStatus
    from app.models.run import CheckRun

    client, ids = seeded

    async def add_cancelled_attempt(session):
        previous = await session.scalar(
            select(CheckRun).where(CheckRun.manuscript_id == ids["mine"])
        )
        cancelled = CheckRun(
            manuscript_id=previous.manuscript_id,
            rubric_id=previous.rubric_id,
            status=CheckRunStatus.cancelled,
            created_at=previous.created_at,
        )
        session.add(cancelled)
        await session.flush()
        return previous.id, cancelled.id

    previous_id, cancelled_id = _seed_with(api_scratch_url, add_cancelled_attempt)
    item = client.get("/archive").json()["items"][0]
    assert item["latest_check_run_id"] == cancelled_id
    assert item["latest_check_run_status"] == "cancelled"
    assert item["latest_done_check_run_id"] == previous_id


def test_purge_and_new_check_creation_cannot_delete_an_active_source(
    seeded, api_scratch_url, monkeypatch
):
    import asyncio

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.archive import service as archive
    from app.db import sqlalchemy_url
    from app.errors import ConflictError
    from app.models.rubric import Rubric
    from app.models.run import CheckRun
    from app.pipeline.service import create_check_run

    _, ids = seeded
    settings = get_settings()

    async def exercise_race():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        reached_deletion = asyncio.Event()
        release_deletion = asyncio.Event()
        original_delete = archive.delete_archive_rows

        async def pause_before_deletion(session, manuscript_id):
            reached_deletion.set()
            await release_deletion.wait()
            return await original_delete(session, manuscript_id)

        monkeypatch.setattr(archive, "delete_archive_rows", pause_before_deletion)
        try:
            async with sessions() as session:
                previous = await session.scalar(
                    select(CheckRun).where(CheckRun.manuscript_id == ids["mine"])
                )
                rubric = await session.get(Rubric, previous.rubric_id)
                rubric.is_active = True
                rubric_id = rubric.id
                await session.commit()

            async def purge():
                async with sessions() as session:
                    return await archive.purge_manuscript(
                        session, ids["owner_id"], ids["mine"], settings
                    )

            async def start():
                async with sessions() as session:
                    return await create_check_run(
                        session, ids["owner_id"], ids["mine"], rubric_id, settings
                    )

            purge_task = asyncio.create_task(purge())
            await asyncio.wait_for(reached_deletion.wait(), timeout=5)
            start_task = asyncio.create_task(start())
            # A broken implementation completes creation during this pause;
            # correct row-locking waits, then sees the committed purge.
            await asyncio.wait({start_task}, timeout=0.2)
            release_deletion.set()
            purged, started = await asyncio.wait_for(
                asyncio.gather(purge_task, start_task, return_exceptions=True), timeout=5
            )
            assert not isinstance(purged, BaseException)
            assert isinstance(started, ConflictError), started
            assert "purged" in str(started).lower()
        finally:
            release_deletion.set()
            await engine.dispose()

    asyncio.run(exercise_race())


def test_waiting_shared_library_reader_rechecks_membership_after_removal(
    seeded, api_scratch_url, monkeypatch
):
    import asyncio
    from pathlib import Path

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.errors import NotFoundError
    from app.library import service as library
    from app.models.enums import LibraryEntrySource, LibraryEntryStatus
    from app.models.instructor import Instructor
    from app.models.library import LibraryEntry

    _, ids = seeded
    settings = get_settings()

    async def add_legacy_entry(session):
        session.add(
            LibraryEntry(
                manuscript_id=ids["mine"],
                source=LibraryEntrySource.legacy_check_upload,
                status=LibraryEntryStatus.active,
            )
        )
        return await session.scalar(select(Instructor.id).where(Instructor.id != ids["owner_id"]))

    foreign_id = _seed_with(api_scratch_url, add_legacy_entry)

    async def exercise_race():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        removal_has_lock = asyncio.Event()
        release_removal = asyncio.Event()
        original_delete = library.delete_archive_rows

        async def pause_removal(session, manuscript_id):
            removal_has_lock.set()
            await release_removal.wait()
            return await original_delete(session, manuscript_id)

        monkeypatch.setattr(library, "delete_archive_rows", pause_removal)

        async def remove():
            async with sessions() as session:
                return await library.remove_library_entry(
                    session, ids["owner_id"], ids["mine"], settings
                )

        async def read():
            async with sessions() as session:
                return await library.get_library_file_path(
                    session, foreign_id, ids["mine"], settings
                )

        tasks = []
        try:
            remover = asyncio.create_task(remove())
            tasks.append(remover)
            await asyncio.wait_for(removal_has_lock.wait(), timeout=5)
            reader = asyncio.create_task(read())
            tasks.append(reader)
            await asyncio.wait({reader}, timeout=0.2)
            release_removal.set()
            removed, result = await asyncio.wait_for(
                asyncio.gather(remover, reader, return_exceptions=True), timeout=5
            )
            assert not isinstance(removed, BaseException), removed
            assert isinstance(result, NotFoundError), result
            assert Path(ids["upload_path"]).is_file(), "legacy removal keeps the owner's source"
        finally:
            release_removal.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    asyncio.run(exercise_race())


def test_purge_is_visible_in_owner_audit_without_exposing_it_to_another_instructor(seeded):
    client, ids = seeded
    before_ids = {
        row["id"]
        for row in client.get("/audit", params={"event_type": "manuscript_purged"}).json()["items"]
    }
    assert client.delete(f"/archive/{ids['mine']}").status_code == 200
    rows = [
        row
        for row in client.get("/audit", params={"event_type": "manuscript_purged"}).json()["items"]
        if row["id"] not in before_ids
    ]
    assert len(rows) == 1
    assert rows[0]["manuscript_id"] == ids["mine"]
    detail = client.get(f"/audit/{rows[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["payload"]["instructor_id"] == ids["owner_id"]
    client.post("/auth/logout")
    login = client.post("/auth/login", json={"email": "stranger@tip.edu.ph", "password": "other!"})
    assert login.status_code == 200
    assert client.get("/audit", params={"event_type": "manuscript_purged"}).json()["items"] == []
    assert client.get(f"/audit/{rows[0]['id']}").status_code == 404


@pytest.mark.parametrize("cancel_count", [0, 1, 2])
@pytest.mark.parametrize("source_kind", ["pdf", "extraction", "malformed_pdf"])
def test_inflight_source_hydration_cannot_restore_bytes_after_purge(
    seeded, api_scratch_url, monkeypatch, cancel_count, source_kind
):
    import asyncio
    import threading
    from pathlib import Path

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.archive import service as archive
    from app.db import sqlalchemy_url
    from app.ingest import service as ingestion
    from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
    from app.models.run import CheckRun
    from app.report import service as report
    from app.storage import storage_key_for

    _, ids = seeded
    settings = get_settings()
    from app.report import source_files

    is_pdf = source_kind != "extraction"
    source_module = source_files if is_pdf else ingestion
    local_source = Path(ids["upload_path"] if is_pdf else ids["raw_path"])
    if source_kind == "malformed_pdf":
        local_source.write_bytes(b"%PDF-1.4 fake")
    if source_kind == "extraction":
        extraction = ExtractionResult(
            page_count=1,
            anchor_kind="page",
            image_only=False,
            text_chars=15,
            section_tree=SectionTree(source="none", nodes=[]),
            blocks=[TextBlock(text="Disposable body", page=1, max_font_size=11, bold_ratio=0)],
            images=[],
        )
        local_source.write_text(extraction.model_dump_json(), encoding="utf-8")
    original_bytes = local_source.read_bytes()
    source_key = storage_key_for(settings, str(local_source))
    local_source.unlink()
    fetched = threading.Event()
    release_fetch = threading.Event()
    refill_finished = threading.Event()
    original_ensure = source_module.ensure_local_file

    def observed_refill(*args):
        try:
            return original_ensure(*args)
        finally:
            refill_finished.set()

    class DelayedStorage:
        def __init__(self):
            self.objects = {source_key: original_bytes}

        def get_bytes(self, key):
            if key not in self.objects:
                raise FileNotFoundError(key)
            payload = self.objects[key]
            fetched.set()
            if not release_fetch.wait(timeout=5):
                raise TimeoutError("test did not release the controlled source read")
            return payload

        def delete(self, key):
            self.objects.pop(key, None)

    storage = DelayedStorage()
    monkeypatch.setattr(source_module, "get_storage", lambda _: storage)
    monkeypatch.setattr(source_module, "ensure_local_file", observed_refill)
    monkeypatch.setattr(archive, "get_storage", lambda _: storage)

    async def exercise_race():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def read_source():
            async with sessions() as session:
                if source_kind == "extraction":
                    return await ingestion.get_group_proposal(
                        session, settings, ids["mine"], ids["owner_id"]
                    )
                run_id = await session.scalar(
                    select(CheckRun.id).where(CheckRun.manuscript_id == ids["mine"])
                )
                return await report.get_manuscript_file_path(
                    session, run_id, ids["owner_id"], settings
                )

        async def purge():
            async with sessions() as session:
                return await archive.purge_manuscript(
                    session, ids["owner_id"], ids["mine"], settings
                )

        tasks = []
        try:
            reader = asyncio.create_task(read_source())
            tasks.append(reader)
            assert await asyncio.to_thread(fetched.wait, 5)
            if cancel_count:
                reader.cancel()
            purger = asyncio.create_task(purge())
            tasks.append(purger)
            await asyncio.wait({purger}, timeout=0.2)
            if cancel_count > 1:
                reader.cancel()
            release_fetch.set()
            read_result, purge_result = await asyncio.wait_for(
                asyncio.gather(reader, purger, return_exceptions=True), timeout=5
            )
            assert await asyncio.to_thread(refill_finished.wait, 5)
            assert not isinstance(purge_result, BaseException), purge_result
            if cancel_count:
                assert isinstance(read_result, asyncio.CancelledError)
            elif source_kind == "malformed_pdf":
                from app.errors import ConflictError

                assert isinstance(read_result, ConflictError)
            elif source_kind == "pdf":
                assert read_result == local_source
            else:
                assert not isinstance(read_result, BaseException), read_result
            assert not local_source.exists(), "source bytes were restored after successful purge"
            assert storage.objects == {}
        finally:
            release_fetch.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    asyncio.run(exercise_race())
