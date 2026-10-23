"""Bounded native-source compatibility for selected F7 candidates (BUG-457)."""

from bisect import bisect_right

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import GoneError
from app.ingest.analytical_source import AnalyticalSource, analytical_source
from app.models.manuscript import Manuscript
from app.storage.access import read_source


async def selected_native_source(
    session: AsyncSession,
    manuscript_id: int,
    settings: Settings,
    cache: dict[int, AnalyticalSource | bool],
) -> AnalyticalSource | bool:
    if manuscript_id in cache:
        return cache[manuscript_id]
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None or manuscript.purged_at is not None:
        source = False
    else:
        from app.ingest.service import load_raw_store_async

        try:
            extraction = await read_source(
                session, manuscript, lambda: load_raw_store_async(settings, manuscript.id)
            )
            source = analytical_source(
                extraction, expected_source_sha256=manuscript.content_hash or ""
            )
        except (FileNotFoundError, GoneError, ValidationError):
            source = False
    cache[manuscript_id] = source
    return source


def selected_passage_supported(
    source: AnalyticalSource | bool, *, text: str, context: str, paragraph: int | None
) -> bool:
    if isinstance(source, bool):
        return source
    if source.state == "not_applicable":
        # The retained extraction identifies PDF capability even when the
        # caller's filename has a Word suffix. Existing PDF guards still apply.
        return True
    if source.state != "complete" or not source.scopes:
        return False

    def normalize(value):
        return " ".join(value.split())

    needle, surrounding = normalize(text), normalize(context)
    if not needle or needle not in surrounding:
        return False
    locations = set()
    context_supported = False
    for scope in source.scopes:
        normalized = [normalize(block.text) for block in scope]
        joined = " ".join(normalized)
        if surrounding in joined:
            context_supported = True
        offset = 0
        ranges = []
        for block, block_text in zip(scope, normalized, strict=True):
            end = offset + len(block_text)
            ranges.append((offset, end, block.paragraph))
            offset = end + 1
        starts = [start for start, _, _ in ranges]
        position = joined.find(needle)
        while position >= 0:
            index = bisect_right(starts, position) - 1
            if index >= 0:
                _, end, anchor = ranges[index]
                if position < end:
                    locations.add(anchor)
            position = joined.find(needle, position + 1)
    return context_supported and locations == {paragraph} and paragraph is not None


async def coarse_candidate_supported(session, manuscript_id, settings, cache) -> bool:
    source = await selected_native_source(session, manuscript_id, settings, cache)
    if isinstance(source, bool):
        return source
    # Existing aggregate vectors have no projection-generation column.
    # A positive/unknown native inventory cannot certify their old input.
    # Independently bound passage leads remain available separately.
    return source.state == "not_applicable" or (
        source.state == "complete" and source.occurrence_count == 0
    )


async def passage_match_supported(session, manuscript_id, match, settings, cache) -> bool:
    own = await selected_native_source(session, manuscript_id, settings, cache)
    matched = await selected_native_source(session, match.matched_manuscript_id, settings, cache)
    return selected_passage_supported(
        own, text=match.own_text, context=match.own_context_text, paragraph=match.own_paragraph
    ) and selected_passage_supported(
        matched,
        text=match.matched_text,
        context=match.matched_context_text,
        paragraph=match.matched_paragraph,
    )
