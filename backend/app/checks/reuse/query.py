"""Similarity query (F7.2, V-037) against the archive V-036 built —
whole-document AND chapter-level (ticket AC: "a chapter transplanted into
a new doc -> chapter-level flag"), real pgvector `<=>` cosine-distance
queries via `Vector.cosine_distance()`.

**Never match against self**: the caller MUST query before writing this
manuscript's own embeddings back (`app.checks.reuse.service` owns that
ordering) — this module additionally excludes the current
`manuscript_id` defensively, since a RE-RUN of an already-processed
manuscript would otherwise find its own prior archive row.

**Cross-model safety** (V-036's own ticket edge case): every query filters
`model_id == settings.embedding_model_id` — a future model change makes
old vectors incomparable, and this is the one place that guarantee
actually gets enforced, not just documented.

**V7 hook, dormant** (ticket AC: "Resubmission by the same group (V7
future) exempted via family link — design the exemption now, dormant
until V7"): `exclude_manuscript_ids` exists on the query signature for
exactly this — no caller passes it yet (V7's resubmission/family data
model doesn't exist, D-005 blocks it), so it is always empty today. When
V7 lands a real family link, wiring the exemption is passing that set in,
not redesigning this function.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.reuse.embed import DocumentEmbeddings
from app.config import Settings
from app.models.manuscript import Manuscript, ManuscriptArchive, ManuscriptChapterArchive

MatchLevel = Literal["exact_duplicate", "high_similarity"]


@dataclass(frozen=True)
class SimilarityMatch:
    level: MatchLevel
    similarity: float
    matched_manuscript_id: int
    matched_group_label: str
    # None for a whole-document match; both set for a chapter-level match.
    matched_chapter_title: str | None = None
    own_chapter_title: str | None = None


@dataclass(frozen=True)
class OriginalityQueryResult:
    matches: list[SimilarityMatch]
    # Cold-start disclosure (ticket AC): how many OTHER manuscripts existed
    # in the archive at query time — shown even when 0 (charter rule 9:
    # honest about growing coverage, never hidden).
    archive_size_n: int


def _classify(similarity: float, settings: Settings) -> MatchLevel | None:
    if similarity >= settings.reuse_exact_duplicate_threshold:
        return "exact_duplicate"
    if similarity >= settings.reuse_high_similarity_threshold:
        return "high_similarity"
    return None


async def _archive_size(
    session: AsyncSession, *, exclude_manuscript_ids: set[int], settings: Settings
) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(ManuscriptArchive)
            .where(
                ManuscriptArchive.model_id == settings.embedding_model_id,
                ManuscriptArchive.manuscript_id.notin_(exclude_manuscript_ids),
            )
        )
    ) or 0


async def _best_whole_document_match(
    session: AsyncSession,
    embeddings: DocumentEmbeddings,
    *,
    exclude_manuscript_ids: set[int],
    settings: Settings,
) -> SimilarityMatch | None:
    if embeddings.whole_document is None:
        return None
    distance_expr = ManuscriptArchive.embedding.cosine_distance(embeddings.whole_document)
    row = (
        await session.execute(
            select(
                ManuscriptArchive.manuscript_id,
                (1 - distance_expr).label("similarity"),
                Manuscript.group_label,
            )
            .join(Manuscript, Manuscript.id == ManuscriptArchive.manuscript_id)
            .where(
                ManuscriptArchive.model_id == settings.embedding_model_id,
                ManuscriptArchive.manuscript_id.notin_(exclude_manuscript_ids),
            )
            .order_by(distance_expr)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    level = _classify(float(row.similarity), settings)
    if level is None:
        return None
    return SimilarityMatch(
        level=level,
        similarity=float(row.similarity),
        matched_manuscript_id=row.manuscript_id,
        matched_group_label=row.group_label,
    )


async def _best_chapter_matches(
    session: AsyncSession,
    embeddings: DocumentEmbeddings,
    *,
    exclude_manuscript_ids: set[int],
    settings: Settings,
) -> list[SimilarityMatch]:
    matches: list[SimilarityMatch] = []
    for chapter in embeddings.chapters:
        distance_expr = ManuscriptChapterArchive.embedding.cosine_distance(chapter.embedding)
        row = (
            await session.execute(
                select(
                    ManuscriptChapterArchive.manuscript_id,
                    ManuscriptChapterArchive.title,
                    (1 - distance_expr).label("similarity"),
                    Manuscript.group_label,
                )
                .join(Manuscript, Manuscript.id == ManuscriptChapterArchive.manuscript_id)
                .where(
                    ManuscriptChapterArchive.model_id == settings.embedding_model_id,
                    ManuscriptChapterArchive.manuscript_id.notin_(exclude_manuscript_ids),
                )
                .order_by(distance_expr)
                .limit(1)
            )
        ).first()
        if row is None:
            continue
        level = _classify(float(row.similarity), settings)
        if level is None:
            continue
        matches.append(
            SimilarityMatch(
                level=level,
                similarity=float(row.similarity),
                matched_manuscript_id=row.manuscript_id,
                matched_group_label=row.group_label,
                matched_chapter_title=row.title,
                own_chapter_title=chapter.title,
            )
        )
    return matches


async def query_similar_manuscripts(
    session: AsyncSession,
    manuscript_id: int,
    embeddings: DocumentEmbeddings,
    settings: Settings,
    *,
    exclude_manuscript_ids: set[int] | None = None,
) -> OriginalityQueryResult:
    exclude = {manuscript_id, *(exclude_manuscript_ids or set())}
    archive_size_n = await _archive_size(session, exclude_manuscript_ids=exclude, settings=settings)

    matches: list[SimilarityMatch] = []
    whole_doc_match = await _best_whole_document_match(
        session, embeddings, exclude_manuscript_ids=exclude, settings=settings
    )
    if whole_doc_match is not None:
        matches.append(whole_doc_match)
    matches.extend(
        await _best_chapter_matches(
            session, embeddings, exclude_manuscript_ids=exclude, settings=settings
        )
    )

    return OriginalityQueryResult(matches=matches, archive_size_n=archive_size_n)
