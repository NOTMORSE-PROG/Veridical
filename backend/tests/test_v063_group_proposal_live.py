"""V-063: the group-proposal HTTP surface end-to-end -- a real title-page
PDF, uploaded through the real ingest endpoint, gets a real deterministic
proposal back; confirming it (or dismissing and re-opening later) really
changes the manuscript's group.
"""

import os

import pytest
from fastapi.testclient import TestClient

import app.auth.service as auth_service
from app.auth.security import hash_password
from app.config import get_settings
from tests.test_ingest_pdf import PdfBuilder

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)

SCRATCH_DB = "veridical_groupproposaltest"


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
    import app.ratelimit as ratelimit
    from app.db import sqlalchemy_url
    from app.models.instructor import Instructor

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    db._engine = None
    auth_service._rate_limiter = None
    ratelimit._limiters.clear()
    from app.main import app

    async def seed_instructor():
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

    import asyncio

    asyncio.run(seed_instructor())

    with TestClient(app) as tc:
        tc.post("/auth/login", json={"email": "prof@tip.edu.ph", "password": "s3cret!"})
        yield tc
    db._engine = None
    get_settings.cache_clear()


def _title_page_pdf(tmp_path, name="titlepage.pdf", title="VERIDICAL: A Study"):
    b = PdfBuilder()
    b.new_page()
    b.line(title, size=16, bold=True)
    b.line("In Partial Fulfillment of the Requirements for the Degree of")
    b.line("Bachelor of Science in Information Technology")
    b.line("By:")
    b.line("Condino, Mark Andrei A")
    b.line("Concepcion, Marc Laurence M.")
    b.line("Adviser:")
    b.line("Mr. Jhon Angelo M. San Andres")
    return b.save(tmp_path / name)


@live
def test_ingest_response_carries_a_real_group_proposal(client, tmp_path):
    path = _title_page_pdf(tmp_path)
    r = client.post(
        "/manuscripts/ingest",
        files={"file": ("titlepage.pdf", path.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    proposal = r.json()["group_proposal"]
    assert proposal["extraction_failed"] is False
    assert proposal["short_name"]["value"] == "VERIDICAL"
    assert proposal["short_name"]["anchor"] == "p. 1"
    assert proposal["program"]["value"] == "IT"
    assert [m["value"] for m in proposal["members"]] == [
        "Condino, Mark Andrei A",
        "Concepcion, Marc Laurence M.",
    ]


@live
def test_get_group_proposal_re_derives_the_same_proposal_later(client, tmp_path):
    """AC6: dismissing the dialog must not be a dead end -- the SAME
    proposal is re-derivable any time later, not just at upload time."""
    path = _title_page_pdf(tmp_path)
    upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("titlepage.pdf", path.read_bytes(), "application/pdf")},
    )
    manuscript_id = upload.json()["manuscript_id"]

    later = client.get(f"/manuscripts/{manuscript_id}/group-proposal")
    assert later.status_code == 200, later.text
    assert later.json() == upload.json()["group_proposal"]


@live
def test_reopening_set_group_after_confirming_shows_the_current_group_not_the_stale_extraction(
    client, tmp_path
):
    """ux-critic (V-063 review), reproduced live: reopening used to
    silently show the ORIGINAL extraction again, discarding an
    instructor's own already-confirmed correction -- e.g. correcting a
    garbled member list, then reopening later, must show the CORRECTED
    state, not resurrect the original garbled one."""
    path = _title_page_pdf(tmp_path)
    upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("titlepage.pdf", path.read_bytes(), "application/pdf")},
    )
    manuscript_id = upload.json()["manuscript_id"]

    # The instructor corrects the extraction on confirm -- drops one
    # extracted member, adds a member the extraction never saw at all,
    # and renames the group.
    client.patch(
        f"/manuscripts/{manuscript_id}/group",
        json={
            "group_name": "VERIDICAL CORRECTED",
            "member_names": ["Condino, Mark Andrei A", "A Member The Extraction Missed"],
            "program_id": None,
        },
    )

    reopened = client.get(f"/manuscripts/{manuscript_id}/group-proposal")
    assert reopened.status_code == 200, reopened.text
    proposal = reopened.json()

    assert proposal["short_name"]["value"] == "VERIDICAL CORRECTED"
    assert proposal["short_name"]["anchor"] == "current group"
    member_values = {m["value"] for m in proposal["members"]}
    assert member_values == {"Condino, Mark Andrei A", "A Member The Extraction Missed"}
    assert all(m["anchor"] == "current group" for m in proposal["members"])
    # The corrected state is shown -- the ORIGINAL extraction's own
    # (uncorrected) second member must NOT reappear.
    assert "Concepcion, Marc Laurence M." not in member_values


@live
def test_confirming_a_new_proposal_creates_a_group_and_sets_the_program(client, tmp_path):
    path = _title_page_pdf(tmp_path)
    upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("titlepage.pdf", path.read_bytes(), "application/pdf")},
    )
    manuscript_id = upload.json()["manuscript_id"]
    programs = {p["name"]: p["id"] for p in client.get("/programs").json()}

    confirm = client.patch(
        f"/manuscripts/{manuscript_id}/group",
        json={
            "group_name": "VERIDICAL",
            "member_names": ["Condino, Mark Andrei A", "Concepcion, Marc Laurence M."],
            "program_id": programs["IT"],
        },
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["matched"] is False  # brand new group
    assert body["group_label"] == "VERIDICAL"
    assert body["program"] == "IT"

    manuscripts = client.get("/manuscripts").json()
    row = next(i for i in manuscripts["items"] if i["id"] == manuscript_id)
    assert row["group_label"] == "VERIDICAL"
    assert row["program"] == "IT"


@live
def test_a_resubmission_with_overlapping_members_matches_the_existing_group(client, tmp_path):
    """AC4: matches, doesn't fork a duplicate."""
    first_path = _title_page_pdf(tmp_path, name="first.pdf", title="VERIDICAL: First Version")
    first_upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("first.pdf", first_path.read_bytes(), "application/pdf")},
    )
    first_id = first_upload.json()["manuscript_id"]
    client.patch(
        f"/manuscripts/{first_id}/group",
        json={
            "group_name": "VERIDICAL",
            "member_names": ["Condino, Mark Andrei A", "Concepcion, Marc Laurence M."],
            "program_id": None,
        },
    )

    second_path = _title_page_pdf(tmp_path, name="second.pdf", title="VERIDICAL - Revised")
    second_upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("second.pdf", second_path.read_bytes(), "application/pdf")},
    )
    second_id = second_upload.json()["manuscript_id"]
    confirm = client.patch(
        f"/manuscripts/{second_id}/group",
        json={
            "group_name": "VERIDICAL",
            "member_names": ["Condino, Mark Andrei A"],
            "program_id": None,
        },
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["matched"] is True

    manuscripts = client.get("/manuscripts").json()
    first_row = next(i for i in manuscripts["items"] if i["id"] == first_id)
    second_row = next(i for i in manuscripts["items"] if i["id"] == second_id)
    assert first_row["group_label"] == second_row["group_label"] == "VERIDICAL"


@live
def test_confirming_a_match_never_overwrites_the_existing_groups_program(client, tmp_path):
    """backend-critic (V-063 review): the code already enforces
    `if not matched and program_id is not None` -- this was previously
    unguarded by any test, so a future refactor could silently regress
    the one property this exact rule exists for."""
    first_path = _title_page_pdf(tmp_path, name="first.pdf", title="VERIDICAL: First Version")
    first_upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("first.pdf", first_path.read_bytes(), "application/pdf")},
    )
    first_id = first_upload.json()["manuscript_id"]
    programs = {p["name"]: p["id"] for p in client.get("/programs").json()}
    client.patch(
        f"/manuscripts/{first_id}/group",
        json={
            "group_name": "VERIDICAL",
            "member_names": ["Condino, Mark Andrei A", "Concepcion, Marc Laurence M."],
            "program_id": programs["IT"],
        },
    )

    second_path = _title_page_pdf(tmp_path, name="second.pdf", title="VERIDICAL - Revised")
    second_upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("second.pdf", second_path.read_bytes(), "application/pdf")},
    )
    second_id = second_upload.json()["manuscript_id"]
    confirm = client.patch(
        f"/manuscripts/{second_id}/group",
        json={
            "group_name": "VERIDICAL",
            "member_names": ["Condino, Mark Andrei A"],
            "program_id": programs["CS"],
        },
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["matched"] is True
    # The FIRST program (IT) survives -- the second submission's own
    # (different) program_id is never applied to an already-existing group.
    assert body["program"] == "IT"


@live
def test_confirming_persists_the_extracted_title(client, tmp_path):
    """V-066: `TitlePageProposal.title` used to be shown in the confirm
    dialog and then discarded -- this is the first thing that ever
    persists it, so the library screen has something real to show.

    Deliberately a distinct group name/members from every other test in
    this module (module-scoped shared DB, `api_scratch_url`): "VERIDICAL"
    with overlapping members is used by several earlier tests, and reusing
    it here would silently MATCH into their already-created group instead
    of creating a fresh one, making this test depend on file execution
    order rather than on its own setup."""
    path = _title_page_pdf(tmp_path, name="titletest1.pdf", title="TITLETEST: A Study")
    upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("titletest1.pdf", path.read_bytes(), "application/pdf")},
    )
    manuscript_id = upload.json()["manuscript_id"]
    extracted_title = upload.json()["group_proposal"]["title"]["value"]
    assert extracted_title == "TITLETEST: A Study"

    confirm = client.patch(
        f"/manuscripts/{manuscript_id}/group",
        json={
            "group_name": "TITLETEST-PERSIST",
            "member_names": ["Some Member"],
            "program_id": None,
            "title": extracted_title,
        },
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["matched"] is False  # brand new group, own unique name
    assert confirm.json()["title"] == "TITLETEST: A Study"


@live
def test_confirming_a_match_never_overwrites_the_existing_groups_title(client, tmp_path):
    """Same rule as the program test above, same reason: a second
    manuscript matched into an existing group carries its OWN title page,
    which is not necessarily the group's. Own unique group name, same
    isolation reasoning as the test above."""
    first_path = _title_page_pdf(tmp_path, name="first-tt.pdf", title="TITLETEST: First Version")
    first_upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("first-tt.pdf", first_path.read_bytes(), "application/pdf")},
    )
    first_id = first_upload.json()["manuscript_id"]
    first_confirm = client.patch(
        f"/manuscripts/{first_id}/group",
        json={
            "group_name": "TITLETEST-NOOVERWRITE",
            "member_names": ["Titletest Member A", "Titletest Member B"],
            "program_id": None,
            "title": "TITLETEST: First Version",
        },
    )
    assert first_confirm.json()["matched"] is False

    second_path = _title_page_pdf(tmp_path, name="second-tt.pdf", title="TITLETEST - Revised")
    second_upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("second-tt.pdf", second_path.read_bytes(), "application/pdf")},
    )
    second_id = second_upload.json()["manuscript_id"]
    confirm = client.patch(
        f"/manuscripts/{second_id}/group",
        json={
            "group_name": "TITLETEST-NOOVERWRITE",
            "member_names": ["Titletest Member A"],
            "program_id": None,
            "title": "TITLETEST - Revised",
        },
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["matched"] is True
    assert confirm.json()["title"] == "TITLETEST: First Version"


@live
def test_confirming_with_an_unknown_program_id_is_a_clean_404_not_a_bare_500(client, tmp_path):
    """backend-critic (V-063 review): reproduced live as an unhandled
    `IntegrityError`/`ForeignKeyViolationError` before this guard existed
    (`app/ingest/service.py::confirm_manuscript_group`, matching the
    established pattern in `rubric/service.py::set_rubric_family_program`)."""
    path = _title_page_pdf(tmp_path)
    upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("titlepage.pdf", path.read_bytes(), "application/pdf")},
    )
    manuscript_id = upload.json()["manuscript_id"]

    confirm = client.patch(
        f"/manuscripts/{manuscript_id}/group",
        json={
            "group_name": "VERIDICAL",
            "member_names": ["Condino, Mark Andrei A"],
            "program_id": 999999,
        },
    )
    assert confirm.status_code == 404, confirm.text
    assert confirm.json()["error"]["code"] == "not_found"


@live
def test_collision_check_warns_before_confirming_a_rule3_collision(client, tmp_path):
    """Owner's call (2026-08-19): the confirm form calls this WHILE the
    instructor edits, so a rule-3 collision (same short name, zero member
    overlap) is disclosed before they confirm, not only after via a
    disambiguating suffix."""
    path = _title_page_pdf(tmp_path)
    upload = client.post(
        "/manuscripts/ingest",
        files={"file": ("titlepage.pdf", path.read_bytes(), "application/pdf")},
    )
    manuscript_id = upload.json()["manuscript_id"]
    client.patch(
        f"/manuscripts/{manuscript_id}/group",
        json={
            "group_name": "VERIDICAL",
            "member_names": ["Condino, Mark Andrei A"],
            "program_id": None,
        },
    )

    no_collision = client.get(
        "/groups/collision-check",
        params={"short_name": "VERIDICAL", "member_names": ["Condino, Mark Andrei A"]},
    )
    assert no_collision.status_code == 200, no_collision.text
    assert no_collision.json() == {"collision": False, "existing_group_name": None}

    collision = client.get(
        "/groups/collision-check",
        params={"short_name": "VERIDICAL", "member_names": ["A Different Person Entirely"]},
    )
    assert collision.status_code == 200, collision.text
    assert collision.json() == {"collision": True, "existing_group_name": "VERIDICAL"}


@live
def test_group_and_program_endpoints_require_auth(api_scratch_url, tmp_path, monkeypatch):
    import app.db as db

    monkeypatch.setenv("DATABASE_URL", api_scratch_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    db._engine = None
    from app.main import app

    with TestClient(app) as tc:
        assert tc.get("/manuscripts/1/group-proposal").status_code == 401
        assert tc.patch("/manuscripts/1/group", json={"group_name": "X"}).status_code == 401
        assert tc.get("/groups/collision-check", params={"short_name": "X"}).status_code == 401
    db._engine = None
    get_settings.cache_clear()


@live
def test_image_only_first_page_returns_the_honest_extraction_failed_state(client, tmp_path):
    """AC5: never a fabricated proposal or a silent 'Ungrouped' default."""
    from tests.test_ingest_pdf import PdfBuilder as _Builder

    b = _Builder()
    b.new_page().image()  # no extractable text at all on the title page
    path = b.save(tmp_path / "scan.pdf")

    r = client.post(
        "/manuscripts/ingest",
        files={"file": ("scan.pdf", path.read_bytes(), "application/pdf")},
    )
    assert r.status_code == 200, r.text
    proposal = r.json()["group_proposal"]
    assert proposal["extraction_failed"] is True
    assert proposal["short_name"] is None
