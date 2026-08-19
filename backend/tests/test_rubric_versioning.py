"""V-013: rubric versioning (F2.4) — re-upload into an existing family,
activate, list families/versions, delete-blocked-by-reports, edit-
blocked-on-an-active-version-with-reports. Needs a live Postgres (same
convention as test_rubric_service.py); runs against its own scratch DB.
"""

import os
from typing import Any

import pytest
from sqlalchemy import select, text

from app.config import get_settings
from app.errors import ConflictError, NotFoundError
from app.llm.base import LLMClient
from app.models.group import Program
from app.models.manuscript import Manuscript
from app.models.rubric import Rubric
from app.models.run import CheckRun
from tests.test_ingest_pdf import PdfBuilder

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_rubricversiontest"

SCRIPTED_RESPONSE = {
    "criteria": [
        {
            "text": "Has an abstract",
            "type": "structural",
            "evidence_needed": "Manuscript must include an abstract section",
            "weight": 5,
        },
        {
            "text": "Argument is well developed",
            "type": "semantic",
            "evidence_needed": "Chapter 4 argument well developed",
            "weight": 15,
        },
    ]
}


class SpyLLM(LLMClient):
    def __init__(self, response: dict[str, Any]):
        self.calls = 0
        self._response = response

    async def complete(
        self, prompt_type: str, prompt: str, *, prompt_version: str = "unversioned", **context: Any
    ) -> dict[str, Any]:
        self.calls += 1
        return self._response


@pytest.fixture(scope="module")
def versioning_scratch_url():
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
def session_factory(versioning_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(versioning_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    """The scratch DB is module-scoped; every test needs a clean slate for
    family/version counts (and, since BUG-001/D-020, a fresh instructor id
    to seed each test's owner against)."""
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE check_run, criterion, rubric, manuscript, instructor "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


@pytest.fixture()
async def instructor_id(session_factory, _clean_tables) -> int:
    """This test's owner — seeded fresh (after truncate) per test. Async
    (not `asyncio.run()`-wrapped): a separate event loop touching the same
    engine as the test body corrupts it across loops on Windows — this
    exact bug is already documented in `_clean_tables`'s docstring above."""
    from app.models.instructor import Instructor

    async with session_factory() as session:
        instructor = Instructor(email="prof@tip.edu.ph", display_name="Prof")
        session.add(instructor)
        await session.commit()
        await session.refresh(instructor)
        return instructor.id


def _rubric_pdf(tmp_path, name="rubric.pdf"):
    b = PdfBuilder()
    b.new_page().line("FORMAT CHECKLIST", bold=True)
    b.line("The manuscript must include an abstract.")
    b.line("The argument in Chapter 4 must be well developed.")
    return b.save(tmp_path / name)


async def _chunks(path):
    yield path.read_bytes()


async def _upload(
    session_factory, tmp_path, monkeypatch, instructor_id, *, family_id=None, title="Rubric"
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()
    path = _rubric_pdf(tmp_path, f"{title}.pdf")
    async with session_factory() as session:
        from app.rubric.service import create_rubric_from_upload

        return await create_rubric_from_upload(
            session,
            _chunks(path),
            f"{title}.pdf",
            title,
            SpyLLM(SCRIPTED_RESPONSE),
            settings,
            instructor_id=instructor_id,
            family_id=family_id,
        )


async def _confirm(session_factory, rubric_id, instructor_id):
    from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest
    from app.rubric.service import get_rubric, update_criteria

    async with session_factory() as session:
        rubric = await get_rubric(session, rubric_id, instructor_id)
        criteria = [
            CriterionIn(id=c.id, type=c.type, text=c.text, evidence=c.evidence, weight=c.weight)
            for c in rubric.criteria
        ]
        return await update_criteria(
            session,
            rubric_id,
            UpdateCriteriaRequest(criteria=criteria, confirm=True),
            instructor_id,
        )


async def test_reupload_with_family_id_creates_v2_and_leaves_v1_untouched(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    await _confirm(session_factory, v1.id, instructor_id)

    v2 = await _upload(
        session_factory,
        tmp_path,
        monkeypatch,
        instructor_id,
        family_id=v1.rubric_family_id,
        title="V2",
    )

    assert v2.rubric_family_id == v1.rubric_family_id
    assert v2.version == 2

    async with session_factory() as session:
        v1_reloaded = (await session.execute(select(Rubric).where(Rubric.id == v1.id))).scalar_one()
    assert v1_reloaded.version == 1
    assert v1_reloaded.title == "V1"  # untouched by the v2 upload


async def test_reupload_into_unknown_family_raises_not_found(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    import uuid

    with pytest.raises(NotFoundError):
        await _upload(session_factory, tmp_path, monkeypatch, instructor_id, family_id=uuid.uuid4())


async def test_reupload_into_another_instructors_family_raises_not_found(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    """BUG-001 regression: a family_id guessed from another instructor's
    rubric must not be reachable — reads exactly like an unknown family_id."""
    from app.models.instructor import Instructor

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")

    async with session_factory() as session:
        other = Instructor(email="other@tip.edu.ph", display_name="Other Prof")
        session.add(other)
        await session.commit()
        await session.refresh(other)
        other_instructor_id = other.id

    with pytest.raises(NotFoundError):
        await _upload(
            session_factory,
            tmp_path,
            monkeypatch,
            other_instructor_id,
            family_id=v1.rubric_family_id,
            title="V2",
        )


async def test_activating_v2_deactivates_v1_exactly_one_active_per_family(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.rubric.service import activate_rubric, get_rubric

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    await _confirm(session_factory, v1.id, instructor_id)  # v1 active
    v2 = await _upload(
        session_factory,
        tmp_path,
        monkeypatch,
        instructor_id,
        family_id=v1.rubric_family_id,
        title="V2",
    )

    async with session_factory() as session:
        await activate_rubric(session, v2.id, instructor_id)

    async with session_factory() as session:
        v1_after = await get_rubric(session, v1.id, instructor_id)
        v2_after = await get_rubric(session, v2.id, instructor_id)
    assert v1_after.is_active is False
    assert v2_after.is_active is True


async def test_confirming_v2_also_deactivates_v1(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    """Same invariant, reached via the OTHER activation path (V-012's
    confirm, not V-013's explicit Activate button)."""
    from app.rubric.service import get_rubric

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    await _confirm(session_factory, v1.id, instructor_id)
    v2 = await _upload(
        session_factory,
        tmp_path,
        monkeypatch,
        instructor_id,
        family_id=v1.rubric_family_id,
        title="V2",
    )
    await _confirm(session_factory, v2.id, instructor_id)

    async with session_factory() as session:
        v1_after = await get_rubric(session, v1.id, instructor_id)
    assert v1_after.is_active is False


async def test_is_latest_version_flips_when_a_new_version_is_uploaded(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.rubric.service import get_rubric

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    async with session_factory() as session:
        assert (await get_rubric(session, v1.id, instructor_id)).is_latest_version is True

    v2 = await _upload(
        session_factory,
        tmp_path,
        monkeypatch,
        instructor_id,
        family_id=v1.rubric_family_id,
        title="V2",
    )
    async with session_factory() as session:
        assert (await get_rubric(session, v1.id, instructor_id)).is_latest_version is False
        assert (await get_rubric(session, v2.id, instructor_id)).is_latest_version is True


async def test_list_rubric_versions_orders_newest_first_with_counts(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.rubric.service import list_rubric_versions

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    await _confirm(session_factory, v1.id, instructor_id)
    v2 = await _upload(
        session_factory,
        tmp_path,
        monkeypatch,
        instructor_id,
        family_id=v1.rubric_family_id,
        title="V2",
    )

    async with session_factory() as session:
        versions = await list_rubric_versions(session, v1.rubric_family_id, instructor_id)

    assert [v.version for v in versions] == [2, 1]
    assert versions[1].is_active is True  # v1
    assert versions[1].criteria_count == 2
    assert versions[1].report_count == 0
    assert versions[0].id == v2.id


async def test_list_rubric_versions_raises_not_found_for_another_instructor(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    """BUG-001 regression on the versions-list route specifically."""
    from app.models.instructor import Instructor
    from app.rubric.service import list_rubric_versions

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")

    async with session_factory() as session:
        other = Instructor(email="other2@tip.edu.ph", display_name="Other Prof 2")
        session.add(other)
        await session.commit()
        await session.refresh(other)
        other_instructor_id = other.id

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await list_rubric_versions(session, v1.rubric_family_id, other_instructor_id)


async def test_list_rubric_families_returns_one_row_per_family(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.rubric.service import list_rubric_families

    cs = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="CS-Format")
    await _confirm(session_factory, cs.id, instructor_id)
    it = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="IT-Format")
    await _confirm(session_factory, it.id, instructor_id)

    async with session_factory() as session:
        families = await list_rubric_families(session, cs.instructor_id)

    family_ids = {f.rubric_family_id for f in families}
    assert family_ids == {cs.rubric_family_id, it.rubric_family_id}  # two independent families
    assert all(f.is_active for f in families)


async def test_two_simultaneously_active_families_carry_distinct_programs(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    """V-064's own QA step: two REAL simultaneously-active families,
    scoped to two different real programs -- not just schema-level
    plumbing, the exact shape an instructor with a CS format and an IT
    format actually has."""
    from app.rubric.service import list_rubric_families, set_rubric_family_program

    cs_rubric = await _upload(
        session_factory, tmp_path, monkeypatch, instructor_id, title="CS-Format"
    )
    await _confirm(session_factory, cs_rubric.id, instructor_id)
    it_rubric = await _upload(
        session_factory, tmp_path, monkeypatch, instructor_id, title="IT-Format"
    )
    await _confirm(session_factory, it_rubric.id, instructor_id)

    async with session_factory() as session:
        cs_id = await session.scalar(select(Program.id).where(Program.name == "CS"))
        it_id = await session.scalar(select(Program.id).where(Program.name == "IT"))
        await set_rubric_family_program(session, cs_rubric.rubric_family_id, cs_id, instructor_id)
        await set_rubric_family_program(session, it_rubric.rubric_family_id, it_id, instructor_id)

        families = await list_rubric_families(session, instructor_id)

    program_by_family = {f.rubric_family_id: f.program for f in families}
    assert program_by_family[cs_rubric.rubric_family_id] == "CS"
    assert program_by_family[it_rubric.rubric_family_id] == "IT"
    # Both families still independently active -- setting a program never
    # touches is_active (a genuinely separate concern).
    assert all(f.is_active for f in families)


async def _insert_check_run_against(session_factory, rubric_id, instructor_id):
    async with session_factory() as session:
        manuscript = Manuscript(
            instructor_id=instructor_id, group_label="Group 1", file_ref="x.pdf"
        )
        session.add(manuscript)
        await session.flush()
        session.add(CheckRun(manuscript_id=manuscript.id, rubric_id=rubric_id))
        await session.commit()


async def test_delete_blocked_when_reports_exist_allowed_otherwise(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.rubric.service import delete_rubric, get_rubric

    with_report = await _upload(
        session_factory, tmp_path, monkeypatch, instructor_id, title="HasReports"
    )
    without_report = await _upload(
        session_factory, tmp_path, monkeypatch, instructor_id, title="NoReports"
    )
    await _insert_check_run_against(session_factory, with_report.id, instructor_id)

    async with session_factory() as session:
        with pytest.raises(ConflictError):
            await delete_rubric(session, with_report.id, instructor_id)

    async with session_factory() as session:
        await delete_rubric(session, without_report.id, instructor_id)  # no reports -> succeeds
        with pytest.raises(NotFoundError):
            await get_rubric(session, without_report.id, instructor_id)


async def test_editing_an_active_version_with_reports_is_blocked(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest
    from app.rubric.service import get_rubric, update_criteria

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    await _confirm(session_factory, v1.id, instructor_id)  # now active
    await _insert_check_run_against(session_factory, v1.id, instructor_id)

    async with session_factory() as session:
        rubric = await get_rubric(session, v1.id, instructor_id)
        criteria = [
            CriterionIn(id=c.id, type=c.type, text="changed", evidence=c.evidence, weight=c.weight)
            for c in rubric.criteria
        ]
        with pytest.raises(ConflictError):
            await update_criteria(
                session, v1.id, UpdateCriteriaRequest(criteria=criteria), instructor_id
            )


async def test_set_rubric_family_program_updates_every_version_row(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    """V-064 AC1: a family-level attribute, denormalized onto every
    version row -- setting it must not silently leave an OLDER (still-
    readable, V-013 history-is-immutable) version disagreeing with its
    own family's current program."""
    from app.rubric.service import set_rubric_family_program

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    await _confirm(session_factory, v1.id, instructor_id)
    v2 = await _upload(
        session_factory,
        tmp_path,
        monkeypatch,
        instructor_id,
        family_id=v1.rubric_family_id,
        title="V2",
    )

    async with session_factory() as session:
        cs_id = await session.scalar(select(Program.id).where(Program.name == "CS"))
        items = await set_rubric_family_program(session, v1.rubric_family_id, cs_id, instructor_id)

    assert {item.program for item in items} == {"CS"}
    assert {item.id for item in items} == {v1.id, v2.id}


async def test_set_rubric_family_program_can_clear_it_back_to_not_set(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.rubric.service import set_rubric_family_program

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    async with session_factory() as session:
        cs_id = await session.scalar(select(Program.id).where(Program.name == "CS"))
        await set_rubric_family_program(session, v1.rubric_family_id, cs_id, instructor_id)
        cleared = await set_rubric_family_program(session, v1.rubric_family_id, None, instructor_id)
    assert all(item.program is None for item in cleared)


async def test_set_rubric_family_program_rejects_another_instructors_family(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.models.instructor import Instructor
    from app.rubric.service import set_rubric_family_program

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    async with session_factory() as session:
        other = Instructor(email="other2@tip.edu.ph", display_name="Other Prof")
        session.add(other)
        await session.commit()
        await session.refresh(other)
        other_id = other.id
        cs_id = await session.scalar(select(Program.id).where(Program.name == "CS"))

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await set_rubric_family_program(session, v1.rubric_family_id, cs_id, other_id)


async def test_set_rubric_family_program_rejects_an_unknown_program_id(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    from app.rubric.service import set_rubric_family_program

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await set_rubric_family_program(session, v1.rubric_family_id, 999_999, instructor_id)


async def test_a_new_version_uploaded_into_a_scoped_family_inherits_its_program(
    session_factory, tmp_path, monkeypatch, instructor_id
):
    """`backend-critic` finding (V-064 review), live-reproduced: a new
    version used to always land at program_id=None regardless of the
    family's own current program -- routine "upload a corrected format"
    (V-013) silently reverted a CS-only family back to "eligible for
    everything" the moment the new version was activated, no error, no
    warning. Every sibling must share the same program (the same
    invariant `set_rubric_family_program` itself maintains)."""
    from app.rubric.service import set_rubric_family_program

    v1 = await _upload(session_factory, tmp_path, monkeypatch, instructor_id, title="V1")
    async with session_factory() as session:
        cs_id = await session.scalar(select(Program.id).where(Program.name == "CS"))
        await set_rubric_family_program(session, v1.rubric_family_id, cs_id, instructor_id)

    v2 = await _upload(
        session_factory,
        tmp_path,
        monkeypatch,
        instructor_id,
        family_id=v1.rubric_family_id,
        title="V2",
    )
    assert v2.version == 2
    async with session_factory() as session:
        from app.rubric.service import get_rubric

        v2_reloaded = await get_rubric(session, v2.id, instructor_id)
    assert v2_reloaded.program == "CS"  # not None -- inherited, not reset
