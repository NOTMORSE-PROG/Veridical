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
    """The scratch DB is module-scoped; every test shares the same demo
    instructor (fixed email), so family/version counts would otherwise
    leak across tests."""
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE check_run, criterion, rubric, manuscript, instructor "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


def _rubric_pdf(tmp_path, name="rubric.pdf"):
    b = PdfBuilder()
    b.new_page().line("FORMAT CHECKLIST", bold=True)
    b.line("The manuscript must include an abstract.")
    b.line("The argument in Chapter 4 must be well developed.")
    return b.save(tmp_path / name)


async def _chunks(path):
    yield path.read_bytes()


async def _upload(session_factory, tmp_path, monkeypatch, *, family_id=None, title="Rubric"):
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
            family_id=family_id,
        )


async def _confirm(session_factory, rubric_id):
    from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest
    from app.rubric.service import get_rubric, update_criteria

    async with session_factory() as session:
        rubric = await get_rubric(session, rubric_id)
        criteria = [
            CriterionIn(id=c.id, type=c.type, text=c.text, evidence=c.evidence, weight=c.weight)
            for c in rubric.criteria
        ]
        return await update_criteria(
            session, rubric_id, UpdateCriteriaRequest(criteria=criteria, confirm=True)
        )


async def test_reupload_with_family_id_creates_v2_and_leaves_v1_untouched(
    session_factory, tmp_path, monkeypatch
):
    v1 = await _upload(session_factory, tmp_path, monkeypatch, title="V1")
    await _confirm(session_factory, v1.id)

    v2 = await _upload(
        session_factory, tmp_path, monkeypatch, family_id=v1.rubric_family_id, title="V2"
    )

    assert v2.rubric_family_id == v1.rubric_family_id
    assert v2.version == 2

    async with session_factory() as session:
        v1_reloaded = (await session.execute(select(Rubric).where(Rubric.id == v1.id))).scalar_one()
    assert v1_reloaded.version == 1
    assert v1_reloaded.title == "V1"  # untouched by the v2 upload


async def test_reupload_into_unknown_family_raises_not_found(
    session_factory, tmp_path, monkeypatch
):
    import uuid

    with pytest.raises(NotFoundError):
        await _upload(session_factory, tmp_path, monkeypatch, family_id=uuid.uuid4())


async def test_activating_v2_deactivates_v1_exactly_one_active_per_family(
    session_factory, tmp_path, monkeypatch
):
    from app.rubric.service import activate_rubric, get_rubric

    v1 = await _upload(session_factory, tmp_path, monkeypatch, title="V1")
    await _confirm(session_factory, v1.id)  # v1 active
    v2 = await _upload(
        session_factory, tmp_path, monkeypatch, family_id=v1.rubric_family_id, title="V2"
    )

    async with session_factory() as session:
        await activate_rubric(session, v2.id)

    async with session_factory() as session:
        v1_after = await get_rubric(session, v1.id)
        v2_after = await get_rubric(session, v2.id)
    assert v1_after.is_active is False
    assert v2_after.is_active is True


async def test_confirming_v2_also_deactivates_v1(session_factory, tmp_path, monkeypatch):
    """Same invariant, reached via the OTHER activation path (V-012's
    confirm, not V-013's explicit Activate button)."""
    from app.rubric.service import get_rubric

    v1 = await _upload(session_factory, tmp_path, monkeypatch, title="V1")
    await _confirm(session_factory, v1.id)
    v2 = await _upload(
        session_factory, tmp_path, monkeypatch, family_id=v1.rubric_family_id, title="V2"
    )
    await _confirm(session_factory, v2.id)

    async with session_factory() as session:
        v1_after = await get_rubric(session, v1.id)
    assert v1_after.is_active is False


async def test_is_latest_version_flips_when_a_new_version_is_uploaded(
    session_factory, tmp_path, monkeypatch
):
    from app.rubric.service import get_rubric

    v1 = await _upload(session_factory, tmp_path, monkeypatch, title="V1")
    async with session_factory() as session:
        assert (await get_rubric(session, v1.id)).is_latest_version is True

    v2 = await _upload(
        session_factory, tmp_path, monkeypatch, family_id=v1.rubric_family_id, title="V2"
    )
    async with session_factory() as session:
        assert (await get_rubric(session, v1.id)).is_latest_version is False
        assert (await get_rubric(session, v2.id)).is_latest_version is True


async def test_list_rubric_versions_orders_newest_first_with_counts(
    session_factory, tmp_path, monkeypatch
):
    from app.rubric.service import list_rubric_versions

    v1 = await _upload(session_factory, tmp_path, monkeypatch, title="V1")
    await _confirm(session_factory, v1.id)
    v2 = await _upload(
        session_factory, tmp_path, monkeypatch, family_id=v1.rubric_family_id, title="V2"
    )

    async with session_factory() as session:
        versions = await list_rubric_versions(session, v1.rubric_family_id)

    assert [v.version for v in versions] == [2, 1]
    assert versions[1].is_active is True  # v1
    assert versions[1].criteria_count == 2
    assert versions[1].report_count == 0
    assert versions[0].id == v2.id


async def test_list_rubric_families_returns_one_row_per_family(
    session_factory, tmp_path, monkeypatch
):
    from app.rubric.service import list_rubric_families

    cs = await _upload(session_factory, tmp_path, monkeypatch, title="CS-Format")
    await _confirm(session_factory, cs.id)
    it = await _upload(session_factory, tmp_path, monkeypatch, title="IT-Format")
    await _confirm(session_factory, it.id)

    async with session_factory() as session:
        families = await list_rubric_families(session, cs.instructor_id)

    family_ids = {f.rubric_family_id for f in families}
    assert family_ids == {cs.rubric_family_id, it.rubric_family_id}  # two independent families
    assert all(f.is_active for f in families)


async def _insert_check_run_against(session_factory, rubric_id):
    async with session_factory() as session:
        manuscript = Manuscript(instructor_id=1, group_label="Group 1", file_ref="x.pdf")
        session.add(manuscript)
        await session.flush()
        session.add(CheckRun(manuscript_id=manuscript.id, rubric_id=rubric_id))
        await session.commit()


async def test_delete_blocked_when_reports_exist_allowed_otherwise(
    session_factory, tmp_path, monkeypatch
):
    from app.rubric.service import delete_rubric, get_rubric

    with_report = await _upload(session_factory, tmp_path, monkeypatch, title="HasReports")
    without_report = await _upload(session_factory, tmp_path, monkeypatch, title="NoReports")
    await _insert_check_run_against(session_factory, with_report.id)

    async with session_factory() as session:
        with pytest.raises(ConflictError):
            await delete_rubric(session, with_report.id)

    async with session_factory() as session:
        await delete_rubric(session, without_report.id)  # no reports -> succeeds
        with pytest.raises(NotFoundError):
            await get_rubric(session, without_report.id)


async def test_editing_an_active_version_with_reports_is_blocked(
    session_factory, tmp_path, monkeypatch
):
    from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest
    from app.rubric.service import get_rubric, update_criteria

    v1 = await _upload(session_factory, tmp_path, monkeypatch, title="V1")
    await _confirm(session_factory, v1.id)  # now active
    await _insert_check_run_against(session_factory, v1.id)

    async with session_factory() as session:
        rubric = await get_rubric(session, v1.id)
        criteria = [
            CriterionIn(id=c.id, type=c.type, text="changed", evidence=c.evidence, weight=c.weight)
            for c in rubric.criteria
        ]
        with pytest.raises(ConflictError):
            await update_criteria(session, v1.id, UpdateCriteriaRequest(criteria=criteria))
