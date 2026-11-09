"""Similarity query (F7.2, V-037) against the archive V-036 built —
whole-document AND chapter-level (ticket AC: "a chapter transplanted into
a new doc -> chapter-level flag"), real pgvector `<=>` cosine-distance
queries via `Vector.cosine_distance()`.

**Never match against self**: since V-077 a check never writes its own
embeddings, but a manuscript can still have corpus rows of its own when it is
itself an active Library entry (a legacy entry re-checked from the dashboard),
so every query excludes the current `manuscript_id`.

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

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from sqlalchemy import Numeric, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.reuse.embed import DocumentEmbeddings, PassageEmbedding
from app.checks.reuse.literal import (
    NORMALIZATION,
    literal_candidates,
    literal_policy_record,
    literal_spans,
    literal_window,
    maximal_literal_matches,
)
from app.checks.reuse.source import SourceCache, coarse_candidate_supported, passage_match_supported
from app.config import Settings
from app.groups.service import DEFAULT_GROUP_LABEL, normalize_group_name
from app.ingest.quotations import (
    located_quote_status,
    located_word_quotation,
    located_word_reference,
)
from app.models.enums import LibraryEntryStatus, ManuscriptOrigin
from app.models.group import Group
from app.models.library import LibraryEntry
from app.models.manuscript import (
    Manuscript,
    ManuscriptArchive,
    ManuscriptChapterArchive,
    ManuscriptPassageArchive,
)

_UNGROUPED_NORMALIZED = normalize_group_name(DEFAULT_GROUP_LABEL)

MatchLevel = Literal["exact_duplicate", "high_similarity", "literal_overlap"]


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
    # BUG-153 (backend review finding, live-reproduced): the index twins
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
    n_source_candidates_withheld: int = 0
    library_passage_indexing: dict | None = None


def _classify(similarity: float, settings: Settings) -> MatchLevel | None:
    if similarity >= settings.reuse_exact_duplicate_threshold:
        return "exact_duplicate"
    if similarity >= settings.reuse_high_similarity_threshold:
        return "high_similarity"
    return None


def _indexing_scope_predicates():
    """SQL counterpart of the strict, source-bound snapshot projector.

    CASE protects row-dependent casts; malformed JSON stays unknown. Numeric
    avoids narrowing otherwise valid integer metadata to a fixed-width cast.
    """
    value = LibraryEntry.passage_indexing_snapshot

    def integer_field(key):
        field = value[key]
        return case(
            (
                and_(
                    func.jsonb_typeof(field) == "number",
                    field.astext.op("~")(r"^[0-9]+$"),
                ),
                cast(field.astext, Numeric),
            ),
            else_=None,
        )

    limit = integer_field("limit")
    found = integer_field("passages_found")
    indexed = integer_field("passages_indexed")
    measured = and_(
        value["inventory_state"].astext == "measured",
        found.is_not(None),
        indexed == func.least(found, limit),
    )
    unavailable = and_(
        value["inventory_state"].astext == "unavailable",
        func.jsonb_typeof(value["passages_found"]) == "null",
        indexed == 0,
    )
    valid = func.coalesce(
        and_(
            func.jsonb_typeof(value) == "object",
            value["version"].astext == "v1",
            func.jsonb_typeof(value["source_sha256"]) == "string",
            value["source_sha256"].astext.op("~")(r"^[a-f0-9]{64}$"),
            value["source_sha256"].astext == Manuscript.content_hash,
            func.jsonb_typeof(value["model_id"]) == "string",
            func.length(value["model_id"].astext) > 0,
            value["model_id"].astext == ManuscriptArchive.model_id,
            limit.is_not(None),
            indexed.is_not(None),
            or_(measured, unavailable),
        ),
        False,
    )
    return and_(valid, measured, indexed < found), and_(valid, unavailable), ~valid


async def _archive_scope(
    session: AsyncSession, *, exclude_manuscript_ids: set[int], settings: Settings
) -> tuple[int, dict]:
    # One snapshot and bounded response for count and indexing facts.
    # LEFT JOIN preserves orphan legacy archive rows as unknown scope.
    partial, unavailable, unknown = _indexing_scope_predicates()
    row = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.bool_or(partial), False),
                func.coalesce(func.bool_or(unavailable), False),
                func.coalesce(func.bool_or(unknown), False),
            )
            .select_from(ManuscriptArchive)
            .outerjoin(Manuscript, Manuscript.id == ManuscriptArchive.manuscript_id)
            .outerjoin(LibraryEntry, LibraryEntry.manuscript_id == ManuscriptArchive.manuscript_id)
            .where(
                ManuscriptArchive.model_id == settings.embedding_model_id,
                ManuscriptArchive.manuscript_id.notin_(exclude_manuscript_ids),
            )
        )
    ).one()
    return row[0], {
        "version": "v1",
        "partial": row[1],
        "unavailable": row[2],
        "not_recorded": row[3],
    }


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
    `None` -- a manuscript ingested before this column existed).

    V-077 (D-028): scoped to the instructor's own ACTIVE Library entries.
    Only those are comparison sources now; an earlier dashboard upload of the
    same bytes is not in the Library, so naming it as the matched source
    would describe a comparison that never happened."""
    if content_hash is None:
        return None
    row = (
        await session.execute(
            select(Manuscript.id)
            .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
            .where(
                Manuscript.instructor_id == instructor_id,
                Manuscript.content_hash == content_hash,
                Manuscript.id != manuscript_id,
                Manuscript.purged_at.is_(None),
                LibraryEntry.status == LibraryEntryStatus.active,
            )
            .order_by(Manuscript.id)
            .limit(1)
        )
    ).first()
    return row[0] if row is not None else None


async def canonical_library_copies(
    session: AsyncSession,
    matched_manuscript_ids: set[int],
    requesting_instructor_id: int | None,
    *,
    exclude_manuscript_ids: set[int],
) -> dict[int, tuple[int, int]]:
    """V-077: map each matched Library manuscript to ONE representative of
    its byte-identical group (same `content_hash`, active entries only), so
    two instructors adding the same file cannot split one source into two
    findings. The requester's own copy is preferred when one exists (so the
    BUG-140 same-instructor rule still sees it), then the lowest id. Returns
    only ids that change, as `{matched_id: (canonical_id, canonical_owner)}`;
    an id with no preferred identical sibling is absent. Nothing about the other
    copies is disclosed beyond what an exact-duplicate match already reveals:
    the representative is always byte-identical to a record that matched,
    though it need not itself have been among the nearest results
    (security review, 2026-10-13). `exclude_manuscript_ids` is the query's own exclusion set (the
    checked manuscript and its group siblings), so a re-check of a Library
    manuscript can never be redirected onto itself."""
    if not matched_manuscript_ids:
        return {}
    hashes = dict(
        (
            await session.execute(
                select(Manuscript.id, Manuscript.content_hash).where(
                    Manuscript.id.in_(matched_manuscript_ids),
                    Manuscript.content_hash.is_not(None),
                )
            )
        ).all()
    )
    if not hashes:
        return {}
    siblings = (
        await session.execute(
            select(Manuscript.id, Manuscript.content_hash, Manuscript.instructor_id)
            .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
            .where(
                Manuscript.content_hash.in_(set(hashes.values())),
                Manuscript.id.notin_(exclude_manuscript_ids),
                LibraryEntry.status == LibraryEntryStatus.active,
            )
        )
    ).all()
    best: dict[str, tuple[bool, int, int]] = {}
    for sibling_id, content_hash, instructor_id in siblings:
        key = (instructor_id != requesting_instructor_id, sibling_id, instructor_id)
        if content_hash not in best or key < best[content_hash]:
            best[content_hash] = key
    remapped: dict[int, tuple[int, int]] = {}
    for mid, content_hash in hashes.items():
        if content_hash in best and best[content_hash][1] != mid:
            _, canonical_id, canonical_owner = best[content_hash]
            remapped[mid] = (canonical_id, canonical_owner)
    return remapped


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
    # V-077 (backend review): counts the instructor's other CHECK uploads only.
    # Library submissions are not checks, so adding a past term to the Library
    # does not silence this banner on the instructor's first real check.
    other_count = await session.scalar(
        select(func.count())
        .select_from(Manuscript)
        .where(
            Manuscript.instructor_id == row.instructor_id,
            Manuscript.id != manuscript_id,
            Manuscript.origin == ManuscriptOrigin.check_upload,
        )
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
    # Kept explicit anyway (backend review review, 2026-08-19): a silent
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


async def comparison_exclusions(session: AsyncSession, manuscript_id: int) -> set[int]:
    """The ids every similarity query in this module excludes: the checked
    manuscript itself and its own group's other uploads."""
    return {manuscript_id} | await _group_sibling_manuscript_ids(session, manuscript_id)


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
    archive_size_n, indexing = await _archive_scope(
        session, exclude_manuscript_ids=exclude, settings=settings
    )

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

    source_cache = SourceCache()
    supported = []
    own_supported = await coarse_candidate_supported(session, manuscript_id, settings, source_cache)
    for match in matches:
        if own_supported and await coarse_candidate_supported(
            session, match.matched_manuscript_id, settings, source_cache
        ):
            supported.append(match)
    return OriginalityQueryResult(
        matches=supported,
        archive_size_n=archive_size_n,
        library_passage_indexing=indexing,
        n_source_candidates_withheld=len(matches) - len(supported),
    )


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
    # Scoped supporting evidence may exist below standalone flag thresholds.
    level: MatchLevel | None
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
    literal_overlap: dict | None = None


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
    candidates = await _passage_candidates_for(
        session,
        passage,
        exclude_manuscript_ids=exclude_manuscript_ids,
        include_reference_list=include_reference_list,
        include_block_quote=include_block_quote,
        settings=settings,
        limit=1,
    )
    return candidates[0] if candidates else None


async def _passage_candidates_for(
    session: AsyncSession,
    passage: PassageEmbedding,
    *,
    exclude_manuscript_ids: set[int],
    include_reference_list: bool,
    include_block_quote: bool,
    settings: Settings,
    limit: int,
) -> list[PassageMatch]:
    """The `limit` nearest Library passages that clear the passage bar,
    nearest first."""
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

    rows = (
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
            .limit(limit)
        )
    ).all()
    found = []
    for row in rows:
        level = _classify_passage(float(row.similarity), settings)
        if level is None:
            break  # nearest first: every later row is further away
        found.append(_passage_match(passage, row, level))
    return found


def _passage_match(passage: PassageEmbedding, row, level: MatchLevel) -> PassageMatch:
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
    (backend review finding, BUG-153 review, live-reproduced): without
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
    source_cache = SourceCache()
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
        candidate = PassageMatch(
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
            level=_classify_passage(similarity, settings),
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
        # The caller's own source and quotation scope are validated by the
        # main coarse query path before this fallback is used;
        # this fallback must independently fence the selected Library side.
        from app.checks.reuse.source import selected_native_source, selected_passage_supported

        source = await selected_native_source(
            session, matched_manuscript_id, settings, source_cache
        )
        if selected_passage_supported(
            source,
            text=candidate.matched_text,
            context=candidate.matched_context_text,
            paragraph=candidate.matched_paragraph,
        ):
            quotation = await asyncio.to_thread(
                located_quote_status, source, candidate.matched_text, settings
            )
            if quotation is None or quotation[1]:
                continue
            best_similarity = similarity
            best = candidate
    return best


@dataclass(frozen=True)
class PassageQueryResult:
    matches: list[PassageMatch]
    # Same cold-start disclosure purpose as `OriginalityQueryResult.archive_size_n`
    # (ticket AC5) — shown even when 0.
    passage_archive_size_n: int
    n_source_candidates_withheld: int = 0
    # BUG-620: own passages whose nearest supported candidates all failed the
    # caller's `confirm` test (the nearest of them, for the audit).
    unconfirmed: list[PassageMatch] = field(default_factory=list)
    literal_scope: dict | None = None


async def _literal_matches_for(
    session,
    manuscript_id,
    passage,
    *,
    exclude,
    include_reference_list,
    include_block_quote,
    settings,
    cache,
    scope,
    deadline,
):
    def limitation(reason):
        scope["unassessed"].append({"passage_index": passage.passage_index, "reason": reason})

    window, start, issue = literal_window(passage.text, passage.context_text, passage.char_start)
    if issue:
        limitation(issue)
    passage = replace(passage, text=window, char_start=start, char_end=start + len(window))
    if settings.reuse_passage_context_words < settings.reuse_literal_min_words:
        limitation("boundary_context_configuration_not_assessed")
    if settings.reuse_passage_chunk_words < settings.reuse_literal_min_words:
        limitation("boundary_chunk_configuration_not_assessed")
    length = settings.reuse_literal_min_words - settings.reuse_literal_probe_words + 1
    bad = settings.reuse_literal_probe_words - 1
    stride = settings.reuse_literal_probe_stride
    if length // stride <= (bad + stride - 1) // stride:
        limitation("boundary_probe_configuration_not_assessed")
    if scope["queries"] >= settings.reuse_literal_max_queries or time.monotonic() >= deadline:
        limitation("query_or_time_budget")
        return []
    scope["queries"] += 1
    rows, issues = await literal_candidates(
        session,
        passage,
        exclude=exclude,
        include_reference_list=include_reference_list,
        include_block_quote=include_block_quote,
        settings=settings,
        scope=scope,
    )
    for issue in issues:
        limitation(issue)
    from app.checks.reuse.source import selected_native_source

    matches = []
    for row in rows:
        if time.monotonic() >= deadline:
            limitation("time_budget")
            break
        archive = row[0]
        matched_window, matched_start, issue = literal_window(
            archive.text, archive.context_text, archive.char_start
        )
        if issue:
            limitation(issue)
        if (
            len(passage.text.split()) * len(matched_window.split())
            > settings.reuse_literal_token_work_limit - scope["token_work"]
        ):
            limitation("token_work_budget")
            break
        own_source = await selected_native_source(session, manuscript_id, settings, cache)
        matched_source = await selected_native_source(
            session, archive.manuscript_id, settings, cache
        )
        own_words = await asyncio.to_thread(
            located_word_quotation, own_source, passage.text, settings
        )
        matched_words = await asyncio.to_thread(
            located_word_quotation, matched_source, matched_window, settings
        )
        own_refs = await asyncio.to_thread(
            located_word_reference, own_source, passage.text, settings
        )
        matched_refs = await asyncio.to_thread(
            located_word_reference, matched_source, matched_window, settings
        )
        if own_words is None or matched_words is None or own_refs is None or matched_refs is None:
            limitation("source_or_quotation_not_assessed")
            continue
        try:
            spans, work, overflow = await asyncio.to_thread(
                literal_spans,
                passage.text,
                matched_window,
                settings.reuse_literal_token_work_limit - scope["token_work"],
                minimum=settings.reuse_literal_min_words,
                span_limit=settings.reuse_literal_span_limit,
                own_quotes=[
                    (quote[1] and not include_block_quote)
                    or (ref[1] and not include_reference_list)
                    for quote, ref in zip(own_words, own_refs, strict=True)
                ],
                matched_quotes=[
                    (quote[1] and not include_block_quote)
                    or (ref[1] and not include_reference_list)
                    for quote, ref in zip(matched_words, matched_refs, strict=True)
                ],
            )
        except OverflowError:
            limitation("token_work_budget")
            break
        scope["token_work"] += work
        scope["candidates_verified"] += 1
        if any(word[1] for word in own_refs) or any(word[1] for word in matched_refs):
            scope["reference_exclusions"].append(
                {
                    "passage_index": passage.passage_index,
                    "matched_manuscript_id": archive.manuscript_id,
                    "own_words_excluded": sum(word[1] for word in own_refs)
                    if not include_reference_list
                    else 0,
                    "matched_words_excluded": sum(word[1] for word in matched_refs)
                    if not include_reference_list
                    else 0,
                }
            )
        if any(word[1] for word in own_words) or any(word[1] for word in matched_words):
            scope["quotation_exclusions"].append(
                {
                    "passage_index": passage.passage_index,
                    "matched_manuscript_id": archive.manuscript_id,
                    "own_words_excluded": sum(word[1] for word in own_words)
                    if not include_block_quote
                    else 0,
                    "matched_words_excluded": sum(word[1] for word in matched_words)
                    if not include_block_quote
                    else 0,
                }
            )
        if overflow:
            limitation("span_limit")
        for span in spans:
            own_text = passage.text[span.own_start : span.own_end]
            matched_text = matched_window[span.matched_start : span.matched_end]
            own_quote = await asyncio.to_thread(
                located_quote_status, own_source, own_text, settings
            )
            matched_quote = await asyncio.to_thread(
                located_quote_status, matched_source, matched_text, settings
            )
            if own_quote is None or matched_quote is None:
                limitation("source_span_ambiguous")
                continue
            own_span_refs = await asyncio.to_thread(
                located_word_reference, own_source, own_text, settings
            )
            matched_span_refs = await asyncio.to_thread(
                located_word_reference, matched_source, matched_text, settings
            )
            if own_span_refs is None or matched_span_refs is None:
                limitation("reference_span_not_assessed")
                continue
            match = _literal_match(
                passage,
                row,
                span,
                own_quote,
                matched_quote,
                matched_window=matched_window,
                matched_start=matched_start,
            )
            match = replace(
                match,
                own_is_reference_list=any(word[1] for word in own_span_refs),
                matched_is_reference_list=any(word[1] for word in matched_span_refs),
            )
            if await passage_match_supported(session, manuscript_id, match, settings, cache):
                matches.append(match)
            else:
                limitation("source_span_not_supported")
    return matches


def _literal_match(
    passage, row, span, own_quote, matched_quote, *, matched_window=None, matched_start=None
):
    archive = row[0]
    matched_window = archive.text if matched_window is None else matched_window
    matched_start = archive.char_start if matched_start is None else matched_start
    return PassageMatch(
        own_passage_index=passage.passage_index,
        own_chapter_index=passage.chapter_index,
        own_page=own_quote[2],
        own_paragraph=own_quote[0],
        own_char_start=passage.char_start + span.own_start,
        own_char_end=passage.char_start + span.own_end,
        own_text=passage.text[span.own_start : span.own_end],
        own_context_text=passage.context_text,
        own_is_reference_list=passage.is_reference_list,
        own_is_block_quote=own_quote[1],
        level="literal_overlap",
        similarity=float(row.similarity),
        matched_manuscript_id=archive.manuscript_id,
        matched_group_label=row.group_label,
        matched_instructor_id=row.instructor_id,
        matched_chapter_index=archive.chapter_index,
        matched_page=matched_quote[2],
        matched_paragraph=matched_quote[0],
        matched_char_start=matched_start + span.matched_start,
        matched_char_end=matched_start + span.matched_end,
        matched_text=matched_window[span.matched_start : span.matched_end],
        matched_context_text=archive.context_text,
        matched_is_reference_list=archive.is_reference_list,
        matched_is_block_quote=matched_quote[1],
        literal_overlap={
            "normalization": NORMALIZATION,
            "word_count": span.word_count,
            "own_start": passage.char_start + span.own_start,
            "own_end": passage.char_start + span.own_end,
            "matched_start": matched_start + span.matched_start,
            "matched_end": matched_start + span.matched_end,
            "cosine_scope": "original_passage_vectors",
        },
    )


async def query_similar_passages(
    session: AsyncSession,
    manuscript_id: int,
    passages: list[PassageEmbedding],
    settings: Settings,
    *,
    include_reference_list: bool = False,
    include_block_quote: bool = False,
    exclude_manuscript_ids: set[int] | None = None,
    confirm: Callable[[PassageMatch], bool] | None = None,
    candidates: int = 1,
) -> PassageQueryResult:
    """One query per own passage (see the perf note above for why that's
    fine) — same self/group-sibling exclusion as `query_similar_manuscripts`
    (BUG-050 item 1 applies at every granularity, not just whole-doc/
    chapter). `include_reference_list`/`include_block_quote` default to
    `False` (ticket AC3: "on by default") — the live exploration toggle
    (`app.report.service`) is the only caller that ever passes `True`, and
    it never turns those matches into scored `Flag` rows (see
    `app.checks.reuse.service`'s own module docstring for that split).

    With `confirm` (BUG-620), the nearest `candidates` Library passages are
    tried in order and the first that is supported and passes `confirm` is
    the match: a nearer passage that shares only a topic must not hide the
    real source one rank below it."""
    exclude = {manuscript_id, *(exclude_manuscript_ids or set())}
    exclude |= await _group_sibling_manuscript_ids(session, manuscript_id)
    archive_size_n = await passage_archive_size(
        session, exclude_manuscript_ids=exclude, settings=settings
    )

    matches: list[PassageMatch] = []
    unconfirmed: list[PassageMatch] = []
    literal_intervals: set[tuple] = set()
    source_cache = SourceCache()

    withheld = 0
    literal_scope = (
        {
            "method": NORMALIZATION,
            "policy": literal_policy_record(settings),
            "queries": 0,
            "candidates_verified": 0,
            "token_work": 0,
            "quoted_spans_excluded": 0,
            "quotation_exclusions": [],
            "reference_exclusions": [],
            "unassessed": [],
        }
        if settings.reuse_literal_enabled
        else None
    )
    deadline = time.monotonic() + settings.reuse_literal_time_budget_seconds
    for passage in passages:
        if not include_reference_list and passage.is_reference_list:
            continue
        found = []
        if include_block_quote or not passage.is_block_quote:
            found = await _passage_candidates_for(
                session,
                passage,
                exclude_manuscript_ids=exclude,
                include_reference_list=include_reference_list,
                include_block_quote=include_block_quote,
                settings=settings,
                limit=max(1, candidates) if confirm is not None else 1,
            )
        supported = []
        native_supported = False
        quotation_unassessed = False
        for candidate in found:
            if not await passage_match_supported(
                session, manuscript_id, candidate, settings, source_cache
            ):
                continue
            native_supported = True
            from app.checks.reuse.source import selected_native_source

            own_source = await selected_native_source(
                session, manuscript_id, settings, source_cache
            )
            matched_source = await selected_native_source(
                session, candidate.matched_manuscript_id, settings, source_cache
            )
            own_quote = await asyncio.to_thread(
                located_quote_status, own_source, candidate.own_text, settings
            )
            matched_quote = await asyncio.to_thread(
                located_quote_status, matched_source, candidate.matched_text, settings
            )
            candidate = replace(
                candidate,
                own_is_block_quote=candidate.own_is_block_quote or bool(own_quote and own_quote[1]),
                matched_is_block_quote=candidate.matched_is_block_quote
                or bool(matched_quote and matched_quote[1]),
            )
            if not include_block_quote and any(
                value and value[1] for value in (own_quote, matched_quote)
            ):
                if literal_scope is not None:
                    literal_scope["quoted_spans_excluded"] += 1
                continue
            if not include_block_quote and any(
                value is None for value in (own_quote, matched_quote)
            ):
                # Valid native prose can still have an unclosed quotation or
                # missing quotation provenance. Neither establishes unquoted text.
                quotation_unassessed = True
                if literal_scope is not None:
                    literal_scope["unassessed"].append(
                        {
                            "passage_index": passage.passage_index,
                            "reason": "source_or_quotation_not_assessed",
                            "matched_manuscript_id": candidate.matched_manuscript_id,
                        }
                    )
                continue
            supported.append(candidate)
            if confirm is None or confirm(candidate):
                matches.append(candidate)
                break
        else:
            if found and (not native_supported or quotation_unassessed):
                withheld += 1
            if supported:
                unconfirmed.append(supported[0])
        if literal_scope is not None:
            literal_matches = await _literal_matches_for(
                session,
                manuscript_id,
                passage,
                exclude=exclude,
                include_reference_list=include_reference_list,
                include_block_quote=include_block_quote,
                settings=settings,
                cache=source_cache,
                scope=literal_scope,
                deadline=deadline,
            )
            for literal in literal_matches:
                if confirm is not None and not confirm(literal):
                    unconfirmed.append(literal)
                    literal_scope["unassessed"].append(
                        {
                            "passage_index": passage.passage_index,
                            "reason": "confirmation_withheld_literal_proof",
                            "matched_manuscript_id": literal.matched_manuscript_id,
                            "literal_overlap": literal.literal_overlap,
                        }
                    )
                    continue
                interval = (
                    literal.own_chapter_index,
                    literal.own_char_start,
                    literal.own_char_end,
                    literal.matched_manuscript_id,
                    literal.matched_chapter_index,
                    literal.matched_char_start,
                    literal.matched_char_end,
                    literal.own_is_block_quote,
                    literal.matched_is_block_quote,
                    literal.own_is_reference_list,
                    literal.matched_is_reference_list,
                )
                # Adjacent stored windows can expose the same source interval.
                if interval in literal_intervals:
                    continue
                literal_intervals.add(interval)
                existing = next(
                    (
                        index
                        for index, item in enumerate(matches)
                        if item.own_passage_index == passage.passage_index
                        and item.matched_manuscript_id == literal.matched_manuscript_id
                        and item.matched_chapter_index == literal.matched_chapter_index
                        and item.own_char_start
                        <= literal.own_char_start
                        < literal.own_char_end
                        <= item.own_char_end
                        and item.matched_char_start
                        <= literal.matched_char_start
                        < literal.matched_char_end
                        <= item.matched_char_end
                        and literal.own_text in item.own_text
                        and literal.matched_text in item.matched_text
                        and item.own_is_block_quote == literal.own_is_block_quote
                        and item.matched_is_block_quote == literal.matched_is_block_quote
                        and item.own_is_reference_list == literal.own_is_reference_list
                        and item.matched_is_reference_list == literal.matched_is_reference_list
                        and item.literal_overlap is None
                        and item.level != "literal_overlap"
                    ),
                    None,
                )
                if existing is None:
                    matches.append(literal)
                else:
                    matches[existing] = replace(
                        matches[existing], literal_overlap=literal.literal_overlap
                    )
    original_match_count = len(matches)
    matches, dedup_unassessed = maximal_literal_matches(
        matches, settings.reuse_literal_dedup_work_limit
    )
    if literal_scope is not None:
        literal_scope["contained_proofs_omitted"] = original_match_count - len(matches)
        literal_scope["unassessed"].extend(
            {"passage_index": index, "reason": "dedup_work_budget"}
            for index in sorted(dedup_unassessed)
        )

    return PassageQueryResult(
        matches=matches,
        passage_archive_size_n=archive_size_n,
        n_source_candidates_withheld=withheld,
        unconfirmed=unconfirmed,
        literal_scope=literal_scope,
    )
