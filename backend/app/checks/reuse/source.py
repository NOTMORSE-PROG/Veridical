"""Bounded native-source compatibility for selected F7 candidates (BUG-457)."""

import asyncio
from bisect import bisect_right
from dataclasses import replace

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import GoneError
from app.ingest.analytical_source import AnalyticalSource, analytical_source
from app.models.manuscript import Manuscript
from app.storage.access import read_source


class SourceCache(dict):
    """A request-local cache with a conservative serialized-source budget."""

    def __init__(self):
        super().__init__()
        self.retained_bytes = 0


async def selected_native_source(
    session: AsyncSession,
    manuscript_id: int,
    settings: Settings,
    cache: dict[int, AnalyticalSource | bool],
) -> AnalyticalSource | bool:
    if manuscript_id in cache:
        return cache[manuscript_id]
    if isinstance(cache, SourceCache):
        available = settings.reuse_source_cache_max_bytes - cache.retained_bytes
        if len(cache) >= settings.reuse_source_cache_max_entries or available <= 0:
            return False
        read_limit = min(settings.reuse_source_read_max_bytes, available)
    else:
        read_limit = settings.reuse_source_read_max_bytes
    manuscript = await session.get(Manuscript, manuscript_id)
    if manuscript is None or manuscript.purged_at is not None:
        source = False
    else:
        from app.ingest.service import load_raw_store_async

        try:
            extraction = await read_source(
                session,
                manuscript,
                lambda: load_raw_store_async(settings, manuscript.id, max_bytes=read_limit),
            )
            if isinstance(cache, SourceCache):
                # Reserve the full admitted read, including JSON overhead that
                # the source projection may no longer retain after validation.
                cache.retained_bytes += read_limit
            source = analytical_source(
                extraction, expected_source_sha256=manuscript.content_hash or ""
            )
            from app.checks.reuse.embed import _unstructured_body_blocks, _uses_unstructured_body
            from app.ingest.blockquotes import block_quote_block_ids, block_quote_ids_in_scope
            from app.ingest.normalize import normalize
            from app.ingest.patterns import load_patterns
            from app.ingest.references import select_references

            patterns = load_patterns(settings.ingest_patterns_file)
            chapters = [node for node in extraction.section_tree.nodes if node.level == 1]
            quote_ids = (
                block_quote_ids_in_scope(_unstructured_body_blocks(extraction, patterns), settings)
                if _uses_unstructured_body(chapters, patterns)
                else block_quote_block_ids(extraction, settings)
            )
            references = select_references(extraction, patterns)
            fragments = None
            if not references.issues and not references.membership_issues:
                from app.ingest.source_slices import SourceFragment

                fragments = []
                for block in [references.heading, *references.span, *references.protected_span]:
                    if block is None or not block.text.strip():
                        continue
                    if isinstance(block, SourceFragment):
                        owner = extraction.blocks[block.source_block_index]
                        fragments.append((id(owner), block.source_start, block.source_end))
                    elif any(block is item for item in extraction.blocks):
                        fragments.append((id(block), 0, len(block.text)))
                    else:
                        fragments = None
                        break
                if fragments is not None:
                    fragments = tuple(fragments)
            headings, pending = [], list(extraction.section_tree.nodes)
            while pending:
                node = pending.pop()
                headings.append(node)
                pending.extend(node.children)
            heading_paragraphs = {node.paragraph for node in headings if node.paragraph is not None}
            page_headings = {(node.page, normalize(node.title)) for node in headings}
            source = replace(
                source,
                quotation_block_ids=frozenset(quote_ids),
                reference_block_spans=fragments,
                heading_block_ids=frozenset(
                    id(block)
                    for block in extraction.blocks
                    if (block.paragraph is not None and block.paragraph in heading_paragraphs)
                    or (
                        block.paragraph is None
                        and (block.page, normalize(block.text)) in page_headings
                    )
                ),
            )
        except (FileNotFoundError, GoneError, ValidationError, OverflowError):
            source = False
    cache[manuscript_id] = source
    return source


def selected_passage_supported(
    source: AnalyticalSource | bool, *, text: str, context: str, paragraph: int | None
) -> bool:
    if isinstance(source, bool):
        return source
    needle = " ".join(text.split())
    if any(
        id(block) in source.heading_block_ids and " ".join(block.text.split()) == needle
        for scope in source.scopes
        for block in scope
    ):
        # A shared structural label alone cannot support a reuse warning.
        return False
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
    from app.ingest.quotations import located_word_quotation

    for scope in source.scopes:
        words = await asyncio.to_thread(
            located_word_quotation, source, " ".join(block.text for block in scope), settings
        )
        if words is None or any(word[1] for word in words):
            # Existing aggregate vectors do not record quotation exclusion.
            # Independently located unquoted passage leads remain available.
            return False
    # Existing aggregate vectors have no projection-generation column.
    # A positive/unknown native inventory cannot certify their old input.
    # Independently bound passage leads remain available separately.
    return source.state == "not_applicable" or (
        source.state == "complete" and source.occurrence_count == 0
    )


async def passage_match_supported(
    session, manuscript_id, match, settings, cache, *, require_unquoted=False
) -> bool:
    own = await selected_native_source(session, manuscript_id, settings, cache)
    matched = await selected_native_source(session, match.matched_manuscript_id, settings, cache)
    supported = selected_passage_supported(
        own, text=match.own_text, context=match.own_context_text, paragraph=match.own_paragraph
    ) and selected_passage_supported(
        matched,
        text=match.matched_text,
        context=match.matched_context_text,
        paragraph=match.matched_paragraph,
    )
    if supported and (match.literal_overlap is not None or require_unquoted):
        from app.ingest.quotations import located_quote_status, located_word_reference

        own_quote = await asyncio.to_thread(located_quote_status, own, match.own_text, settings)
        matched_quote = await asyncio.to_thread(
            located_quote_status, matched, match.matched_text, settings
        )
        if own_quote is None or matched_quote is None:
            return False
        if require_unquoted and (
            own_quote[1]
            or matched_quote[1]
            or match.own_is_block_quote
            or match.matched_is_block_quote
        ):
            return False
        if match.literal_overlap is None:
            return True
        own_refs = await asyncio.to_thread(located_word_reference, own, match.own_text, settings)
        matched_refs = await asyncio.to_thread(
            located_word_reference, matched, match.matched_text, settings
        )
        return bool(
            own_quote is not None
            and matched_quote is not None
            and own_refs is not None
            and matched_refs is not None
            and own_quote[1] == match.own_is_block_quote
            and matched_quote[1] == match.matched_is_block_quote
            and any(word[1] for word in own_refs) == match.own_is_reference_list
            and any(word[1] for word in matched_refs) == match.matched_is_reference_list
        )
    return supported
