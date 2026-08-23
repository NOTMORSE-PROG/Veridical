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
                upload_path.write_bytes(b"%PDF-1.4 fake")
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
