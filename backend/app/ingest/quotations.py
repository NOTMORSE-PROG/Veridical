"""Source-located quotation spans; missing provenance remains unknown."""

import json
import re
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

from app.config import Settings
from app.ingest.analytical_source import AnalyticalSource
from app.ingest.blockquotes import block_quote_ids_in_scope


@lru_cache
def quotation_policy(path: str | None = None) -> dict:
    source = Path(path) if path else Path(__file__).parent / "data" / "quotation_policy.json"
    return json.loads(source.read_text(encoding="utf-8"))


def inline_quote_spans(text: str, policy: dict) -> list[tuple[int, int]] | None:
    spans = []
    for opening, closing in policy["delimiter_pairs"]:
        pending = None
        for index, char in enumerate(text):
            if (
                opening == closing == "'"
                and pending is None
                and index
                and text[index - 1].isalnum()
            ):
                continue
            # Apostrophes inside words are not quotation delimiters.
            if (
                char in (opening, closing)
                and index
                and index + 1 < len(text)
                and text[index - 1].isalnum()
                and text[index + 1].isalnum()
            ):
                continue
            if pending is None and char == opening:
                pending = index
            elif pending is not None and char == closing:
                spans.append((pending, index + 1))
                pending = None
            elif char == closing and opening != closing:
                return None
        if pending is not None:
            return None
    return spans


def merged_spans(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _overlaps(spans, ends, start, end):
    index = bisect_right(ends, start)
    return index < len(spans) and spans[index][0] < end


def located_word_quotation(
    source: AnalyticalSource | bool, excerpt: str, settings: Settings, *, _scope_cache=None
) -> list[tuple[int | None, bool, int | None]] | None:
    """Locate one source span; retain quotation membership per whitespace token."""
    return _located_word_protection(
        source, excerpt, settings, references=False, _scope_cache=_scope_cache
    )


def located_word_reference(source, excerpt, settings):
    """Retain exact bibliography membership when passage context is widened."""
    return _located_word_protection(source, excerpt, settings, references=True)


def _located_word_protection(source, excerpt, settings, *, references, _scope_cache=None):
    if isinstance(source, bool) or not source.scopes:
        return None
    if references and source.reference_block_spans is None:
        return None
    if not references and source.state == "not_applicable" and source.quotation_block_ids is None:
        return None
    policy = quotation_policy(settings.reuse_quotation_policy_file)
    reference_by_block = {}
    if references:
        for owner, start, end in source.reference_block_spans:
            reference_by_block.setdefault(owner, []).append((start, end))
        reference_by_block = {
            owner: merged_spans(spans) for owner, spans in reference_by_block.items()
        }
    needle = " ".join(excerpt.split())
    if not needle:
        return None
    locations = []
    for scope in source.scopes:
        cache_key = (
            id(source),
            id(scope),
            id(policy),
            settings.block_quote_indent_threshold_points,
            settings.block_quote_min_words,
        )
        cached = None if references or _scope_cache is None else _scope_cache.get(cache_key)
        if cached is not None and cached[0] is source and cached[1] is scope:
            _, _, joined, ranges, inline_spans, quote_ids = cached
        else:
            text_parts, ranges, offset = [], [], 0
            quote_ids = (
                source.quotation_block_ids
                if source.quotation_block_ids is not None
                else block_quote_ids_in_scope(list(scope), settings)
            )
            for block in scope:
                text = " ".join(block.text.split())
                text_parts.append(text)
                ranges.append((offset, offset + len(text), block))
                offset += len(text) + 1
            joined = " ".join(text_parts)
            inline_spans = [] if references else inline_quote_spans(joined, policy)
            if not references and _scope_cache is not None:
                # A caller may reuse this projection only during one immutable
                # extraction. Ownership and unknown styles are still checked
                # for each excerpt; manuscript text is never cached globally.
                _scope_cache[cache_key] = (source, scope, joined, ranges, inline_spans, quote_ids)
        if references:
            for base, _end, block in ranges:
                position = base
                intervals = reference_by_block.get(id(block), ())
                ends = [end for _start, end in intervals]
                run_start, run_end = None, None
                for token in re.finditer(r"\S+", block.text):
                    end = position + len(token.group())
                    if _overlaps(intervals, ends, token.start(), token.end()):
                        if run_start is None:
                            run_start = position
                        run_end = end
                    elif run_start is not None:
                        inline_spans.append((run_start, run_end))
                        run_start = None
                    position = end + 1
                if run_start is not None:
                    inline_spans.append((run_start, run_end))
        for match in re.finditer(re.escape(needle), joined):
            start, end = match.span()
            owners = [(a, b, block) for a, b, block in ranges if a < end and b > start]
            if not owners:
                continue
            uncertain = inline_spans is None
            protected = list(inline_spans or [])
            for a, _b, block in owners:
                if (
                    not references
                    and source.state != "not_applicable"
                    and block.paragraph_style is None
                ):
                    uncertain = True
                if not references and (
                    (block.paragraph_style or "").casefold() in policy["paragraph_styles"]
                    or id(block) in quote_ids
                ):
                    protected.append((a, _b))
            words = []
            protected = merged_spans(protected)
            protected_ends = [b for _a, b in protected]
            owner_starts = [a for a, _b, _block in owners]
            for word in re.finditer(r"\S+", needle):
                left, right = start + word.start(), start + word.end()
                owner = owners[bisect_right(owner_starts, left) - 1][2]
                quoted = _overlaps(protected, protected_ends, left, right)
                words.append((owner.paragraph, quoted, owner.page))
            locations.append(None if uncertain else words)
    return locations[0] if len(locations) == 1 else None


def located_quote_status(
    source, excerpt, settings, *, _scope_cache=None
) -> tuple[int | None, bool, int | None] | None:
    words = located_word_quotation(source, excerpt, settings, _scope_cache=_scope_cache)
    return (words[0][0], any(word[1] for word in words), words[0][2]) if words else None
