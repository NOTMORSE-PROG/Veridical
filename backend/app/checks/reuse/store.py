"""Archive persistence (F7.1, V-036): idempotent upsert of a manuscript's
whole-document + per-chapter embeddings. Deliberately separate from
`embed.py` (pure computation, no DB) and from V-037's query/write-back
timing (query the archive BEFORE this manuscript is added, write AFTER
the check completes — V-037 owns WHEN this gets called; this module only
owns HOW the row lands correctly when it does).

**Idempotent re-runs** (ticket AC): re-embedding the same manuscript
replaces its existing archive rows rather than duplicating them —
`ManuscriptArchive.manuscript_id` is unique, and chapter rows are deleted
+ reinserted inside the same transaction (a chapter count/order change
between runs, e.g. after a re-upload, can't leave stale rows behind).
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.reuse.embed import (
    DocumentEmbeddings,
    PassageEmbedding,
    compute_document_embeddings,
)
from app.config import Settings
from app.ingest.schemas import ExtractionResult
from app.models.manuscript import (
    ManuscriptArchive,
    ManuscriptChapterArchive,
    ManuscriptPassageArchive,
)


async def store_document_embeddings(
    session: AsyncSession, manuscript_id: int, embeddings: DocumentEmbeddings
) -> ManuscriptArchive | None:
    """Returns None (and stores nothing) when the manuscript produced no
    whole-document vector at all (an honest absence — e.g. a genuinely
    empty extraction — never a fabricated placeholder row)."""
    existing = await session.scalar(
        select(ManuscriptArchive).where(ManuscriptArchive.manuscript_id == manuscript_id)
    )
    await session.execute(
        delete(ManuscriptChapterArchive).where(
            ManuscriptChapterArchive.manuscript_id == manuscript_id
        )
    )

    if embeddings.whole_document is None:
        if existing is not None:
            await session.delete(existing)
        await session.commit()
        return None

    if existing is None:
        existing = ManuscriptArchive(manuscript_id=manuscript_id)
        session.add(existing)
    existing.embedding = embeddings.whole_document
    existing.model_id = embeddings.model_id

    for chapter in embeddings.chapters:
        session.add(
            ManuscriptChapterArchive(
                manuscript_id=manuscript_id,
                chapter_index=chapter.chapter_index,
                title=chapter.title,
                page=chapter.page,
                paragraph=chapter.paragraph,
                embedding=chapter.embedding,
                model_id=embeddings.model_id,
            )
        )

    await session.commit()
    await session.refresh(existing)
    return existing


async def embed_and_store(
    session: AsyncSession,
    manuscript_id: int,
    extraction: ExtractionResult,
    settings: Settings,
) -> ManuscriptArchive | None:
    embeddings = compute_document_embeddings(extraction, settings)
    return await store_document_embeddings(session, manuscript_id, embeddings)


async def store_passage_embeddings(
    session: AsyncSession,
    manuscript_id: int,
    passages: list[PassageEmbedding],
    settings: Settings,
) -> None:
    """F7.4 (V-072) persistence — same idempotent delete-then-reinsert
    convention as the chapter archive above (a re-run's passage count/
    offsets can change between runs, e.g. after a re-upload). `model_id`
    comes from `settings`, not the passage itself — same convention as
    `ChapterEmbedding`, which also doesn't carry its own model id (it's a
    single settings-level fact for the whole batch, recorded once on the
    parent `DocumentEmbeddings.model_id` there)."""
    await session.execute(
        delete(ManuscriptPassageArchive).where(
            ManuscriptPassageArchive.manuscript_id == manuscript_id
        )
    )
    for passage in passages:
        session.add(
            ManuscriptPassageArchive(
                manuscript_id=manuscript_id,
                passage_index=passage.passage_index,
                chapter_index=passage.chapter_index,
                page=passage.page,
                paragraph=passage.paragraph,
                char_start=passage.char_start,
                char_end=passage.char_end,
                text=passage.text,
                context_text=passage.context_text,
                is_reference_list=passage.is_reference_list,
                is_block_quote=passage.is_block_quote,
                embedding=passage.embedding,
                model_id=settings.embedding_model_id,
            )
        )
    await session.commit()
