"""F6 source-bound notation adaptation and neutral textual coverage inventory."""

import hashlib
import io
import json
import math
import re
from array import array
from bisect import bisect_right
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from statcheck.helpers_parse_stats import extract_pattern
from statcheck.regex import RGX_NHST

from app.ingest.analytical_source import analytical_source

if TYPE_CHECKING:
    from app.checks.forensics.extract import ReportedStat
    from app.ingest.schemas import ExtractionResult


@lru_cache
def notation_policy() -> dict:
    content = (Path(__file__).parent / "data" / "forensics_notation.json").read_bytes()
    policy = json.loads(content)
    policy["sha256"] = hashlib.sha256(content).hexdigest()
    return policy


def _pattern(value: str, policy: dict) -> str:
    observed = policy["observed_number"].replace("$NUMBER", policy["number"])
    return value.replace("$OBSERVED", observed).replace("$NUMBER", policy["number"])


@dataclass(frozen=True)
class NormalizedNotation:
    text: str
    original: str
    starts: array
    original_starts: array

    def original_range(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("Invalid normalized source range")
        first = bisect_right(self.starts, start) - 1
        last = bisect_right(self.starts, end - 1) - 1
        return (
            self.original_starts[first] + start - self.starts[first],
            self.original_starts[last] + end - self.starts[last],
        )


def _delete(value: NormalizedNotation, spans: Iterable[tuple[int, int]]) -> NormalizedNotation:
    """Keep sparse contiguous source runs, without identity-per-character integers."""
    output = io.StringIO()
    starts, originals = array("Q"), array("Q")
    length = 0

    def add_run(position: int, original: int) -> None:
        if starts and originals[-1] + position - starts[-1] == original:
            return
        starts.append(position)
        originals.append(original)

    def keep(start: int, end: int) -> None:
        nonlocal length
        if start >= end:
            return
        output.write(value.text[start:end])
        index = bisect_right(value.starts, start) - 1
        add_run(length, value.original_starts[index] + start - value.starts[index])
        index += 1
        while index < len(value.starts) and value.starts[index] < end:
            add_run(length + value.starts[index] - start, value.original_starts[index])
            index += 1
        length += end - start

    previous, changed = 0, False
    for start, end in spans:
        keep(previous, start)
        previous, changed = end, True
    if not changed:
        return value
    keep(previous, len(value.text))
    return NormalizedNotation(output.getvalue(), value.original, starts, originals)


def normalize_notation(text: str) -> NormalizedNotation:
    policy = notation_policy()
    replacements = policy["character_replacements"]
    normalized = (
        text.translate(str.maketrans(replacements))
        if any(char in text for char in replacements)
        else text
    )
    normalized = re.sub(policy["chi_squared_pattern"], "2", normalized)
    # Configured replacements are single-character transcription equivalents.
    if len(normalized) != len(text):
        raise ValueError("Notation replacements must retain character ownership")
    value = NormalizedNotation(normalized, text, array("Q", [0]), array("Q", [0]))
    spans = (
        (match.start(), match.end()) for match in re.finditer(policy["thousands_comma"], normalized)
    )
    value = _delete(value, spans)
    spans = (
        (match.end("head"), match.end())
        for match in re.finditer(_pattern(policy["explicit_df_prefix"], policy), value.text)
    )
    return _delete(value, spans)


def library_ranges(normalized: NormalizedNotation) -> list[tuple[int, int]]:
    matches, starts, ends = extract_pattern(
        normalized.text, RGX_NHST, ignore_case=False, return_limits=True
    )
    ranges = []
    for text, start, end in zip(matches, starts, ends, strict=True):
        # Upstream strips Raw, while its offsets describe the unstripped match.
        start += len(text) - len(text.lstrip())
        end -= len(text) - len(text.rstrip())
        ranges.append((start, end))
    return ranges


def _number(token: str | None) -> tuple[float, int] | None:
    if token is None:
        return None
    try:
        decimal = Decimal(token)
        value = float(decimal)
        if not decimal.is_finite() or not math.isfinite(value) or (decimal != 0 and value == 0):
            return None
        precision = max(0, -decimal.as_tuple().exponent)
        if 10.0 ** (-precision) == 0:
            return None
        return value, precision
    except (ValueError, InvalidOperation, OverflowError):
        return None


def _same_value(expected: float | None, actual: object) -> bool:
    if actual is None or actual == "":
        return expected is None
    try:
        value = float(actual)
    except (TypeError, ValueError):
        return False
    if math.isnan(value):
        return expected is None
    return math.isfinite(value) and expected == value


def bind_library_row(
    row: dict,
    normalized: NormalizedNotation,
    ranges: deque[tuple[int, int]],
) -> dict | None:
    """Only exact, unmodified upstream matches can own a source statistic."""
    policy = notation_policy()
    raw = row.get("Raw")
    family = policy["families"].get(row.get("Statistic"))
    if not isinstance(raw, str) or family is None:
        return None
    p_match = re.search(_pattern(policy["p_token"], policy), raw)
    if p_match is None:
        return None
    prefix_pattern = _pattern(family + policy["test_value_suffix"], policy)
    prefix = re.fullmatch(prefix_pattern, raw[: p_match.start()])
    if prefix is None:
        return None
    p_number = _number(p_match["number"])
    test_number = _number(prefix["number"])
    if p_number is None or test_number is None:
        return None
    groups = prefix.groupdict()
    degrees = {key: _number(groups.get(key)) for key in ("df1", "df2")}
    if any(groups.get(key) is not None and value is None for key, value in degrees.items()):
        return None
    if any(value is not None and value[0] <= 0 for value in degrees.values()):
        return None
    if not 0 <= p_number[0] <= 1:
        return None
    if (
        not _same_value(test_number[0], row.get("Value"))
        or not _same_value(p_number[0], row.get("Reported_P_Value"))
        or prefix["comparison"] != row.get("Test_Comparison")
        or p_match["comparison"] != row.get("Reported_Comparison")
        or any(
            not _same_value(value[0] if value else None, row.get(key))
            for key, value in degrees.items()
        )
    ):
        return None
    while ranges:
        start, end = ranges.popleft()
        if normalized.text[start:end] != raw:
            continue
        if start and re.match(
            policy["statistic_prefix_word_character"], normalized.text[start - 1]
        ):
            continue
        if re.match(policy["numeric_continuation"], normalized.text[end:]):
            continue
        source_start, source_end = normalized.original_range(start, end)
        return {
            "raw_text": normalized.original[source_start:source_end],
            "source_start": source_start,
            "source_end": source_end,
            "test_value_precision": test_number[1],
            "p_value_precision": p_number[1],
        }
    return None


def text_notation_coverage(extraction: "ExtractionResult", stats: list["ReportedStat"]) -> dict:
    policy = notation_policy()
    source = analytical_source(extraction)
    blocks = [block for scope in source.scopes for block in scope if not block.is_furniture]
    patterns = [re.compile(_pattern(pattern, policy)) for pattern in policy["result_candidates"]]
    items = []
    bound_by_block = defaultdict(list)
    for stat in stats:
        if (
            stat.kind == "inferential"
            and stat.source_block_index is not None
            and stat.source_start is not None
            and stat.source_end is not None
        ):
            bound_by_block[stat.source_block_index].append((stat.source_start, stat.source_end))
    for block_index, block in enumerate(blocks):
        candidates = sorted(
            {match.span() for pattern in patterns for match in pattern.finditer(block.text)},
            key=lambda span: (span[0], -span[1]),
        )
        # Retain the full observed statement when two candidate grammars overlap.
        maximal, furthest = [], -1
        for start, end in candidates:
            if end > furthest:
                maximal.append((start, end))
                furthest = end
        supported = iter(sorted(bound_by_block[block_index]))
        next_supported = next(supported, None)
        supported_end = -1
        for start, end in maximal:
            while next_supported is not None and next_supported[0] <= start:
                supported_end = max(supported_end, next_supported[1])
                next_supported = next(supported, None)
            if end <= supported_end:
                continue
            items.append(
                {
                    "source_block_index": block_index,
                    "source_start": start,
                    "source_end": end,
                    "raw_text": block.text[start:end],
                    "page": block.page,
                    "paragraph": block.paragraph,
                    "anchor": f"p. {block.page}"
                    if block.page is not None
                    else f"\u00b6{block.paragraph}"
                    if block.paragraph is not None
                    else "document",
                    "reason": "unsupported_or_unbound_notation",
                }
            )
    return {
        "statistical_notation_unassessed": bool(items),
        "statistical_notation_coverage": {
            "version": policy["version"],
            "policy_sha256": policy["sha256"],
            "items": items,
        },
    }
