"""V-008: the ingestion HTTP surface — taxonomy end-to-end.

Needs a live Postgres (same DATABASE_URL convention as the other
integration suites); runs against its own scratch database.
"""

import os

import pytest
from fastapi.testclient import TestClient

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.config import get_settings
from tests.test_ingest_fixtures import FIXTURE_DIR

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)

SCRATCH_DB = "veridical_apitest"


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
    """App wired to the scratch DB, a temp data dir, and fake-LLM mode —
    logged in as a seeded instructor (BUG-002/D-020: /manuscripts/ingest
    now requires auth, same as every other manuscript-scoped route)."""
    import asyncio

    import app.db as db
    import app.ratelimit as ratelimit
    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None  # drop any engine bound to another database
    auth_service._rate_limiter = None
    ratelimit._limiters.clear()  # don't leak ingest-rate-limit counts across tests
    from app.main import app

    async def seed_instructor():
        # api_scratch_url is module-scoped (not truncated between tests,
        # matching this file's prior convention) — find-or-create so a
        # repeat email doesn't violate the unique constraint on a 2nd test.
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                existing = await session.scalar(
                    select(Instructor).where(Instructor.email == "prof@tip.edu.ph")
                )
                if existing is None:
                    session.add(
                        Instructor(
                            email="prof@tip.edu.ph",
                            display_name="Prof",
                            password_hash=hash_password("s3cret!"),
                        )
                    )
                    await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed_instructor())

    with TestClient(app) as tc:
        tc.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
        yield tc
    db._engine = None
    get_settings.cache_clear()


def _upload(client, fixture_name: str, as_name: str | None = None, data: bytes | None = None):
    content = data if data is not None else (FIXTURE_DIR / fixture_name).read_bytes()
    return client.post(
        "/manuscripts/ingest",
        files={"file": (as_name or fixture_name, content, "application/octet-stream")},
    )


@live
def test_upload_pdf_returns_summary(client):
    r = _upload(client, "native.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingest_status"] == "done"
    assert body["page_count"] == 6 and body["anchor_kind"] == "page"
    assert body["citations"] == 2
    assert body["section_tree"]["source"] == "heuristics"
    assert body["notes"] == []


@live
def test_two_ungrouped_manuscripts_are_distinguishable_by_filename(client):
    """BUG-022: group_label defaults to "Ungrouped" for any manuscript not
    explicitly grouped, so two such uploads were indistinguishable in
    every picker/list. The instructor's own filename (already sent on
    every upload, previously discarded) now survives instead."""
    r1 = _upload(client, "native.pdf", as_name="Chapter1-3_FinalDefense.pdf")
    r2 = _upload(client, "native.pdf", as_name="Chapter1-3_Revised.pdf")
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    body = client.get("/manuscripts").json()
    by_filename = {item["original_filename"]: item for item in body["items"]}
    assert "Chapter1-3_FinalDefense.pdf" in by_filename
    assert "Chapter1-3_Revised.pdf" in by_filename
    assert by_filename["Chapter1-3_FinalDefense.pdf"]["group_label"] == "Ungrouped"
    assert by_filename["Chapter1-3_Revised.pdf"]["group_label"] == "Ungrouped"
    assert (
        by_filename["Chapter1-3_FinalDefense.pdf"]["id"]
        != by_filename["Chapter1-3_Revised.pdf"]["id"]
    )


@live
def test_group_label_sent_as_form_field_is_persisted(client):
    """BUG-043: a bare scalar FastAPI parameter is a QUERY parameter, not a
    form field, even on a multipart endpoint. A client that sends
    `group_label` as a form field alongside the file -- the conventional
    thing to do, since the file is already multipart -- had it silently
    discarded; the manuscript was created with the default "Ungrouped" and
    nothing in the response said so."""
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    r = client.post(
        "/manuscripts/ingest",
        files={"file": ("native.pdf", content, "application/octet-stream")},
        data={"group_label": "FORM-FIELD-TEST"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["group_label"] == "FORM-FIELD-TEST"

    body = client.get("/manuscripts").json()
    item = next(i for i in body["items"] if i["id"] == r.json()["manuscript_id"])
    assert item["group_label"] == "FORM-FIELD-TEST"


@live
def test_group_label_query_param_still_works_for_one_release(client):
    """BUG-043's fix: the query-param path (the only one that worked
    before this fix, and the shape the frontend used as its documented
    workaround) must keep working for one release so an in-flight client
    isn't broken mid-deploy."""
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    r = client.post(
        "/manuscripts/ingest?group_label=QUERY-PARAM-TEST",
        files={"file": ("native.pdf", content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["group_label"] == "QUERY-PARAM-TEST"


@live
def test_group_label_form_field_wins_over_conflicting_query_param(client):
    """BUG-043: a form field doesn't leak into logs/URLs the way a query
    parameter does, so when a client sends both (e.g. mid-migration), the
    form field -- the one this fix exists to make work -- takes
    precedence."""
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    r = client.post(
        "/manuscripts/ingest?group_label=QUERY-PARAM-VALUE",
        files={"file": ("native.pdf", content, "application/octet-stream")},
        data={"group_label": "FORM-FIELD-VALUE"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["group_label"] == "FORM-FIELD-VALUE"


@live
def test_group_label_resolves_case_insensitively_to_one_group(client):
    """V-062 AC1: "Group 4" and "group 4" must resolve to the SAME group
    -- the first spelling submitted wins as the display name."""
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    r1 = client.post(
        "/manuscripts/ingest",
        files={"file": ("a.pdf", content, "application/octet-stream")},
        data={"group_label": "Group 4"},
    )
    r2 = client.post(
        "/manuscripts/ingest",
        files={"file": ("b.pdf", content, "application/octet-stream")},
        data={"group_label": "group   4"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    # Second submission's group_label is the FIRST spelling, not its own.
    assert r1.json()["group_label"] == "Group 4"
    assert r2.json()["group_label"] == "Group 4"

    groups = client.get("/groups").json()
    matching = [g for g in groups if g["name"] == "Group 4"]
    assert len(matching) == 1, f"expected exactly one 'Group 4' row, got {groups!r}"
    assert matching[0]["program"] is None  # never guessed


@live
def test_new_groups_program_is_null_until_set_and_filters_correctly(client):
    """V-062 AC5: `program` is None ("Not set") for a freshly-created
    group, and GET /manuscripts?program=... only returns manuscripts
    whose group has that program assigned -- never a guess."""
    r = _upload(client, "native.pdf")
    manuscript_id = r.json()["manuscript_id"]

    body = client.get("/manuscripts").json()
    item = next(i for i in body["items"] if i["id"] == manuscript_id)
    assert item["program"] is None

    # No group has a program set yet, so filtering by any real program
    # name excludes this (and every other) manuscript -- not a guess.
    filtered = client.get("/manuscripts?program=CS").json()
    assert manuscript_id not in {i["id"] for i in filtered["items"]}


@live
def test_get_programs_lists_the_seeded_reference_data(client):
    programs = client.get("/programs").json()
    assert {p["name"] for p in programs} == {"CS", "IT"}


@live
def test_filename_with_control_characters_never_500s(client):
    """BUG-022 review (backend-critic): a NUL byte in the uploaded
    filename previously reached Postgres unfiltered and 500'd (UTF8
    encoding rejects NUL outright) before the ingest even got a chance
    to run — this is a client-input taxonomy gap, not a real file
    problem, so it must never surface as an unhandled 500.

    `TestClient`'s `files=` convenience helper percent-encodes control
    characters in the filename before they ever leave the client, which
    would mask this exact bug — a raw multipart body is built by hand so
    an actual NUL byte reaches the server, the way backend-critic's own
    live repro did."""
    content = (FIXTURE_DIR / "native.pdf").read_bytes()
    boundary = "boundary123"
    body_bytes = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="evil\x00name\x07.pdf"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8", errors="surrogateescape")
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )

    r = client.post(
        "/manuscripts/ingest",
        content=body_bytes,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert r.status_code == 200, r.text

    manuscripts = client.get("/manuscripts").json()
    item = next(i for i in manuscripts["items"] if i["id"] == r.json()["manuscript_id"])
    assert "\x00" not in (item["original_filename"] or "")
    assert item["original_filename"] == "evilname.pdf"


@live
def test_windows_style_path_in_filename_is_stripped_on_linux(client):
    """BUG-022 review (backend-critic): bare Path(...).name only splits
    on the HOST's own separator -- production runs on Linux, so a raw
    Windows-style path from a non-browser client would otherwise pass
    through untouched and be stored (and shown to the instructor) in
    full, including whatever local directory structure it revealed."""
    r = _upload(client, "native.pdf", as_name="C:\\Users\\student\\Desktop\\Thesis_Final.pdf")
    assert r.status_code == 200, r.text

    body = client.get("/manuscripts").json()
    item = next(i for i in body["items"] if i["id"] == r.json()["manuscript_id"])
    assert item["original_filename"] == "Thesis_Final.pdf"


@live
def test_malformed_upload_is_a_422_not_a_500(client):
    r = _upload(client, "malformed.pdf")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "file_malformed"
    assert "re-export" in err["message"]  # user-fixable wording, no stack trace


@live
def test_oversized_upload_rejected_early_with_413(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    r = _upload(client, "native.pdf", data=b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413
    err = r.json()["error"]
    assert err["code"] == "too_large" and "1 MB" in err["message"]


@live
def test_docx_renamed_to_pdf_is_sniffed_and_parsed(client):
    r = _upload(client, "docx_renamed.pdf")
    assert r.status_code == 200
    assert r.json()["anchor_kind"] == "paragraph"  # parsed as the DOCX it is


@live
def test_image_only_scan_continues_with_limited_checks_note(client):
    r = _upload(client, "scan_imageonly.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingest_status"] == "done"  # pipeline CONTINUED (F1.7)
    assert body["image_only"] is True
    assert any("limited" in note for note in body["notes"])


@live
def test_encrypted_pdf_is_a_422_with_unlock_message(client):
    r = _upload(client, "encrypted.pdf")
    assert r.status_code == 422
    assert "password-protected" in r.json()["error"]["message"]


@live
def test_legacy_doc_names_the_unsupported_type(client):
    r = _upload(client, "legacy.doc")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "file_malformed" and "'.doc'" in err["message"]
