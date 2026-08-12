"""V-010: rubric upload orchestration — file to persisted rubric+criteria.
Needs a live Postgres (same convention as test_ingest_api.py); runs
against its own scratch database.
"""

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.errors import NotFoundError
from app.llm.base import LLMClient
from app.models.instructor import Instructor
from app.models.rubric import Criterion, Rubric
from tests.test_ingest_pdf import PdfBuilder

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_rubrictest"

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
def rubric_scratch_url():
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
def session_factory(rubric_scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(rubric_scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


async def _seed_instructor(session_factory) -> int:
    """A fresh, uniquely-emailed instructor per call — this scratch DB is
    module-scoped and never truncated between tests (matches
    test_ingest_api.py's convention), so a fixed email would collide on
    the unique constraint by the second test."""
    async with session_factory() as session:
        instructor = Instructor(email=f"{uuid.uuid4()}@test.local", display_name="Test Instructor")
        session.add(instructor)
        await session.commit()
        await session.refresh(instructor)
        return instructor.id


def _rubric_pdf(tmp_path):
    # Title kept under the 20-char coverage-line floor (config default) so
    # it doesn't count as an uncovered "requirement" — it's a heading, not
    # a checkable rule.
    b = PdfBuilder()
    b.new_page().line("FORMAT CHECKLIST", bold=True)
    b.line("The manuscript must include an abstract.")
    b.line("The argument in Chapter 4 must be well developed.")
    return b.save(tmp_path / "rubric.pdf")


async def _chunks(path):
    yield path.read_bytes()


async def test_create_rubric_from_upload_persists_criteria_with_normalized_weights(
    session_factory, tmp_path, monkeypatch
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()

    path = _rubric_pdf(tmp_path)
    spy = SpyLLM(SCRIPTED_RESPONSE)

    from app.rubric.service import create_rubric_from_upload

    instructor_id = await _seed_instructor(session_factory)
    async with session_factory() as session:
        rubric = await create_rubric_from_upload(
            session,
            _chunks(path),
            "rubric.pdf",
            "Oral Defense Rubric",
            spy,
            settings,
            instructor_id=instructor_id,
        )
        rubric_id = rubric.id

    assert spy.calls == 1  # one call per rubric parse (D-011 quota math)

    async with session_factory() as session:
        persisted = (
            await session.execute(select(Rubric).where(Rubric.id == rubric_id))
        ).scalar_one()
        assert persisted.title == "Oral Defense Rubric"
        assert persisted.version == 1
        assert persisted.source_file.endswith(".pdf")
        assert persisted.instructor_id == instructor_id

        criteria = (
            (
                await session.execute(
                    select(Criterion)
                    .where(Criterion.rubric_id == rubric_id)
                    .order_by(Criterion.position)
                )
            )
            .scalars()
            .all()
        )
    assert [c.text for c in criteria] == ["Has an abstract", "Argument is well developed"]
    assert [c.type for c in criteria] == ["structural", "semantic"]
    assert [c.position for c in criteria] == [0, 1]
    assert sum(float(c.weight) for c in criteria) == pytest.approx(100.0)


async def test_failed_upload_leaves_no_orphaned_rubric_version(
    session_factory, tmp_path, monkeypatch
):
    """BUG-027: a corrupt/unreadable file must not leave a permanent
    0-criteria "Draft" version behind — nothing was ever reviewable, so
    nothing should persist. Before the fix, `create_rubric_from_upload`
    committed the Rubric row before extraction ran, and never cleaned it
    up when extraction raised — the orphaned row then became the
    family's new highest version, which made ReviewCriteriaPage's
    `readOnly` check falsely claim the real active version was
    superseded."""
    from app.errors import FileMalformedError
    from app.rubric.service import create_rubric_from_upload

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()

    async def garbage_chunks():
        yield b"this is not a pdf, docx, or doc file at all"

    instructor_id = await _seed_instructor(session_factory)
    async with session_factory() as session:
        with pytest.raises(FileMalformedError):
            await create_rubric_from_upload(
                session,
                garbage_chunks(),
                "bad.pdf",
                "Broken Upload",
                SpyLLM(SCRIPTED_RESPONSE),
                settings,
                instructor_id=instructor_id,
            )

    async with session_factory() as session:
        rows = (
            (await session.execute(select(Rubric).where(Rubric.instructor_id == instructor_id)))
            .scalars()
            .all()
        )
        assert rows == []

    # A subsequent GOOD upload must land on version 1, not 2 — proving no
    # version-number gap or lingering family from the failed attempt.
    path = _rubric_pdf(tmp_path)
    async with session_factory() as session:
        good = await create_rubric_from_upload(
            session,
            _chunks(path),
            "rubric.pdf",
            "Real Rubric",
            SpyLLM(SCRIPTED_RESPONSE),
            settings,
            instructor_id=instructor_id,
        )
    assert good.version == 1


async def test_failed_upload_cleanup_survives_a_poisoned_transaction(
    session_factory, tmp_path, monkeypatch
):
    """BUG-027, `backend-critic` finding: the except block's own cleanup
    (`session.delete(rubric)`, `session.commit()`) can itself fail if the
    exception that triggered it left the session's transaction aborted
    (a DB-level hiccup mid-flow — Neon idle-suspend, a dropped connection;
    app/db.py's check_connectivity docstring already documents this as a
    live condition). Without a `rollback()` first, that second commit
    raises `PendingRollbackError`/`InFailedSQLTransactionError` instead
    of succeeding, the ORIGINAL exception never propagates, and — worse —
    the orphaned rubric row survives anyway, reproducing BUG-027 through
    a different trigger. Reproduced live against the real dev Postgres
    before the `rollback()`/`refresh()` fix: 1 orphaned row remained.
    After: 0."""
    import contextlib

    from sqlalchemy import text

    import app.rubric.service as rubric_service

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()

    async def garbage_chunks():
        yield b"not a real pdf"

    instructor_id = await _seed_instructor(session_factory)
    async with session_factory() as session:
        real_save_upload = rubric_service.save_upload

        async def poisoning_save_upload(chunks_arg, dest, settings_arg):
            result = await real_save_upload(chunks_arg, dest, settings_arg)
            # Poison the transaction right where a real DB hiccup would
            # land — after the file is saved, before the next commit.
            with contextlib.suppress(Exception):
                await session.execute(text("SELECT 1/0"))
            return result

        monkeypatch.setattr(rubric_service, "save_upload", poisoning_save_upload)
        with pytest.raises(Exception):  # noqa: B017 — the poisoned-transaction DBAPIError, not FileMalformedError
            await rubric_service.create_rubric_from_upload(
                session,
                garbage_chunks(),
                "bad.pdf",
                "Poisoned",
                SpyLLM(SCRIPTED_RESPONSE),
                settings,
                instructor_id=instructor_id,
            )

    async with session_factory() as verify:
        rows = (
            (await verify.execute(select(Rubric).where(Rubric.instructor_id == instructor_id)))
            .scalars()
            .all()
        )
        assert rows == []


async def test_repeated_upload_starts_a_new_family_not_a_new_version(
    session_factory, tmp_path, monkeypatch
):
    """V-013 owns re-upload-becomes-v2; V-010's upload always starts a
    fresh family (no version-bump logic exists yet)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()
    path = _rubric_pdf(tmp_path)

    from app.rubric.service import create_rubric_from_upload

    instructor_id = await _seed_instructor(session_factory)
    async with session_factory() as session:
        first = await create_rubric_from_upload(
            session,
            _chunks(path),
            "rubric.pdf",
            "Rubric A",
            SpyLLM(SCRIPTED_RESPONSE),
            settings,
            instructor_id=instructor_id,
        )
        second = await create_rubric_from_upload(
            session,
            _chunks(path),
            "rubric.pdf",
            "Rubric B",
            SpyLLM(SCRIPTED_RESPONSE),
            settings,
            instructor_id=instructor_id,
        )

    assert first.rubric_family_id != second.rubric_family_id
    assert first.version == second.version == 1


async def _seeded_rubric(session_factory, tmp_path, monkeypatch) -> tuple[int, int]:
    """Returns (rubric_id, instructor_id) — a fresh instructor owns a
    freshly-uploaded rubric."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    settings = get_settings()
    path = _rubric_pdf(tmp_path)
    instructor_id = await _seed_instructor(session_factory)
    async with session_factory() as session:
        from app.rubric.service import create_rubric_from_upload

        rubric = await create_rubric_from_upload(
            session,
            _chunks(path),
            "rubric.pdf",
            "Oral Defense Rubric",
            SpyLLM(SCRIPTED_RESPONSE),
            settings,
            instructor_id=instructor_id,
        )
        return rubric.id, instructor_id


async def test_get_rubric_returns_criteria_ordered_by_position(
    session_factory, tmp_path, monkeypatch
):
    from app.rubric.service import get_rubric

    rubric_id, instructor_id = await _seeded_rubric(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        rubric = await get_rubric(session, rubric_id, instructor_id)
    assert [c.position for c in rubric.criteria] == [0, 1]
    assert rubric.is_active is False  # not confirmed yet


async def test_get_rubric_raises_not_found_for_unknown_id(session_factory):
    from app.rubric.service import get_rubric

    instructor_id = await _seed_instructor(session_factory)
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_rubric(session, 999999, instructor_id)


async def test_get_rubric_raises_not_found_for_a_different_instructors_rubric(
    session_factory, tmp_path, monkeypatch
):
    """BUG-001 regression: an authenticated instructor must never reach
    another instructor's rubric by guessing/incrementing an id — a wrong
    owner reads exactly like an unknown id, never a distinct 403 (no
    existence leak)."""
    from app.rubric.service import activate_rubric, delete_rubric, get_rubric, update_criteria

    rubric_id, _owner_id = await _seeded_rubric(session_factory, tmp_path, monkeypatch)
    other_instructor_id = await _seed_instructor(session_factory)

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await get_rubric(session, rubric_id, other_instructor_id)

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest

            await update_criteria(
                session,
                rubric_id,
                UpdateCriteriaRequest(
                    criteria=[CriterionIn(type="structural", text="x", weight=1.0)]
                ),
                other_instructor_id,
            )

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await activate_rubric(session, rubric_id, other_instructor_id)

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await delete_rubric(session, rubric_id, other_instructor_id)


async def test_update_criteria_edit_round_trip_persists(session_factory, tmp_path, monkeypatch):
    """V-012 AC: change type + weight, confirm, reload -> persisted."""
    from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest
    from app.rubric.service import get_rubric, update_criteria

    rubric_id, instructor_id = await _seeded_rubric(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        original = await get_rubric(session, rubric_id, instructor_id)
        edited = [
            CriterionIn(
                id=c.id,
                type="semantic" if c.type == "structural" else "structural",
                text=c.text + " (edited)",
                evidence=c.evidence,
                weight=42.0,
            )
            for c in original.criteria
        ]
        await update_criteria(
            session,
            rubric_id,
            UpdateCriteriaRequest(criteria=edited, confirm=True),
            instructor_id,
        )

    # Reload with a FRESH session — proves it's persisted, not just in-memory.
    async with session_factory() as session:
        reloaded = await get_rubric(session, rubric_id, instructor_id)
    assert reloaded.is_active is True
    assert [c.type for c in reloaded.criteria] == ["semantic", "structural"]
    assert all(c.text.endswith("(edited)") for c in reloaded.criteria)
    assert all(float(c.weight) == 42.0 for c in reloaded.criteria)


async def test_update_criteria_adds_and_deletes_rows(session_factory, tmp_path, monkeypatch):
    from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest
    from app.rubric.service import get_rubric, update_criteria

    rubric_id, instructor_id = await _seeded_rubric(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        original = await get_rubric(session, rubric_id, instructor_id)
        kept = original.criteria[0]
        new_list = [
            CriterionIn(
                id=kept.id, type=kept.type, text=kept.text, evidence=kept.evidence, weight=50.0
            ),
            CriterionIn(
                id=None, type="semantic", text="A brand new hand-added criterion", weight=50.0
            ),
        ]
        await update_criteria(
            session, rubric_id, UpdateCriteriaRequest(criteria=new_list), instructor_id
        )

    async with session_factory() as session:
        reloaded = await get_rubric(session, rubric_id, instructor_id)
    assert len(reloaded.criteria) == 2  # the 2nd original was deleted, 1 new was added
    assert reloaded.criteria[1].text == "A brand new hand-added criterion"
    assert reloaded.is_active is False  # confirm defaults to False — save draft, not activate


async def test_update_criteria_rejects_a_criterion_id_from_another_rubric(
    session_factory, tmp_path, monkeypatch
):
    from app.rubric.schemas import CriterionIn, UpdateCriteriaRequest
    from app.rubric.service import update_criteria

    rubric_a, instructor_id = await _seeded_rubric(session_factory, tmp_path, monkeypatch)
    rubric_b, _ = await _seeded_rubric(session_factory, tmp_path, monkeypatch)

    async with session_factory() as session:
        # rubric_b belongs to a different instructor; fetch its criterion
        # id directly off the model to keep the test focused on the
        # cross-rubric check, not another authorization path.
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import selectinload

        rubric_b_row = await session.scalar(
            sa_select(Rubric).where(Rubric.id == rubric_b).options(selectinload(Rubric.criteria))
        )
        b_criterion_id = rubric_b_row.criteria[0].id

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await update_criteria(
                session,
                rubric_a,
                UpdateCriteriaRequest(
                    criteria=[
                        CriterionIn(id=b_criterion_id, type="structural", text="x", weight=1.0)
                    ]
                ),
                instructor_id,
            )
