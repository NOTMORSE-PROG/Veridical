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
                    # BUG-147 (`backend-critic` finding): a real title,
                    # unchanged from before this fix -- without one, the
                    # redaction test can't tell "title was stripped" from
                    # "title was never set", since both look like `None`.
                    title="A Confidential Stranger Capstone Title",
                )
                session.add_all([own_group, stranger_group])
                await session.commit()
                session.add(GroupMember(group_id=own_group.id, name="Author One"))
                session.add(GroupMember(group_id=stranger_group.id, name="Maria Santos"))
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
                    original_filename="stranger_capstone.pdf",
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


def test_list_library_never_exposes_another_accounts_identity(seeded):
    """BUG-147 (Critical): `list_library` used to return every OTHER
    instructor's full team name, capstone title, member names, and
    original filename to every account with no consent step -- a Data
    Privacy Act (RA 10173) exposure, confirmed live. The shared corpus
    itself stays intentional (BUG-050) -- what changes is the PAYLOAD for
    a row this requester doesn't own: only a non-identifying reference,
    program, and date, exactly the ticket's own worked example ("archived
    manuscript #3, IT, Aug 2026")."""
    client, ids = seeded
    resp = client.get("/library")
    assert resp.status_code == 200, resp.text
    by_id = {item["manuscript_id"]: item for item in resp.json()["items"]}
    theirs = by_id[ids["theirs"]]
    assert theirs["is_own"] is False
    assert theirs["title"] is None
    assert theirs["authors"] == []
    assert theirs["original_filename"] is None
    assert theirs["group_label"] == f"Archived manuscript #{ids['theirs']}"
    assert "Stranger Group" not in theirs["group_label"]
    # Whole-payload check (`backend-critic` finding, BUG-147 review): the
    # enriched fixture now gives `theirs` a real title, member, and
    # filename, so these strings would appear SOMEWHERE in the raw
    # response if the fix regressed, even if not on the exact field this
    # test happens to check first.
    assert "Confidential Stranger" not in str(theirs)
    assert "Maria Santos" not in str(theirs)
    assert "stranger_capstone" not in str(theirs)
    # The requester's OWN row is completely unaffected by this fix.
    mine = by_id[ids["mine"]]
    assert mine["title"] == "A Real Capstone Title"
    assert mine["authors"] == ["Author One"]
    assert mine["group_label"] == "Own Group"


def test_list_library_filters_by_program(seeded):
    client, ids = seeded
    resp = client.get("/library?program=IT")
    assert resp.status_code == 200, resp.text
    ids_returned = {item["manuscript_id"] for item in resp.json()["items"]}
    assert ids_returned == {ids["mine"]}


def test_list_library_searches_group_title_and_author(seeded):
    """The search filter runs against the real stored `Group`/
    `GroupMember` rows server-side (never against the anonymized response
    payload), scoped to the CALLER's own manuscripts (BUG-147's search-
    scoping fix, see the adversarial test below for why)."""
    client, ids = seeded
    for q in ["Real Capstone", "Own Group", "Author One"]:
        resp = client.get(f"/library?search={q}")
        assert resp.status_code == 200, resp.text
        assert {item["manuscript_id"] for item in resp.json()["items"]} == {ids["mine"]}


def test_search_cannot_be_used_to_confirm_another_accounts_real_identity(seeded):
    """BUG-147 (`ux-critic` finding, live-reproduced before this fix): the
    Library screen's own redaction is worthless if an instructor who
    already suspects a name (a rumor, a cover page glimpsed elsewhere) can
    type it into Search and get a hit back with program and date attached
    -- that CONFIRMS the name exists in another account's manuscript even
    though the row itself never displays it. `owner` searches for
    `stranger`'s real, never-displayed identity (team name, capstone
    title, member name) and must get ZERO results, the same as searching
    for a name that doesn't exist anywhere at all."""
    client, ids = seeded
    for needle in [
        "Stranger Group",
        "Confidential Stranger",  # the redacted title
        "Maria Santos",  # the redacted member name
        "stranger_capstone",  # the redacted original filename
    ]:
        resp = client.get(f"/library?search={needle}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 0, (
            f"search={needle!r} returned a hit -- confirms another account's "
            "real identity exists even though the row itself is redacted"
        )
        assert body["items"] == []

    # A genuinely nonexistent name is indistinguishable from a real,
    # redacted one -- both correctly return nothing.
    control = client.get("/library?search=Zzyzx Nonexistent Name")
    assert control.status_code == 200, control.text
    assert control.json()["total"] == 0


def test_library_item_detail_visible_for_a_strangers_manuscript_too(seeded):
    """BUG-147: the RECORD is visible (identity-is-the-point-of-the-library,
    BUG-050's decided direction) but the record's real identity is not.
    `get_library_item` has its own field-population logic ahead of the
    shared `_item_out` (`backend-critic` finding, BUG-147 review) -- a
    separate code path from `list_library`'s, so it needs its own full
    assertion, not just `group_label`, or a future change that broke only
    THIS endpoint's redaction would go uncaught."""
    client, ids = seeded
    resp = client.get(f"/library/{ids['theirs']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_own"] is False
    assert body["title"] is None
    assert body["authors"] == []
    assert body["original_filename"] is None
    assert body["group_label"] == f"Archived manuscript #{ids['theirs']}"
    assert "Stranger Group" not in body["group_label"]
    assert "Confidential Stranger" not in str(body)
    assert "Maria Santos" not in str(body)
    assert "stranger_capstone" not in str(body)


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


# --- BUG-148: collapse the requester's own byte-identical re-uploads -----


@pytest.fixture()
def dup_seeded(client, api_scratch_url):
    """A dedicated, leaner seed for the content_hash collapsing behavior --
    no chapter/passage archives needed here, just manuscripts and their
    hashes/purge state, across two instructors so cross-tenant isolation
    has something real to differ against."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import CheckRunStatus, IngestStatus
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript
    from app.models.rubric import Rubric
    from app.models.run import CheckRun

    ids: dict[str, int] = {}

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "TRUNCATE check_run, rubric, manuscript_passage_archive, "
                        "manuscript_chapter_archive, manuscript_archive, group_member, "
                        "manuscript, manuscript_group, program, session, instructor "
                        "RESTART IDENTITY CASCADE"
                    )
                )
                owner = Instructor(
                    email="dupowner@tip.edu.ph",
                    display_name="Dup Owner",
                    password_hash=hash_password("s3cret!"),
                )
                other = Instructor(
                    email="dupother@tip.edu.ph",
                    display_name="Dup Other",
                    password_hash=hash_password("other!"),
                )
                session.add_all([owner, other])
                await session.commit()

                # owner: 3 uploads of the SAME bytes (hash-a), oldest to
                # newest, plus 1 unrelated upload (hash-b, no duplicates).
                oldest = Manuscript(
                    instructor_id=owner.id,
                    group_label="Group A",
                    file_ref="",
                    original_filename="draft-v1.pdf",
                    content_hash="hash-a",
                    ingest_status=IngestStatus.done,
                )
                middle_purged = Manuscript(
                    instructor_id=owner.id,
                    group_label="Group A",
                    file_ref="",
                    original_filename="draft-v1-copy.pdf",
                    content_hash="hash-a",
                    ingest_status=IngestStatus.done,
                )
                newest = Manuscript(
                    instructor_id=owner.id,
                    group_label="Group A",
                    file_ref="",
                    original_filename="draft-v1-final.pdf",
                    content_hash="hash-a",
                    ingest_status=IngestStatus.done,
                )
                unrelated = Manuscript(
                    instructor_id=owner.id,
                    group_label="Group B",
                    file_ref="",
                    original_filename="unrelated.pdf",
                    content_hash="hash-b",
                    ingest_status=IngestStatus.done,
                )
                other_same_bytes = Manuscript(
                    instructor_id=other.id,
                    group_label="Other's Group",
                    file_ref="",
                    original_filename="coincidence.pdf",
                    content_hash="hash-a",  # same bytes as owner's, different account
                    ingest_status=IngestStatus.done,
                )
                session.add_all([oldest, middle_purged, newest, unrelated, other_same_bytes])
                await session.commit()
                # Distinct, explicit created_at (all three would otherwise
                # tie, created in the same transaction) so "newest"/"oldest"
                # are real, not incidental id order -- and middle_purged is
                # marked purged directly, rather than routed through the
                # real DELETE /archive endpoint, since this fixture only
                # needs the resulting DB state, not the purge flow itself
                # (already covered by test_purged_manuscript_stays_visible).
                now = middle_purged.created_at
                oldest.created_at = now.replace(year=now.year - 1)
                middle_purged.created_at = now.replace(year=now.year - 1, month=6)
                middle_purged.purged_at = now
                await session.commit()

                # One completed check run, on the REPRESENTATIVE ("newest")
                # only -- proves `latest_done_check_run_id` reaches the
                # representative row itself, not just a hidden sibling
                # (`ux-critic` finding, BUG-148 review).
                rubric = Rubric(instructor_id=owner.id, title="Format", source_file="r.pdf")
                session.add(rubric)
                await session.commit()
                done_run = CheckRun(
                    manuscript_id=newest.id, rubric_id=rubric.id, status=CheckRunStatus.done
                )
                session.add(done_run)
                await session.commit()
                ids["done_run"] = done_run.id

                ids["oldest"] = oldest.id
                ids["middle_purged"] = middle_purged.id
                ids["newest"] = newest.id
                ids["unrelated"] = unrelated.id
                ids["other_same_bytes"] = other_same_bytes.id
        finally:
            await engine.dispose()

    asyncio.run(seed())
    client.post("/auth/login", json={"email": "dupowner@tip.edu.ph", "password": "s3cret!"})
    return client, ids


def test_collapses_byte_identical_reuploads_into_one_representative_row(dup_seeded):
    client, ids = dup_seeded
    resp = client.get("/library")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    manuscript_ids = {item["manuscript_id"] for item in body["items"]}
    # 3 duplicate-hash rows collapse to 1 (the newest), plus the unrelated
    # hash-b row and the OTHER instructor's own coincidental hash-a row
    # (never collapsed with owner's, see the isolation test below) -- 3
    # rows total, not 5.
    assert body["total"] == 3
    assert manuscript_ids == {ids["newest"], ids["unrelated"], ids["other_same_bytes"]}
    assert ids["oldest"] not in manuscript_ids
    assert ids["middle_purged"] not in manuscript_ids

    representative = next(item for item in body["items"] if item["manuscript_id"] == ids["newest"])
    assert representative["original_filename"] == "draft-v1-final.pdf"
    dup_ids = {dup["manuscript_id"] for dup in representative["duplicate_uploads"]}
    assert dup_ids == {ids["oldest"], ids["middle_purged"]}
    # The representative's own id never appears among its own duplicates.
    assert ids["newest"] not in dup_ids

    unrelated_item = next(
        item for item in body["items"] if item["manuscript_id"] == ids["unrelated"]
    )
    assert unrelated_item["duplicate_uploads"] is None


def test_all_purged_duplicate_group_falls_back_to_most_recent_overall(dup_seeded):
    """Representative-selection rule: not-yet-purged wins first: if the
    group's actual newest upload gets purged, an OLDER still-stored copy
    becomes representative rather than surfacing a purged, unopenable row
    as the group's face."""
    client, ids = dup_seeded
    purge = client.delete(f"/archive/{ids['newest']}")
    assert purge.status_code == 200, purge.text

    resp = client.get("/library")
    body = resp.json()
    manuscript_ids = {item["manuscript_id"] for item in body["items"]}
    # middle_purged was ALREADY purged in the seed; oldest is the only
    # still-stored instance left, so it becomes representative.
    assert ids["oldest"] in manuscript_ids
    assert ids["newest"] not in manuscript_ids
    assert ids["middle_purged"] not in manuscript_ids

    representative = next(item for item in body["items"] if item["manuscript_id"] == ids["oldest"])
    dup_ids = {dup["manuscript_id"] for dup in representative["duplicate_uploads"]}
    assert dup_ids == {ids["newest"], ids["middle_purged"]}


def test_content_hash_collapsing_never_applies_across_instructors(dup_seeded):
    """BUG-140's own same-instructor scoping, preserved here: two different
    accounts uploading byte-identical files must never merge into one
    entry, and neither account's response may even hint that the other
    uploaded the same bytes."""
    client, ids = dup_seeded
    resp = client.get("/library")
    body = resp.json()
    by_id = {item["manuscript_id"]: item for item in body["items"]}
    assert ids["other_same_bytes"] in by_id
    other_item = by_id[ids["other_same_bytes"]]
    assert other_item["is_own"] is False
    assert other_item["duplicate_uploads"] is None
    # The owner's own representative row's duplicates never include the
    # other instructor's manuscript id.
    representative = by_id[ids["newest"]]
    dup_ids = {dup["manuscript_id"] for dup in representative["duplicate_uploads"]}
    assert ids["other_same_bytes"] not in dup_ids


def test_total_count_reflects_the_collapsed_row_count_not_raw_manuscripts(dup_seeded):
    """Ground rule 8: a `total`/pagination count that still reflects the
    pre-collapse row count while fewer cards actually render is a number
    that lies, even though it isn't a percentage."""
    client, ids = dup_seeded
    resp = client.get("/library?page_size=1")
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1


def test_representative_row_carries_its_own_latest_done_check_run_id(dup_seeded):
    """`ux-critic` finding (BUG-148 review): a hidden duplicate-group
    sibling could link straight to its own completed report while the far
    more visible representative row could not -- confirms the fix reaches
    BOTH the list endpoint and the single-item detail endpoint, which
    share `_item_out` but populate this field via separate code paths."""
    client, ids = dup_seeded
    resp = client.get("/library")
    by_id = {item["manuscript_id"]: item for item in resp.json()["items"]}
    assert by_id[ids["newest"]]["latest_done_check_run_id"] == ids["done_run"]
    # The unrelated (no-duplicate) row has never been checked.
    assert by_id[ids["unrelated"]]["latest_done_check_run_id"] is None
    # Another instructor's row never discloses this fact either way.
    assert by_id[ids["other_same_bytes"]]["latest_done_check_run_id"] is None

    detail = client.get(f"/library/{ids['newest']}")
    assert detail.json()["latest_done_check_run_id"] == ids["done_run"]


@pytest.fixture()
def tied_timestamps_seeded(client, api_scratch_url):
    """`backend-critic` finding (BUG-148 review), live-reproduced against
    real Postgres: `created_at`'s `server_default=func.now()` returns the
    IDENTICAL value for every row inserted in the same transaction -- a
    real, common case for this corpus-wide endpoint (bulk seeding, or
    simply two uploads landing in the same second), not a contrived one.
    Without a tie-break, `ORDER BY created_at DESC` + OFFSET/LIMIT is not
    guaranteed stable across pages: the same row can repeat on multiple
    pages while another never appears at all. 5 manuscripts, same account,
    all committed in ONE transaction so their `created_at` genuinely ties."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url
    from app.models.enums import IngestStatus
    from app.models.instructor import Instructor
    from app.models.manuscript import Manuscript

    ids: list[int] = []

    async def seed():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "TRUNCATE check_run, rubric, manuscript_passage_archive, "
                        "manuscript_chapter_archive, manuscript_archive, group_member, "
                        "manuscript, manuscript_group, program, session, instructor "
                        "RESTART IDENTITY CASCADE"
                    )
                )
                owner = Instructor(
                    email="tiedowner@tip.edu.ph",
                    display_name="Tied Owner",
                    password_hash=hash_password("s3cret!"),
                )
                session.add(owner)
                await session.commit()

                manuscripts = [
                    Manuscript(
                        instructor_id=owner.id,
                        group_label=f"Group {i}",
                        file_ref="",
                        original_filename=f"paper-{i}.pdf",
                        ingest_status=IngestStatus.done,
                    )
                    for i in range(5)
                ]
                session.add_all(manuscripts)
                # ONE commit for all 5 -- this is what makes `created_at`
                # genuinely tie under Postgres's real `now()` semantics,
                # not a manually-forced equal timestamp.
                await session.commit()
                ids.extend(m.id for m in manuscripts)
        finally:
            await engine.dispose()

    asyncio.run(seed())
    client.post("/auth/login", json={"email": "tiedowner@tip.edu.ph", "password": "s3cret!"})
    return client, ids


def test_pagination_is_stable_when_created_at_ties(tied_timestamps_seeded):
    """Walk the whole listing one row per page and confirm every real id
    appears EXACTLY once across the walk -- not zero times (silently
    unreachable) and not more than once (a page boundary re-showing a row
    already seen), which is exactly what an unstable sort produces."""
    client, ids = tied_timestamps_seeded
    seen: list[int] = []
    page = 1
    while True:
        resp = client.get(f"/library?page_size=1&page={page}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if not body["items"]:
            break
        seen.append(body["items"][0]["manuscript_id"])
        if page >= body["total"]:
            break
        page += 1
    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(set(seen)), f"a row repeated across pages: {seen}"
