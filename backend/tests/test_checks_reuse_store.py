"""V-036 tests: archive persistence — idempotent upsert, and the ticket's
own AC ("similarity query returns self at ~1.0 for a re-embedded
duplicate") proven against a REAL pgvector `<=>` query, not just a Python
cosine check. Live Postgres (own scratch DB, same convention as
`test_checks_agreement_service.py`)."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.checks.reuse.embed import compute_document_embeddings
from app.checks.reuse.store import embed_and_store, store_document_embeddings
from app.config import get_settings
from app.db import sqlalchemy_url
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.models.instructor import Instructor
from app.models.manuscript import Manuscript

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_reusetest"


@pytest.fixture(scope="module")
def scratch_url():
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
def session_factory(scratch_url):
    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean(session_factory):
    async with session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE manuscript_chapter_archive, manuscript_archive, manuscript, "
                "instructor RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


async def _seed_manuscript(session_factory) -> int:
    async with session_factory() as session:
        instructor = Instructor(email="reuse-test@test.local", display_name="Reuse Test")
        session.add(instructor)
        await session.commit()
        manuscript = Manuscript(
            instructor_id=instructor.id, group_label="Group A", file_ref="test.pdf"
        )
        session.add(manuscript)
        await session.commit()
        return manuscript.id


def _block(text_: str, *, page: int = 1) -> TextBlock:
    return TextBlock(text=text_, page=page, max_font_size=11.0, bold_ratio=0.0)


def _extraction() -> ExtractionResult:
    blocks = [
        _block("Chapter 1: Introduction", page=1),
        _block("The current process for tracking attendance is entirely manual.", page=1),
        _block("Chapter 2: Methodology", page=5),
        _block("We used a hybrid rule-based and AI approach to grading.", page=5),
    ]
    nodes = [
        SectionNode(title="Chapter 1: Introduction", level=1, page=1),
        SectionNode(title="Chapter 2: Methodology", level=1, page=5),
    ]
    return ExtractionResult(
        page_count=5,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=SectionTree(source="heuristics", nodes=nodes),
        blocks=blocks,
        images=[],
    )


async def test_embed_and_store_round_trip(session_factory):
    manuscript_id = await _seed_manuscript(session_factory)
    settings = get_settings()
    async with session_factory() as session:
        archive = await embed_and_store(session, manuscript_id, _extraction(), settings)
    assert archive is not None
    assert archive.model_id == settings.embedding_model_id
    assert len(archive.embedding) == settings.embedding_dim

    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT chapter_index, title FROM manuscript_chapter_archive "
                    "WHERE manuscript_id = :id ORDER BY chapter_index"
                ),
                {"id": manuscript_id},
            )
        ).all()
    assert [r.title for r in rows] == ["Chapter 1: Introduction", "Chapter 2: Methodology"]


async def test_idempotent_rerun_does_not_duplicate_rows(session_factory):
    """Ticket AC: idempotent re-runs — re-embedding replaces, never
    duplicates (e.g. after a re-upload with a different chapter count)."""
    manuscript_id = await _seed_manuscript(session_factory)
    settings = get_settings()
    extraction = _extraction()
    async with session_factory() as session:
        await embed_and_store(session, manuscript_id, extraction, settings)
    async with session_factory() as session:
        await embed_and_store(session, manuscript_id, extraction, settings)

    async with session_factory() as session:
        archive_count = await session.scalar(
            text("SELECT count(*) FROM manuscript_archive WHERE manuscript_id = :id"),
            {"id": manuscript_id},
        )
        chapter_count = await session.scalar(
            text("SELECT count(*) FROM manuscript_chapter_archive WHERE manuscript_id = :id"),
            {"id": manuscript_id},
        )
    assert archive_count == 1
    assert chapter_count == 2  # not 4 — the second run replaced, not appended


async def test_reembedded_duplicate_matches_itself_at_near_1_via_real_pgvector_query(
    session_factory,
):
    """Ticket AC, verbatim: 'Similarity query returns self at ~1.0 for a
    re-embedded duplicate.' Proven against a REAL pgvector cosine-distance
    query (`<=>`), not just a Python-side cosine check — `1 - distance` is
    what V-037's query will actually compute."""
    manuscript_id = await _seed_manuscript(session_factory)
    settings = get_settings()
    extraction = _extraction()
    async with session_factory() as session:
        await embed_and_store(session, manuscript_id, extraction, settings)

    # Re-embed the SAME text fresh (simulating a re-uploaded duplicate) —
    # deterministic, so this must be (numerically) identical to the stored
    # vector, and a real pgvector query against it must return ~1.0.
    fresh = compute_document_embeddings(extraction, settings)
    assert fresh.whole_document is not None

    async with session_factory() as session:
        row = await session.execute(
            text(
                "SELECT 1 - (embedding <=> CAST(:vec AS vector)) AS similarity "
                "FROM manuscript_archive WHERE manuscript_id = :id"
            ),
            {"vec": str(fresh.whole_document), "id": manuscript_id},
        )
        similarity = row.scalar()
    assert similarity > 0.999


async def test_extraction_with_no_content_stores_nothing(session_factory):
    manuscript_id = await _seed_manuscript(session_factory)
    settings = get_settings()
    empty = ExtractionResult(
        page_count=0,
        anchor_kind="page",
        image_only=False,
        text_chars=0,
        section_tree=SectionTree(source="none", nodes=[]),
        blocks=[],
        images=[],
    )
    async with session_factory() as session:
        result = await store_document_embeddings(
            session, manuscript_id, compute_document_embeddings(empty, settings)
        )
    assert result is None
    async with session_factory() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM manuscript_archive WHERE manuscript_id = :id"),
            {"id": manuscript_id},
        )
    assert count == 0
