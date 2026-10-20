"""V-077 (D-028): the Library as a standalone collection and the only F7
corpus, end to end over HTTP against a live Postgres. Two real instructors,
real fixture files, fake-LLM mode, and a recorded vision pass (a Library
submission must cost zero Gemini calls; a dashboard upload may make one).

Replaces the V-066 suite, whose subject (every dashboard upload listed as a
Library record) no longer exists.
"""

import asyncio
import os
from pathlib import Path

import asyncpg
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

SCRATCH_DB = "veridical_library_v077_test"
FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
OWNER = ("owner@tip.edu.ph", "s3cret!")
STRANGER = ("stranger@tip.edu.ph", "other!")


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


VISION_CALLS: list[str] = []


@pytest.fixture()
def client(api_scratch_url, tmp_path, monkeypatch):
    """Records every Gemini vision call: a dashboard upload may legitimately
    make one, a Library submission never may (asserted per test)."""
    import app.db as db
    import app.ingest.vision as vision
    import app.ratelimit as ratelimit

    async def record_vision(result, file_path, *args, **kwargs):
        VISION_CALLS.append(str(file_path))

    VISION_CALLS.clear()
    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    ratelimit._limiters.clear()
    asyncio.run(_reset(api_scratch_url))
    monkeypatch.setattr(vision, "read_images", record_vision)
    from app.main import app

    with TestClient(app) as c:
        yield c
    db._engine = None
    auth_service._rate_limiter = None


async def _reset(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            "TRUNCATE library_entry, manuscript_passage_archive, manuscript_chapter_archive, "
            "manuscript_archive, citation, audit_log, group_member, manuscript, manuscript_group, "
            "program, session, instructor RESTART IDENTITY CASCADE"
        )
        for email, password in (OWNER, STRANGER):
            await conn.execute(
                "INSERT INTO instructor (email, display_name, password_hash) VALUES ($1, $2, $3)",
                email,
                email.split("@")[0],
                hash_password(password),
            )
        await conn.execute("INSERT INTO program (name) VALUES ('IT'), ('CS')")
    finally:
        await conn.close()


def _fetchval(url: str, sql: str, *args):
    async def run():
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


def _login(client: TestClient, account: tuple[str, str]) -> None:
    r = client.post("/auth/login", json={"email": account[0], "password": account[1]})
    assert r.status_code == 200, r.text


def _submit(client: TestClient, content: bytes, name: str, **form) -> dict:
    r = client.post(
        "/library/entries",
        files={"file": (name, content, "application/octet-stream")},
        data=form,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _pdf() -> bytes:
    return (FIXTURES / "native.pdf").read_bytes()


def _same_text_other_bytes() -> bytes:
    # Trailing bytes after %%EOF: a different file (different hash) whose
    # extracted text is identical.
    return _pdf() + b"\n% a re-exported copy\n"


def _corpus_rows(url: str, manuscript_id: int) -> int:
    return _fetchval(
        url,
        "SELECT (SELECT count(*) FROM manuscript_archive WHERE manuscript_id = $1)"
        " + (SELECT count(*) FROM manuscript_chapter_archive WHERE manuscript_id = $1)"
        " + (SELECT count(*) FROM manuscript_passage_archive WHERE manuscript_id = $1)",
        manuscript_id,
    )


def test_submission_adds_an_active_entry_with_comparison_data(client, api_scratch_url):
    _login(client, OWNER)
    body = _submit(client, _pdf(), "native.pdf", program="IT", school_year="2025-2026")

    assert body["outcome"] == "added", body
    item = body["item"]
    assert item["status"] == "active"
    assert item["is_own"] is True
    assert item["program"] == "IT"
    assert item["school_year"] == "2025-2026"
    assert item["source"] == "library_submission"
    assert item["added_at"] is not None
    manuscript_id = item["manuscript_id"]

    assert (
        _fetchval(api_scratch_url, "SELECT origin FROM manuscript WHERE id = $1", manuscript_id)
        == "library_submission"
    )
    assert (
        _fetchval(
            api_scratch_url,
            "SELECT count(*) FROM manuscript_archive WHERE manuscript_id = $1",
            manuscript_id,
        )
        == 1
    )
    assert (
        _fetchval(
            api_scratch_url,
            "SELECT count(*) FROM manuscript_passage_archive WHERE manuscript_id = $1",
            manuscript_id,
        )
        > 0
    )
    assert (
        _fetchval(
            api_scratch_url,
            "SELECT count(*) FROM audit_log WHERE event_type = 'library_entry_added'",
        )
        == 1
    )

    listing = client.get("/library").json()
    assert listing["total"] == 1
    assert listing["items"][0]["manuscript_id"] == manuscript_id
    assert VISION_CALLS == [], "a Library submission ran the Gemini vision pass"


def test_exact_copy_of_own_entry_creates_nothing(client, api_scratch_url):
    _login(client, OWNER)
    first = _submit(client, _pdf(), "native.pdf")["item"]["manuscript_id"]

    again = _submit(client, _pdf(), "renamed-copy.pdf")

    assert again["outcome"] == "exact_duplicate"
    assert again["item"] is None
    assert again["duplicate_of"]["manuscript_id"] == first
    assert _fetchval(api_scratch_url, "SELECT count(*) FROM manuscript") == 1
    uploads = get_settings().data_dir / "uploads"
    assert not list(uploads.glob("library-pending-*")), "a duplicate left its upload behind"


def test_same_text_in_another_file_waits_for_the_instructor_then_add_anyway(
    client, api_scratch_url
):
    _login(client, OWNER)
    first = _submit(client, _pdf(), "native.pdf")["item"]["manuscript_id"]

    body = _submit(client, _same_text_other_bytes(), "native-reexport.pdf")

    assert body["outcome"] == "possible_duplicate", body
    waiting = body["item"]
    assert client.get("/library").json()["total"] == 1
    assert client.get("/library", params={"scope": "pending"}).json()["total"] == 1
    assert waiting["status"] == "needs_decision"
    assert body["duplicate_of"]["manuscript_id"] == first
    assert waiting["possible_duplicate_of"]["manuscript_id"] == first
    # Not a comparison source while it waits.
    assert _corpus_rows(api_scratch_url, waiting["manuscript_id"]) == 0

    decided = client.post(f"/library/{waiting['manuscript_id']}/decision", json={"action": "add"})
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "active"
    assert _corpus_rows(api_scratch_url, waiting["manuscript_id"]) > 0


def test_skip_removes_the_waiting_entry_and_deletes_its_file(client, api_scratch_url):
    _login(client, OWNER)
    _submit(client, _pdf(), "native.pdf")
    waiting = _submit(client, _same_text_other_bytes(), "copy.pdf")["item"]
    manuscript_id = waiting["manuscript_id"]
    stored = Path(
        _fetchval(api_scratch_url, "SELECT file_ref FROM manuscript WHERE id = $1", manuscript_id)
    )
    assert stored.exists()

    skipped = client.post(f"/library/{manuscript_id}/decision", json={"action": "skip"})

    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["status"] == "removed"
    assert skipped.json()["file_deleted"] is True
    assert not stored.exists()
    assert client.get(f"/library/{manuscript_id}").status_code == 404


def test_decision_on_an_entry_that_is_not_waiting_is_a_conflict(client):
    _login(client, OWNER)
    manuscript_id = _submit(client, _pdf(), "native.pdf")["item"]["manuscript_id"]
    r = client.post(f"/library/{manuscript_id}/decision", json={"action": "add"})
    assert r.status_code == 409


def test_a_file_with_no_readable_text_fails_honestly(client, api_scratch_url):
    _login(client, OWNER)
    body = _submit(client, (FIXTURES / "scan_imageonly.pdf").read_bytes(), "scan.pdf")

    assert body["outcome"] == "failed", body
    assert body["item"]["status"] == "failed"
    assert "readable text" in body["item"]["failure_detail"]
    assert _corpus_rows(api_scratch_url, body["item"]["manuscript_id"]) == 0
    # The owner can still see it and act on it.
    assert client.get(f"/library/{body['item']['manuscript_id']}").status_code == 200
    # Never counted in the collection; listed only among the owner's pending uploads.
    assert client.get("/library").json()["total"] == 0
    pending = client.get("/library", params={"scope": "pending"}).json()
    assert [i["manuscript_id"] for i in pending["items"]] == [body["item"]["manuscript_id"]]


def test_owner_edits_metadata(client):
    _login(client, OWNER)
    manuscript_id = _submit(client, _pdf(), "native.pdf")["item"]["manuscript_id"]

    r = client.patch(
        f"/library/{manuscript_id}",
        json={
            "title": "  An Edited Title ",
            "authors": ["Ana Cruz", " ", "Ben Reyes"],
            "program": "CS",
        },
    )
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["title"] == "An Edited Title"
    assert item["authors"] == ["Ana Cruz", "Ben Reyes"]
    assert item["program"] == "CS"
    assert "title" not in item["proposed_fields"]
    assert item["label"] == "An Edited Title"

    assert client.patch(f"/library/{manuscript_id}", json={"program": "Nope"}).status_code == 404
    assert client.patch(f"/library/{manuscript_id}", json={"title": "x" * 501}).status_code == 422


def test_removing_a_library_submission_deletes_its_file_and_comparison_data(
    client, api_scratch_url
):
    _login(client, OWNER)
    manuscript_id = _submit(client, _pdf(), "native.pdf")["item"]["manuscript_id"]
    stored = Path(
        _fetchval(api_scratch_url, "SELECT file_ref FROM manuscript WHERE id = $1", manuscript_id)
    )

    r = client.post(f"/library/{manuscript_id}/remove")

    assert r.status_code == 200, r.text
    assert r.json()["file_deleted"] is True
    assert _corpus_rows(api_scratch_url, manuscript_id) == 0
    assert not stored.exists()
    assert client.get("/library").json()["total"] == 0
    assert client.post(f"/library/{manuscript_id}/remove").status_code == 404


def test_removing_a_legacy_entry_keeps_the_checked_manuscripts_file(client, api_scratch_url):
    """A record carried over by migration 0037 is a checked manuscript; its
    report still needs the file, so only the comparison data leaves."""
    _login(client, OWNER)
    ingested = client.post(
        "/manuscripts/ingest",
        files={"file": ("native.pdf", _pdf(), "application/octet-stream")},
    )
    assert ingested.status_code == 200, ingested.text
    manuscript_id = ingested.json()["manuscript_id"]
    asyncio.run(_make_legacy_entry(api_scratch_url, manuscript_id))
    assert _corpus_rows(api_scratch_url, manuscript_id) > 0
    stored = Path(
        _fetchval(api_scratch_url, "SELECT file_ref FROM manuscript WHERE id = $1", manuscript_id)
    )

    r = client.post(f"/library/{manuscript_id}/remove")

    assert r.status_code == 200, r.text
    assert r.json()["file_deleted"] is False
    assert _corpus_rows(api_scratch_url, manuscript_id) == 0
    assert stored.exists()
    assert (
        _fetchval(api_scratch_url, "SELECT purged_at FROM manuscript WHERE id = $1", manuscript_id)
        is None
    )


async def _make_legacy_entry(url: str, manuscript_id: int) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.checks.reuse.embed import compute_document_embeddings, compute_passage_embeddings
    from app.checks.reuse.store import store_document_embeddings, store_passage_embeddings
    from app.db import sqlalchemy_url
    from app.ingest.service import load_raw_store
    from app.models.enums import LibraryEntrySource, LibraryEntryStatus
    from app.models.library import LibraryEntry

    settings = get_settings()
    extraction = load_raw_store(settings, manuscript_id)
    engine = create_async_engine(sqlalchemy_url(url))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await store_document_embeddings(
                session, manuscript_id, compute_document_embeddings(extraction, settings)
            )
            await store_passage_embeddings(
                session, manuscript_id, compute_passage_embeddings(extraction, settings), settings
            )
            session.add(
                LibraryEntry(
                    manuscript_id=manuscript_id,
                    source=LibraryEntrySource.legacy_check_upload,
                    status=LibraryEntryStatus.active,
                    title="A Legacy Title",
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def test_other_instructors_can_read_active_files_but_not_private_uploads_or_write(
    client, api_scratch_url, monkeypatch
):
    _login(client, OWNER)
    active = _submit(
        client,
        _pdf(),
        "secret-file-name.pdf",
        title="A Confidential Capstone Title",
        team_name="Secret Team",
        authors=["Maria Santos"],
        program="IT",
        school_year="2024-2025",
    )["item"]["manuscript_id"]
    failed = _submit(client, (FIXTURES / "scan_imageonly.pdf").read_bytes(), "scan.pdf")["item"][
        "manuscript_id"
    ]

    _login(client, STRANGER)
    listing = client.get("/library").json()
    assert listing["total"] == 1
    seen = listing["items"][0]
    assert seen["manuscript_id"] == active
    assert seen["is_own"] is False
    assert seen["label"] == f"Library record #{active}"
    assert seen["title"] is None
    assert seen["team_name"] is None
    assert seen["authors"] == []
    assert seen["original_filename"] is None
    assert seen["source"] is None
    assert seen["program"] == "IT"
    assert seen["school_year"] == "2024-2025"
    assert "Maria" not in str(listing) and "Confidential" not in str(listing)

    # Identity search is own-only: a hit would confirm the name exists.
    assert client.get("/library", params={"search": "Maria"}).json()["total"] == 0
    assert client.get("/library", params={"search": "Secret"}).json()["total"] == 0

    assert client.get(f"/library/{active}").status_code == 200
    excerpt = client.get(f"/library/{active}/excerpt")
    assert excerpt.status_code == 200
    assert "open the full manuscript" in excerpt.json()["limitations"]
    assert "never shows another" not in excerpt.json()["limitations"]
    assert client.get(f"/library/{failed}").status_code == 404
    assert client.get(f"/library/{failed}/excerpt").status_code == 404
    viewer = client.get(f"/library/{active}/document")
    assert viewer.status_code == 200, viewer.text
    assert viewer.json()["available"] is True
    assert viewer.json()["original_filename"] is None
    file_response = client.get(f"/library/{active}/document/file")
    assert file_response.status_code == 200
    assert file_response.content == _pdf()
    assert file_response.headers["Cache-Control"] == "private, no-store"
    import app.library.service as library_service

    storage_factory = library_service.get_storage
    storage_threads = []

    def checked_storage(settings):
        # A cold storage client may block during credential discovery. The
        # download must construct it on a worker, outside any running loop.
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        storage_threads.append(True)
        return storage_factory(settings)

    monkeypatch.setattr(library_service, "get_storage", checked_storage)
    original = client.get(f"/library/{active}/document/original")
    assert original.status_code == 200 and original.content == _pdf()
    assert storage_threads
    assert "secret-file-name" not in original.headers.get("Content-Disposition", "")
    for method, path, kwargs in (
        ("patch", f"/library/{active}", {"json": {"title": "mine now"}}),
        ("post", f"/library/{active}/remove", {}),
        ("post", f"/library/{active}/decision", {"json": {"action": "skip"}}),
        ("get", f"/library/{failed}/document", {}),
        ("get", f"/library/{failed}/document/file", {}),
        ("get", f"/library/{failed}/document/paragraphs", {}),
        ("get", f"/library/{failed}/document/original", {}),
    ):
        r = getattr(client, method)(path, **kwargs)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
    assert _corpus_rows(api_scratch_url, active) > 0


def test_other_instructors_can_read_full_docx_text_and_lose_access_after_removal(client):
    _login(client, OWNER)
    item = _submit(client, (FIXTURES / "native.docx").read_bytes(), "source.docx")["item"]
    mid = item["manuscript_id"]
    expected = client.get(f"/library/{mid}/document/paragraphs").json()
    assert expected["paragraphs"]
    _login(client, STRANGER)
    viewer = client.get(f"/library/{mid}/document").json()
    assert viewer["source_format"] == "docx" and viewer["available"] is True
    assert viewer["original_filename"] is None
    response = client.get(f"/library/{mid}/document/paragraphs")
    assert response.status_code == 200 and response.json() == expected
    assert response.headers["Cache-Control"] == "private, no-store"
    original = client.get(f"/library/{mid}/document/original")
    assert original.status_code == 200
    assert original.content == (FIXTURES / "native.docx").read_bytes()
    assert original.headers["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    _login(client, OWNER)
    assert client.post(f"/library/{mid}/remove").status_code == 200
    _login(client, STRANGER)
    for suffix in ("document", "document/file", "document/paragraphs", "document/original"):
        response = client.get(f"/library/{mid}/{suffix}")
        assert response.status_code == 404
        assert response.headers["Cache-Control"] == "private, no-store"


def test_original_docx_images_follow_each_read_surfaces_own_access_policy(
    client, api_scratch_url, tmp_path
):
    from tests.test_docx_source_media import _source
    from tests.test_ingest_docx import _png_bytes

    source = tmp_path / "native-media.docx"
    document = _source(source)
    document.add_paragraph(
        "This source contains a complete synthetic body for testing document reading. "
        "It does not represent an analytical result or a real manuscript evaluation."
    )
    document.save(source)
    _login(client, OWNER)
    item = _submit(client, source.read_bytes(), source.name)["item"]
    mid = item["manuscript_id"]
    body = client.get(f"/library/{mid}/document/paragraphs").json()
    assert body["tables"][0]["rows"][0][0] == "Original table text"
    assert [(row["paragraph"], row["available"]) for row in body["images"]] == [
        (1, True),
        (3, True),
    ]
    owner_id = _fetchval(api_scratch_url, "SELECT id FROM instructor WHERE email=$1", OWNER[0])
    rubric_id = _fetchval(
        api_scratch_url,
        "INSERT INTO rubric(instructor_id,title,source_file) "
        "VALUES($1,'Format','r.pdf') RETURNING id",
        owner_id,
    )
    run_id = _fetchval(
        api_scratch_url,
        "INSERT INTO check_run(manuscript_id,rubric_id,status) VALUES($1,$2,'done') RETURNING id",
        mid,
        rubric_id,
    )
    report_path = f"/check-runs/{run_id}/document/images/0"
    library_path = f"/library/{mid}/document/images/0"
    for path in (report_path, library_path):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.content == _png_bytes()
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/library/{mid}/document/images/-1").status_code == 422
    assert client.get(f"/library/{mid}/document/images/999999").status_code == 404
    _login(client, STRANGER)
    assert client.get(report_path).status_code == 404
    assert client.get(library_path).content == _png_bytes()
    _fetchval(
        api_scratch_url,
        "UPDATE library_entry SET status='needs_decision' WHERE manuscript_id=$1",
        mid,
    )
    assert client.get(library_path).status_code == 404
    _login(client, OWNER)
    assert client.get(library_path).content == _png_bytes()
    _fetchval(
        api_scratch_url, "UPDATE library_entry SET status='active' WHERE manuscript_id=$1", mid
    )
    _fetchval(api_scratch_url, "UPDATE manuscript SET purged_at=now() WHERE id=$1", mid)
    for path in (report_path, library_path):
        assert client.get(path).status_code == 410
    assert client.post("/auth/logout").status_code in (200, 204)
    for path in (report_path, library_path):
        assert client.get(path).status_code == 401
    assert VISION_CALLS == []


def test_shared_reading_never_exposes_non_library_uploads_or_anonymous_files(client):
    _login(client, OWNER)
    checked = client.post(
        "/manuscripts/ingest", files={"file": ("private-check.pdf", _pdf(), "application/pdf")}
    )
    assert checked.status_code == 200
    mid = checked.json()["manuscript_id"]
    _login(client, STRANGER)
    for suffix in ("document", "document/file", "document/paragraphs", "document/original"):
        response = client.get(f"/library/{mid}/{suffix}")
        assert response.status_code == 404
    assert client.post("/auth/logout").status_code in (200, 204)
    for suffix in ("document", "document/file", "document/paragraphs", "document/original"):
        response = client.get(f"/library/{mid}/{suffix}")
        assert response.status_code == 401
        assert response.headers["Cache-Control"] == "private, no-store"


def test_duplicate_detection_never_crosses_accounts(client):
    """BUG-234: telling one instructor that another account holds the same
    bytes would be a cross-tenant existence oracle."""
    _login(client, OWNER)
    _submit(client, _pdf(), "native.pdf")

    _login(client, STRANGER)
    body = _submit(client, _pdf(), "native.pdf")
    assert body["outcome"] == "added", body
    assert body["duplicate_of"] is None


def test_library_submissions_never_reach_the_review_desk_or_a_check(client):
    _login(client, OWNER)
    manuscript_id = _submit(client, _pdf(), "native.pdf")["item"]["manuscript_id"]

    assert client.get("/manuscripts").json()["total"] == 0
    stats = client.get("/stats").json()
    assert stats["manuscripts_checked"] == 0
    assert stats["needs_attention_count"] == 0
    r = client.post("/check-runs", json={"manuscript_id": manuscript_id, "rubric_id": 1})
    assert r.status_code == 404, r.text
    assert client.get(f"/manuscripts/{manuscript_id}/group-proposal").status_code == 404


def test_a_dashboard_upload_is_not_added_to_the_library(client):
    _login(client, OWNER)
    ingested = client.post(
        "/manuscripts/ingest",
        files={"file": ("native.pdf", _pdf(), "application/octet-stream")},
    )
    assert ingested.status_code == 200, ingested.text
    manuscript_id = ingested.json()["manuscript_id"]

    assert client.get("/library").json()["total"] == 0
    assert client.get(f"/library/{manuscript_id}").status_code == 404
    assert client.get(f"/library/{manuscript_id}/excerpt").status_code == 404
    assert client.get(f"/library/{manuscript_id}/document").status_code == 404
    # Control for the vision assertion above: the dashboard path does read
    # images, so an empty recorder there is meaningful, not vacuous.
    assert VISION_CALLS, "the dashboard upload should have run the vision pass"


# --- V-077 review fixes (security-auditor / backend review / UX review, 2026-10-13) ---


def _set_created_at_seconds_ago(url: str, manuscript_id: int, seconds: int) -> None:
    _fetchval(
        url,
        "UPDATE library_entry SET created_at = now() - make_interval(secs => $2)"
        " WHERE manuscript_id = $1 RETURNING id",
        manuscript_id,
        seconds,
    )


def _make_processing_entry(url: str, owner_email: str, content: bytes) -> int:
    """A processing entry as a still-running (or crashed) request leaves it."""
    import hashlib

    return _fetchval(
        url,
        "WITH m AS ("
        " INSERT INTO manuscript (instructor_id, file_ref, group_label, origin, content_hash)"
        " SELECT id, '', 'Ungrouped', 'library_submission', $2 FROM instructor WHERE email = $1"
        " RETURNING id)"
        " INSERT INTO library_entry (manuscript_id, source, status)"
        " SELECT id, 'library_submission', 'processing' FROM m RETURNING manuscript_id",
        owner_email,
        hashlib.sha256(content).hexdigest(),
    )


def test_a_live_upload_cannot_be_removed_but_a_stalled_one_can(client, api_scratch_url):
    """Removing during reading used to be undone by the reading request
    (resurrecting the text as an active comparison source)."""
    _login(client, OWNER)
    manuscript_id = _make_processing_entry(api_scratch_url, OWNER[0], b"still being read")

    r = client.post(f"/library/{manuscript_id}/remove")
    assert r.status_code == 409, r.text
    assert "still reading" in r.json()["error"]["message"]

    _set_created_at_seconds_ago(api_scratch_url, manuscript_id, 3600)
    item = client.get(f"/library/{manuscript_id}").json()
    assert item["stalled"] is True
    assert client.post(f"/library/{manuscript_id}/remove").status_code == 200


def test_a_stalled_upload_never_blocks_re_adding_the_file(client, api_scratch_url):
    _login(client, OWNER)
    manuscript_id = _make_processing_entry(api_scratch_url, OWNER[0], _pdf())
    assert _submit(client, _pdf(), "native.pdf")["outcome"] == "exact_duplicate"

    _set_created_at_seconds_ago(api_scratch_url, manuscript_id, 3600)
    assert _submit(client, _pdf(), "native.pdf")["outcome"] == "added"


def test_a_non_document_is_rejected_before_anything_is_stored(client, api_scratch_url):
    _login(client, OWNER)
    body = _submit(client, b"just some notes, not a manuscript", "notes.txt")

    assert body["outcome"] == "failed"
    assert body["item"] is None
    assert _fetchval(api_scratch_url, "SELECT count(*) FROM manuscript") == 0
    uploads = get_settings().data_dir / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())


def test_re_sending_an_unreadable_file_stores_nothing_new(client, api_scratch_url):
    _login(client, OWNER)
    scan = (FIXTURES / "scan_imageonly.pdf").read_bytes()
    first = _submit(client, scan, "scan.pdf")
    second = _submit(client, scan, "scan-again.pdf")

    assert first["outcome"] == second["outcome"] == "failed"
    assert second["item"]["manuscript_id"] == first["item"]["manuscript_id"]
    assert "could not read this exact file before" in second["message"]
    assert _fetchval(api_scratch_url, "SELECT count(*) FROM manuscript") == 1


def test_a_pending_copy_is_never_called_in_the_library(client):
    """UX review P1-1: a file waiting for a duplicate decision was reported as
    "Already in your Library" while the Library said it was not."""
    _login(client, OWNER)
    _submit(client, _pdf(), "native.pdf")
    waiting = _submit(client, _same_text_other_bytes(), "reexport.pdf")
    assert waiting["outcome"] == "possible_duplicate"

    again = _submit(client, _same_text_other_bytes(), "reexport.pdf")
    assert again["outcome"] == "exact_duplicate"
    assert again["duplicate_of"]["manuscript_id"] == waiting["item"]["manuscript_id"]
    assert "waiting for a duplicate decision" in again["message"]
    assert "already in your Library" not in again["message"]


def test_a_storage_failure_leaves_no_rows_behind(client, api_scratch_url, monkeypatch):
    import app.library.service as library_service

    class BrokenStorage:
        def put_file(self, *args, **kwargs):
            raise OSError("durable storage unavailable")

    _login(client, OWNER)
    monkeypatch.setattr(library_service, "get_storage", lambda settings: BrokenStorage())
    body = _submit(client, _pdf(), "native.pdf")
    assert body["outcome"] == "failed"
    assert "could not store this file" in body["message"]
    assert _fetchval(api_scratch_url, "SELECT count(*) FROM manuscript") == 0

    monkeypatch.undo()
    assert _submit(client, _pdf(), "native.pdf")["outcome"] == "added"


def test_a_record_number_is_searchable_but_names_stay_own_only(client):
    _login(client, OWNER)
    manuscript_id = _submit(client, _pdf(), "native.pdf", title="A Searchable Capstone")["item"][
        "manuscript_id"
    ]

    _login(client, STRANGER)
    by_number = client.get("/library", params={"search": f"#{manuscript_id}"}).json()
    assert [i["manuscript_id"] for i in by_number["items"]] == [manuscript_id]
    assert by_number["items"][0]["title"] is None
    assert client.get("/library", params={"search": "Searchable"}).json()["total"] == 0
