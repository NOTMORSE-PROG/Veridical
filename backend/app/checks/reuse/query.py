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

**Own group's prior submissions are never reuse** (BUG-050 item 1, fixed
2026-08-19 now that V-062 gave `Manuscript.group_id` a real FK instead of
free text): `_group_sibling_manuscript_ids` below excludes every OTHER
manuscript row in the querying manuscript's own group, so a re-upload or
revision by the same team never flags against its own prior submission.
Deliberately does NOT apply this to the "Ungrouped" default bucket
(`app.groups.service.DEFAULT_GROUP_LABEL`) — every manuscript with no real
team name resolves into that ONE shared row per instructor
(`resolve_or_create_group`), so excluding its siblings would silently hide
real reuse between two UNRELATED teams that both just haven't been
assigned a group yet (the opposite of what this check exists to catch).

**V7 hook, dormant** (ticket AC: "Resubmission by the same group (V7
future) exempted via family link — design the exemption now, dormant
until V7"): `exclude_manuscript_ids` exists on the query signature for a
FUTURE family-link exemption distinct from the group exemption above — no
caller passes it yet (V7's resubmission/family data model doesn't exist,
D-005 blocks it), so it is always empty today. When V7 lands a real
family link, wiring that exemption is passing that set in, not
redesigning this function.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.reuse.embed import DocumentEmbeddings, PassageEmbedding
from app.config import Settings
from app.groups.service import DEFAULT_GROUP_LABEL, normalize_group_name
from app.models.group import Group
from app.models.manuscript import (
    Manuscript,
    ManuscriptArchive,
    ManuscriptChapterArchive,
    ManuscriptPassageArchive,
)

_UNGROUPED_NORMALIZED = normalize_group_name(DEFAULT_GROUP_LABEL)

MatchLevel = Literal["exact_duplicate", "high_similarity"]


@dataclass(frozen=True)
class SimilarityMatch:
    level: MatchLevel
    similarity: float
    matched_manuscript_id: int
    matched_group_label: str
    # BUG-140: internal only, same convention as `matched_group_label` --
    # never serialized instructor-facing. Lets the caller (service.py)
    # tell "this match is against my OWN earlier upload" from "this is a
    # genuinely different instructor's document" without a second query.
    matched_instructor_id: int
    # None for a whole-document match; both set for a chapter-level match.
    matched_chapter_title: str | None = None
    own_chapter_title: str | None = None
    # BUG-153 (backend-critic finding, live-reproduced): the index twins
    # of the titles above -- needed to SCOPE a chapter-level match's
    # supporting-passage lookup to the two specific chapters this match
    # is actually about. Titles alone can't do that (not a stable join
    # key, and never guaranteed unique). None for a whole-document match,
    # same convention as the titles.
    matched_chapter_index: int | None = None
    own_chapter_index: int | None = None


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
                Manuscript.instructor_id,
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
        matched_instructor_id=row.instructor_id,
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
                    ManuscriptChapterArchive.chapter_index,
                    (1 - distance_expr).label("similarity"),
                    Manuscript.group_label,
                    Manuscript.instructor_id,
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
                matched_instructor_id=row.instructor_id,
                matched_group_label=row.group_label,
                matched_chapter_title=row.title,
                own_chapter_title=chapter.title,
                matched_chapter_index=row.chapter_index,
                own_chapter_index=chapter.chapter_index,
            )
        )
    return matches


async def same_instructor_hash_duplicate(
    session: AsyncSession, manuscript_id: int, instructor_id: int, content_hash: str | None
) -> int | None:
    """BUG-140: a stronger, embedding-independent "same file" signal --
    exact content-hash equality is certain where cosine similarity is only
    ever confident, and it still catches a byte-identical re-upload even
    when the manuscript produces no embeddable content at all (an
    image-only scan, where the embedding-similarity path below never runs
    a comparison in the first place). Returns the OTHER manuscript's id, or
    `None` if there's no match (including when `content_hash` itself is
    `None` -- a manuscript ingested before this column existed)."""
    if content_hash is None:
        return None
    row = (
        await session.execute(
            select(Manuscript.id)
            .where(
                Manuscript.instructor_id == instructor_id,
                Manuscript.content_hash == content_hash,
                Manuscript.id != manuscript_id,
                Manuscript.purged_at.is_(None),
            )
            .order_by(Manuscript.id)
            .limit(1)
        )
    ).first()
    return row[0] if row is not None else None


async def is_first_upload_for_instructor(session: AsyncSession, manuscript_id: int) -> bool:
    """BUG-097 (presentation-only remedy, owner ruling 2026-08-24): True
    when this manuscript is the ONLY manuscript its instructor has ever
    uploaded — i.e. this instructor has no other `Manuscript` row. On a
    genuinely first-ever upload, ANY match found is necessarily
    cross-instructor by construction (there is no other manuscript from
    this instructor to match against), so the caller doesn't need to
    separately check the matched side's instructor.

    Deliberately does NOT change severity or scoring (that was considered
    and rejected: nearly every fixture in this test suite, and much of the
    real corpus during its early/thin period, has exactly this shape, so a
    severity downgrade here would broadly weaken genuine duplicate
    detection, not just soften one rare edge case). This flag exists only
    so the report can show a "this is your first-ever check, verify with
    extra care" banner — the actual finding stays exactly as trustworthy
    as any other match.
    """
    row = (
        await session.execute(
            select(Manuscript.instructor_id).where(Manuscript.id == manuscript_id)
        )
    ).first()
    if row is None:
        return False
    other_count = await session.scalar(
        select(func.count())
        .select_from(Manuscript)
        .where(Manuscript.instructor_id == row.instructor_id, Manuscript.id != manuscript_id)
    )
    return (other_count or 0) == 0


async def _group_sibling_manuscript_ids(session: AsyncSession, manuscript_id: int) -> set[int]:
    row = (
        await session.execute(
            select(Manuscript.group_id, Manuscript.instructor_id, Group.name_normalized)
            .outerjoin(Group, Group.id == Manuscript.group_id)
            .where(Manuscript.id == manuscript_id)
        )
    ).first()
    if row is None or row.group_id is None or row.name_normalized == _UNGROUPED_NORMALIZED:
        return set()
    # `instructor_id` is redundant with `group_id` today -- `Group` rows are
    # always created scoped to one instructor (`resolve_or_create_group`,
    # `app/groups/service.py`), so no `group_id` can span two instructors.
    # Kept explicit anyway (`backend-critic` review, 2026-08-19): a silent
    # false negative here (a real cross-tenant match that never fires) is
    # WORSE than BUG-050's leak was, because nothing surfaces to notice it
    # by -- worth the one extra predicate as a belt-and-suspenders guard
    # against that scoping invariant ever drifting elsewhere.
    return set(
        (
            await session.scalars(
                select(Manuscript.id).where(
                    Manuscript.group_id == row.group_id,
                    Manuscript.instructor_id == row.instructor_id,
                )
            )
        ).all()
    )


async def query_similar_manuscripts(
    session: AsyncSession,
    manuscript_id: int,
    embeddings: DocumentEmbeddings,
    settings: Settings,
    *,
    exclude_manuscript_ids: set[int] | None = None,
) -> OriginalityQueryResult:
    exclude = {manuscript_id, *(exclude_manuscript_ids or set())}
    exclude |= await _group_sibling_manuscript_ids(session, manuscript_id)
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


# --- Passage-level query (V-072, F7.4) --------------------------------------
#
# Measured 2026-08-20 (V-072.md's own "still genuinely open" research item):
# HNSW query latency against a scratch table at 2,000/5,000/10,000 rows
# (the ticket's own worst-case archive-size projection) was ~1-2ms per
# query even at 10,000 rows, and a sequential loop of 200 per-passage
# queries (a realistic manuscript's passage count) against a 10,000-row
# archive totalled ~0.22s. That resolves the performance question this
# ticket left open: the exact same "one query per own item" loop
# `_best_chapter_matches` already uses above is fast enough here too — no
# batched/LATERAL-join query was needed.


@dataclass(frozen=True)
class PassageMatch:
    own_passage_index: int
    own_chapter_index: int
    own_page: int | None
    own_paragraph: int | None
    own_char_start: int
    own_char_end: int
    own_text: str
    own_context_text: str
    own_is_reference_list: bool
    own_is_block_quote: bool
    level: MatchLevel
    similarity: float
    matched_manuscript_id: int
    # Internal only, same BUG-050/097 convention as `SimilarityMatch` above
    # — never serialized to an instructor-facing response.
    matched_group_label: str
    # BUG-140: internal only, same purpose as `SimilarityMatch.matched_instructor_id`.
    matched_instructor_id: int
    matched_chapter_index: int
    matched_page: int | None
    matched_paragraph: int | None
    matched_char_start: int
    matched_char_end: int
    matched_text: str
    matched_context_text: str
    matched_is_reference_list: bool
    matched_is_block_quote: bool


async def passage_archive_size(
    session: AsyncSession, *, exclude_manuscript_ids: set[int], settings: Settings
) -> int:
    """Same cold-start honesty purpose as `_archive_size` above (ticket
    AC5: "a thin archive must not make passage matching look
    authoritative") — a separate count because the passage archive can be
    non-empty even when a manuscript's whole-doc/chapter vectors are (a
    manuscript with chapters but very short ones could produce passages at
    a size below F7.1's own dilution-vs-signal balance, an edge this
    ticket doesn't need to resolve, just not misreport)."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(ManuscriptPassageArchive)
            .join(Manuscript, Manuscript.id == ManuscriptPassageArchive.manuscript_id)
            .where(
                ManuscriptPassageArchive.model_id == settings.embedding_model_id,
                ManuscriptPassageArchive.manuscript_id.notin_(exclude_manuscript_ids),
                # BUG-123: a purged manuscript's passages must not count
                # toward "how big is the comparison archive" either -- same
                # honesty purpose as excluding them from matches.
                Manuscript.purged_at.is_(None),
            )
        )
    ) or 0


def _classify_passage(similarity: float, settings: Settings) -> MatchLevel | None:
    if similarity >= settings.reuse_passage_exact_duplicate_threshold:
        return "exact_duplicate"
    if similarity >= settings.reuse_passage_high_similarity_threshold:
        return "high_similarity"
    return None


async def _best_passage_match_for(
    session: AsyncSession,
    passage: PassageEmbedding,
    *,
    exclude_manuscript_ids: set[int],
    include_reference_list: bool,
    include_block_quote: bool,
    settings: Settings,
) -> PassageMatch | None:
    distance_expr = ManuscriptPassageArchive.embedding.cosine_distance(passage.embedding)
    conditions = [
        ManuscriptPassageArchive.model_id == settings.embedding_model_id,
        ManuscriptPassageArchive.manuscript_id.notin_(exclude_manuscript_ids),
        # BUG-123 defense in depth: `purge_manuscript` now deletes this
        # manuscript's passage rows outright, but this join+filter is a
        # second, independent guard against a purged manuscript's text
        # ever being matched or surfaced again -- a single missed delete
        # call site should not be able to reintroduce the leak.
        Manuscript.purged_at.is_(None),
    ]
    # The CANDIDATE side respects the same inclusion flags as the query
    # side: a reference-list/block-quote passage on either side of a match
    # is exactly what "on by default" excludes (ticket AC3) -- filtering
    # only the query side would still let a legitimate body passage match
    # an excluded reference-list passage in the archive.
    if not include_reference_list:
        conditions.append(ManuscriptPassageArchive.is_reference_list.is_(False))
    if not include_block_quote:
        conditions.append(ManuscriptPassageArchive.is_block_quote.is_(False))

    row = (
        await session.execute(
            select(
                ManuscriptPassageArchive,
                (1 - distance_expr).label("similarity"),
                Manuscript.group_label,
                Manuscript.instructor_id,
            )
            .join(Manuscript, Manuscript.id == ManuscriptPassageArchive.manuscript_id)
            .where(*conditions)
            .order_by(distance_expr)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    level = _classify_passage(float(row.similarity), settings)
    if level is None:
        return None
    archive_row: ManuscriptPassageArchive = row[0]
    return PassageMatch(
        own_passage_index=passage.passage_index,
        own_chapter_index=passage.chapter_index,
        own_page=passage.page,
        own_paragraph=passage.paragraph,
        own_char_start=passage.char_start,
        own_char_end=passage.char_end,
        own_text=passage.text,
        own_context_text=passage.context_text,
        own_is_reference_list=passage.is_reference_list,
        own_is_block_quote=passage.is_block_quote,
        level=level,
        similarity=float(row.similarity),
        matched_manuscript_id=archive_row.manuscript_id,
        matched_instructor_id=row.instructor_id,
        matched_group_label=row.group_label,
        matched_chapter_index=archive_row.chapter_index,
        matched_page=archive_row.page,
        matched_paragraph=archive_row.paragraph,
        matched_char_start=archive_row.char_start,
        matched_char_end=archive_row.char_end,
        matched_text=archive_row.text,
        matched_context_text=archive_row.context_text,
        matched_is_reference_list=archive_row.is_reference_list,
        matched_is_block_quote=archive_row.is_block_quote,
    )


async def best_supporting_passage(
    session: AsyncSession,
    passages: list[PassageEmbedding],
    matched_manuscript_id: int,
    settings: Settings,
    *,
    own_chapter_index: int | None = None,
    matched_chapter_index: int | None = None,
) -> PassageMatch | None:
    """BUG-153: the single strongest passage-level pairing between THIS
    manuscript and one SPECIFIC already-matched manuscript -- real,
    checkable evidence (a quote, an anchor on both sides) for a
    whole-document/chapter-level flag, which otherwise carries only a
    templated accusation sentence (see `checks.reuse.service`'s wording
    templates' own docstring: no real quotable text exists at that
    granularity). Scoped to ONE manuscript, unlike `query_similar_passages`
    above, which answers a different question ("what's the best passage
    match anywhere in the corpus"). Same per-own-passage query loop as
    `_best_passage_match_for` (proven fast at this archive size, 2026-08-20
    research note above) -- just keeps the single best result across every
    own passage instead of one match per own passage, and is deliberately
    NOT gated by `_classify_passage`'s own threshold: this passage does not
    need to independently clear the passage-level similarity bar to serve
    as supporting evidence for a match already established at the coarser
    whole-document/chapter granularity.

    `own_chapter_index`/`matched_chapter_index` (both `None` for a
    whole-document match, both set for a chapter-level one -- the caller's
    own convention, `SimilarityMatch`'s docstring) scope the search to the
    TWO SPECIFIC chapters a chapter-level match is actually about
    (`backend-critic` finding, BUG-153 review, live-reproduced): without
    this, the code picked the single best pairing ANYWHERE in either
    manuscript, which could -- and in the live repro, did -- attach a
    Chapter 1 flag's own text from Chapter 2, self-contradicting the
    finding it was meant to evidence the moment an instructor reads it.
    A whole-document match has no such claim to violate ("any passage
    anywhere in the doc" is exactly what it asserts), so it stays
    unscoped.

    Returns `None` when no passage exists in the (scoped) search space at
    all -- either `matched_manuscript_id` has no passage archive (an
    image-only match, or one ingested before V-072/F7.4 existed), or, for
    a chapter-level match, this specific chapter pair has no passage on
    one or both sides even though OTHER chapters do. The caller treats
    both as "this finding cannot be evidenced" (ticket's own fallback) --
    a real, honest outcome, not a bug: a chapter-level aggregate match can
    be driven by diffuse similarity spread across many passages rather
    than one strong pairing, and that is exactly the case this ticket says
    must not stay high severity."""
    best: PassageMatch | None = None
    best_similarity = -1.0
    for passage in passages:
        if passage.is_reference_list or passage.is_block_quote:
            continue
        if own_chapter_index is not None and passage.chapter_index != own_chapter_index:
            continue
        distance_expr = ManuscriptPassageArchive.embedding.cosine_distance(passage.embedding)
        conditions = [
            ManuscriptPassageArchive.model_id == settings.embedding_model_id,
            ManuscriptPassageArchive.manuscript_id == matched_manuscript_id,
            ManuscriptPassageArchive.is_reference_list.is_(False),
            ManuscriptPassageArchive.is_block_quote.is_(False),
            Manuscript.purged_at.is_(None),
        ]
        if matched_chapter_index is not None:
            conditions.append(ManuscriptPassageArchive.chapter_index == matched_chapter_index)
        row = (
            await session.execute(
                select(
                    ManuscriptPassageArchive,
                    (1 - distance_expr).label("similarity"),
                    Manuscript.group_label,
                    Manuscript.instructor_id,
                )
                .join(Manuscript, Manuscript.id == ManuscriptPassageArchive.manuscript_id)
                .where(*conditions)
                .order_by(distance_expr)
                .limit(1)
            )
        ).first()
        if row is None:
            continue
        similarity = float(row.similarity)
        if similarity <= best_similarity:
            continue
        archive_row: ManuscriptPassageArchive = row[0]
        best_similarity = similarity
        best = PassageMatch(
            own_passage_index=passage.passage_index,
            own_chapter_index=passage.chapter_index,
            own_page=passage.page,
            own_paragraph=passage.paragraph,
            own_char_start=passage.char_start,
            own_char_end=passage.char_end,
            own_text=passage.text,
            own_context_text=passage.context_text,
            own_is_reference_list=passage.is_reference_list,
            own_is_block_quote=passage.is_block_quote,
            level=_classify_passage(similarity, settings) or "high_similarity",
            similarity=similarity,
            matched_manuscript_id=archive_row.manuscript_id,
            matched_instructor_id=row.instructor_id,
            matched_group_label=row.group_label,
            matched_chapter_index=archive_row.chapter_index,
            matched_page=archive_row.page,
            matched_paragraph=archive_row.paragraph,
            matched_char_start=archive_row.char_start,
            matched_char_end=archive_row.char_end,
            matched_text=archive_row.text,
            matched_context_text=archive_row.context_text,
            matched_is_reference_list=archive_row.is_reference_list,
            matched_is_block_quote=archive_row.is_block_quote,
        )
    return best


@dataclass(frozen=True)
class PassageQueryResult:
    matches: list[PassageMatch]
    # Same cold-start disclosure purpose as `OriginalityQueryResult.archive_size_n`
    # (ticket AC5) — shown even when 0.
    passage_archive_size_n: int


async def query_similar_passages(
    session: AsyncSession,
    manuscript_id: int,
    passages: list[PassageEmbedding],
    settings: Settings,
    *,
    include_reference_list: bool = False,
    include_block_quote: bool = False,
    exclude_manuscript_ids: set[int] | None = None,
) -> PassageQueryResult:
    """One query per own passage (see the perf note above for why that's
    fine) — same self/group-sibling exclusion as `query_similar_manuscripts`
    (BUG-050 item 1 applies at every granularity, not just whole-doc/
    chapter). `include_reference_list`/`include_block_quote` default to
    `False` (ticket AC3: "on by default") — the live exploration toggle
    (`app.report.service`) is the only caller that ever passes `True`, and
    it never turns those matches into scored `Flag` rows (see
    `app.checks.reuse.service`'s own module docstring for that split)."""
    exclude = {manuscript_id, *(exclude_manuscript_ids or set())}
    exclude |= await _group_sibling_manuscript_ids(session, manuscript_id)
    archive_size_n = await passage_archive_size(
        session, exclude_manuscript_ids=exclude, settings=settings
    )

    matches: list[PassageMatch] = []
    for passage in passages:
        if not include_reference_list and passage.is_reference_list:
            continue
        if not include_block_quote and passage.is_block_quote:
            continue
        match = await _best_passage_match_for(
            session,
            passage,
            exclude_manuscript_ids=exclude,
            include_reference_list=include_reference_list,
            include_block_quote=include_block_quote,
            settings=settings,
        )
        if match is not None:
            matches.append(match)
    return PassageQueryResult(matches=matches, passage_archive_size_n=archive_size_n)
