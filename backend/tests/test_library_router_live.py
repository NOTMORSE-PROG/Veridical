"""V-066: the shared-corpus library HTTP surface end-to-end. Needs a live
Postgres (same convention as test_archive_router_live.py) -- two real
instructors, each with their own manuscript, so ownership-gated behavior
(document/file/paragraphs own-only, excerpt/detail available to anyone) has
something real to differ against.
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

SCRATCH_DB = "veridical_libraryapitest"


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
    """Two instructors, one manuscript each: `owner`'s is a real on-disk
    PDF with a chapter + passage archive (own document view AND the
    cross-tenant excerpt both need real data); `stranger`'s exists mainly
    so `owner`'s library requests have a genuine "not mine" row to see."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import IngestStatus
    from app.models.group import Group, GroupMember, Program
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript, ManuscriptChapterArchive, ManuscriptPassageArchive

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
                        "manuscript_archive, group_member, manuscript, manuscript_group, "
                        "program, session, instructor RESTART IDENTITY CASCADE"
                    )
                )
                owner = Instructor(
                    email="owner@tip.edu.ph",
                    display_name="Owner",
                    password_hash=hash_password("s3cret!"),
                )
                stranger = Instructor(
                    email="stranger2@tip.edu.ph",
                    display_name="Stranger",
                    password_hash=hash_password("other!"),
                )
                session.add_all([owner, stranger])
                await session.commit()

                program = Program(name="IT")
                session.add(program)
                await session.commit()

                own_group = Group(
                    instructor_id=owner.id,
                    name="Own Group",
                    name_normalized="own group",
                    program_id=program.id,
                    title="A Real Capstone Title",
                )
                stranger_group = Group(
                    instructor_id=stranger.id,
                    name="Stranger Group",
                    name_normalized="stranger group",
                )
                session.add_all([own_group, stranger_group])
                await session.commit()
                session.add(GroupMember(group_id=own_group.id, name="Author One"))
                await session.commit()

                mine = Manuscript(
                    instructor_id=owner.id,
                    group_label="Own Group",
                    group_id=own_group.id,
                    file_ref="",
                    ingest_status=IngestStatus.done,
                )
                theirs = Manuscript(
                    instructor_id=stranger.id,
                    group_label="Stranger Group",
                    group_id=stranger_group.id,
                    file_ref="",
                    ingest_status=IngestStatus.done,
                )
                session.add_all([mine, theirs])
                await session.commit()

                upload_path = upload_dir / f"{mine.id}.pdf"
                upload_path.write_bytes(
                    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
                    b"trailer<</Root 1 0 R>>"
                )
                mine.file_ref = str(upload_path)
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
                session.add(
                    ManuscriptPassageArchive(
                        manuscript_id=mine.id,
                        passage_index=0,
                        chapter_index=0,
                        page=1,
                        char_start=0,
                        char_end=20,
                        text="the matched passage",
                        context_text="before words the matched passage after words",
                        is_reference_list=False,
                        is_block_quote=False,
                        embedding=[0.0] * 256,
                        model_id="test-model",
                    )
                )
                await session.commit()

                ids["mine"] = mine.id
                ids["theirs"] = theirs.id
                ids["owner_id"] = owner.id
                ids["upload_path"] = str(upload_path)
        finally:
            await engine.dispose()

    asyncio.run(seed())
    client.post("/auth/login", json={"email": "owner@tip.edu.ph", "password": "s3cret!"})
    return client, ids


def test_list_library_spans_both_accounts_unlike_archive(seeded):
    client, ids = seeded
    resp = client.get("/library")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2  # BOTH manuscripts, not just the caller's own
    by_id = {item["manuscript_id"]: item for item in body["items"]}
    assert by_id[ids["mine"]]["is_own"] is True
    assert by_id[ids["mine"]]["title"] == "A Real Capstone Title"
    assert by_id[ids["mine"]]["authors"] == ["Author One"]
    assert by_id[ids["mine"]]["program"] == "IT"
    assert by_id[ids["theirs"]]["is_own"] is False
    assert by_id[ids["theirs"]]["program"] is None


def test_list_library_filters_by_program(seeded):
    client, ids = seeded
    resp = client.get("/library?program=IT")
    assert resp.status_code == 200, resp.text
    ids_returned = {item["manuscript_id"] for item in resp.json()["items"]}
    assert ids_returned == {ids["mine"]}


def test_list_library_searches_group_title_and_author(seeded):
    client, ids = seeded
    for q in ["Real Capstone", "Own Group", "Author One"]:
        resp = client.get(f"/library?search={q}")
        assert resp.status_code == 200, resp.text
        assert {item["manuscript_id"] for item in resp.json()["items"]} == {ids["mine"]}


def test_library_item_detail_visible_for_a_strangers_manuscript_too(seeded):
    client, ids = seeded
    resp = client.get(f"/library/{ids['theirs']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_own"] is False
    assert body["group_label"] == "Stranger Group"


def test_library_excerpt_is_bounded_and_available_for_any_manuscript(seeded):
    client, ids = seeded
    resp = client.get(f"/library/{ids['mine']}/excerpt")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_chapters"] == 1
    assert len(body["chapters"]) == 1
    chapter = body["chapters"][0]
    assert chapter["title"] == "Chapter 1"
    assert chapter["excerpt"] == "the matched passage"
    assert chapter["context_before"] == "before words"
    assert chapter["context_after"] == "after words"
    assert "bounded excerpt" in body["limitations"]


def test_library_own_document_is_reachable(seeded):
    client, ids = seeded
    resp = client.get(f"/library/{ids['mine']}/document")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["source_format"] == "pdf"

    file_resp = client.get(f"/library/{ids['mine']}/document/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"] == "application/pdf"


def test_library_document_for_a_strangers_manuscript_is_not_found(seeded):
    """Q2's ruling, enforced at the API: the full document is never
    reachable for another instructor's manuscript, regardless of what the
    UI does or doesn't render."""
    client, ids = seeded
    assert client.get(f"/library/{ids['theirs']}/document").status_code == 404
    assert client.get(f"/library/{ids['theirs']}/document/file").status_code == 404
    assert client.get(f"/library/{ids['theirs']}/document/paragraphs").status_code == 404


def test_purged_manuscript_stays_visible_with_purged_state(seeded):
    """AC5: a purged manuscript's record and its purged state both stay
    visible -- the row must not vanish from the corpus-wide list."""
    client, ids = seeded
    purge = client.delete(f"/archive/{ids['mine']}")
    assert purge.status_code == 200, purge.text

    listing = client.get("/library")
    by_id = {item["manuscript_id"]: item for item in listing.json()["items"]}
    assert by_id[ids["mine"]]["purged_at"] is not None
    assert listing.json()["total"] == 2  # still both rows, not one fewer

    excerpt = client.get(f"/library/{ids['mine']}/excerpt")
    assert excerpt.status_code == 200, excerpt.text
    # BUG-123 defense-in-depth: purged content is never re-served here,
    # even though the underlying ManuscriptPassageArchive row still exists
    # (the bug this endpoint guards against, not fixes).
    assert excerpt.json()["chapters"] == []
    assert excerpt.json()["purged_at"] is not None


def test_out_of_range_pagination_is_a_clean_422_not_a_bare_500(seeded):
    """security-auditor (V-066 review): reproduced live as an unhandled
    500 before this bound existed."""
    client, _ = seeded
    assert client.get("/library?page=0").status_code == 422
    assert client.get("/library?page=-1").status_code == 422
    assert client.get("/library?page_size=0").status_code == 422
    assert client.get("/library?page_size=99999").status_code == 422


def test_library_routes_require_auth(client):
    assert client.get("/library").status_code == 401
    assert client.get("/library/1").status_code == 401
    assert client.get("/library/1/excerpt").status_code == 401
    assert client.get("/library/1/document").status_code == 401
