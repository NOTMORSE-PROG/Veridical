"""Spelled-out numbers in a criterion's own wording become digits before a
bound is parsed: "at least five (5) references" and "two hundred fifty
words" read the same as "at least 5 references" and "250 words". The
vocabulary is data (number_words.json); only the arithmetic is code.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "number_words.json"


@lru_cache
def _vocabulary(path: Path = _DEFAULT_FILE) -> tuple[dict[str, int], re.Pattern[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    values: dict[str, int] = {}
    for key in ("units", "tens", "scales"):
        values.update({word.lower(): int(value) for word, value in raw[key].items()})
    conjunctions = [word.lower() for word in raw.get("conjunctions", [])]
    word = "|".join(sorted(map(re.escape, values), key=len, reverse=True))
    joiner = r"(?:\s+|-)" + (
        rf"(?:(?:{'|'.join(map(re.escape, conjunctions))})\s+)?" if conjunctions else ""
    )
    phrase = re.compile(
        rf"\b(?P<words>(?:{word})(?:{joiner}(?:{word}))*)\b(?P<digits>\s*\(\s*\d+\s*\))?",
        re.IGNORECASE,
    )
    return values, phrase


def _segment_values(words: str, values: dict[str, int]) -> list[int] | None:
    """A conjunction joins one number only after a scale word ("two hundred
    and fifty"); anywhere else it separates two numbers ("between one
    hundred fifty and two hundred fifty"). None when the phrase does not
    state one reading: a scale word repeated within a number ("one hundred
    and two hundred"), or a bare number joined to a scaled one ("between two
    and three hundred", which usually means 200 to 300)."""
    segments: list[tuple[int, bool]] = []
    total = current = 0
    previous: int | None = None
    scales: set[int] = set()
    for token in re.findall(r"[a-z]+", words.lower()):
        value = values.get(token)
        if value is None:
            if previous is not None and previous < 100:
                segments.append((total + current, bool(scales)))
                total = current = 0
                previous = None
                scales = set()
            continue
        if value >= 100:
            if value in scales:
                return None
            scales.add(value)
        if value == 100:
            current = (current or 1) * value
        elif value >= 1000:
            total += (current or 1) * value
            current = 0
        else:
            current += value
        previous = value
    segments.append((total + current, bool(scales)))
    for (_, scaled), (_, next_scaled) in zip(segments, segments[1:], strict=False):
        if not scaled and next_scaled:
            return None
    return [number for number, _ in segments]


def normalize_numerals(text: str) -> str:
    values, phrase = _vocabulary()

    def replace(match: re.Match[str]) -> str:
        numbers = _segment_values(match.group("words"), values)
        if numbers is None:
            return match.group(0)
        digits = match.group("digits")
        if digits is not None:
            stated = int(re.sub(r"\D", "", digits))
            if len(numbers) != 1 or stated != numbers[0]:
                return match.group(0)
        return " and ".join(map(str, numbers))

    return phrase.sub(replace, text)
