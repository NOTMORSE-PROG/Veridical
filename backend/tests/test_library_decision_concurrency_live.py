"""V-078/V-081: competing duplicate decisions must preserve a successful activation."""

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import sqlalchemy_url
from app.errors import ConflictError
from app.library import service as library
from app.models.enums import LibraryEntrySource, LibraryEntryStatus, ManuscriptOrigin
from app.models.library import LibraryEntry
from app.models.manuscript import Manuscript, ManuscriptArchive, ManuscriptPassageArchive
from tests.test_archive_router_live import _seed_with
from tests.test_archive_router_live import api_scratch_url as api_scratch_url
from tests.test_archive_router_live import client as client
from tests.test_archive_router_live import seeded as seeded
from tests.test_library_corpus_live import _extraction

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs live PostgreSQL")


def test_competing_add_decisions_preserve_the_winners_source_and_corpus(
    seeded, api_scratch_url, monkeypatch
):
    _, ids = seeded
    settings = get_settings()
    Path(ids["raw_path"]).write_text(_extraction().model_dump_json(), encoding="utf-8")

    async def waiting_submission(session):
        manuscript = await session.get(Manuscript, ids["mine"])
        manuscript.origin = ManuscriptOrigin.library_submission
        session.add(
            LibraryEntry(
                manuscript_id=manuscript.id,
                source=LibraryEntrySource.library_submission,
                status=LibraryEntryStatus.needs_decision,
            )
        )

    _seed_with(api_scratch_url, waiting_submission)

    async def exercise():
        engine = create_async_engine(sqlalchemy_url(api_scratch_url))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        both_embedded = asyncio.Event()
        completed_embeddings = 0
        original_embed = library._embed

        async def synchronized_embed(result, config):
            nonlocal completed_embeddings
            value = await original_embed(result, config)
            completed_embeddings += 1
            if completed_embeddings == 2:
                both_embedded.set()
            await asyncio.wait_for(both_embedded.wait(), timeout=10)
            return value

        monkeypatch.setattr(library, "_embed", synchronized_embed)

        async def add():
            async with sessions() as session:
                return await library.decide_library_entry(
                    session, ids["owner_id"], ids["mine"], "add", settings
                )

        try:
            outcomes = await asyncio.wait_for(
                asyncio.gather(add(), add(), return_exceptions=True), timeout=20
            )
            assert sum(not isinstance(row, BaseException) for row in outcomes) == 1, outcomes
            assert sum(isinstance(row, ConflictError) for row in outcomes) == 1, outcomes
            async with sessions() as session:
                entry = await session.scalar(
                    select(LibraryEntry).where(LibraryEntry.manuscript_id == ids["mine"])
                )
                manuscript = await session.get(Manuscript, ids["mine"])
                assert entry.status == LibraryEntryStatus.active
                assert manuscript.purged_at is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ManuscriptArchive)
                        .where(ManuscriptArchive.manuscript_id == manuscript.id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ManuscriptPassageArchive)
                        .where(ManuscriptPassageArchive.manuscript_id == manuscript.id)
                    )
                    > 0
                )
            assert Path(ids["upload_path"]).is_file()
            assert Path(ids["raw_path"]).is_file()
        finally:
            await engine.dispose()

    asyncio.run(exercise())
