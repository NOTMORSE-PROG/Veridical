"""Quotations marked and cited in the text (BUG-632).

A student who quotes a Library thesis inside quotation marks, with a citation
beside the quotation, is using the source the honest way. The shared-wording
rule (`lexical.py`) cannot tell that apart from copying, because a quotation
shares its wording with its source by definition. The spans found here are
left out of that comparison on both sides, the way indented block quotes are
left out of passage matching (`app/ingest/blockquotes.py`). Copied prose
beside the quotation is still compared.

What counts as a quotation mark and as a citation is data
(`data/cited_quotations.json`). A quotation needs a citation next to it:
most quotation marks in a thesis are button labels, section titles or code,
and those are compared like any other text.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_WORD = re.compile(r"[^\W_]+")
# Around a symmetric mark: what may come before an opening one and after a
# closing one, besides the start or end of the text.
_OPEN_BEFORE = re.compile(r"[\s(\[{\u2014\u2013/-]")
_CLOSE_AFTER = re.compile(r"[\s.,;:!?)\]}\u2014\u2013-]")
# How far to look for a citation, in characters, before counting words.
_LOOKAROUND_CHARS = 800


@dataclass(frozen=True)
class QuotationRules:
    pairs: tuple[tuple[str, str], ...]
    min_words: int
    max_words: int
    after_words: int
    before_words: int
    after: tuple[re.Pattern, ...]
    before: tuple[re.Pattern, ...]


@lru_cache(maxsize=4)
def load_quotation_rules(path: Path) -> QuotationRules:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return QuotationRules(
        pairs=tuple((open_mark, close_mark) for open_mark, close_mark in raw["quote_pairs"]),
        min_words=raw["min_quote_words"],
        max_words=raw["max_quote_words"],
        after_words=raw["after_words"],
        before_words=raw["before_words"],
        after=tuple(re.compile(p) for p in raw["citations_after"].values()),
        before=tuple(re.compile(p) for p in raw["citations_before"].values()),
    )


def _word_count(text: str) -> int:
    return len(_WORD.findall(text))


def _opens(text: str, i: int) -> bool:
    before_ok = i == 0 or bool(_OPEN_BEFORE.fullmatch(text[i - 1]))
    return before_ok and i + 1 < len(text) and not text[i + 1].isspace()


def _closes(text: str, i: int) -> bool:
    after_ok = i + 1 == len(text) or bool(_CLOSE_AFTER.fullmatch(text[i + 1]))
    return after_ok and i > 0 and not text[i - 1].isspace()


def _closing_mark(
    text: str, start: int, close_mark: str, symmetric: bool, max_words: int
) -> int | None:
    i = start + 1
    while True:
        i = text.find(close_mark, i)
        if i == -1 or _word_count(text[start + 1 : i]) > max_words:
            return None
        if not symmetric or _closes(text, i):
            return i
        i += 1


def _cited(text: str, start: int, end: int, rules: QuotationRules) -> bool:
    """A citation starts within `after_words` words after the quotation, or
    ends within `before_words` words before it."""
    after_limit = min(len(text), end + _LOOKAROUND_CHARS)
    for pattern in rules.after:
        found = pattern.search(text, end, after_limit)
        if found and _word_count(text[end : found.start()]) <= rules.after_words:
            return True
    before_start = max(0, start - _LOOKAROUND_CHARS)
    for pattern in rules.before:
        last = None
        for found in pattern.finditer(text, before_start, start):
            last = found
        if last and _word_count(text[last.end() : start]) <= rules.before_words:
            return True
    return False


def cited_quotation_spans(text: str, rules: QuotationRules) -> list[tuple[int, int]]:
    """(start, end) of each quotation in `text` that has a citation beside it,
    quotation marks included."""
    spans: list[tuple[int, int]] = []
    for open_mark, close_mark in rules.pairs:
        symmetric = open_mark == close_mark
        i = 0
        while (start := text.find(open_mark, i)) != -1:
            i = start + 1
            if symmetric and not _opens(text, start):
                continue
            close = _closing_mark(text, start, close_mark, symmetric, rules.max_words)
            if close is None:
                continue
            words = _word_count(text[start + 1 : close])
            if words >= rules.min_words and _cited(text, start, close + 1, rules):
                spans.append((start, close + 1))
                i = close + 1
    return sorted(spans)


def cited_quotations_in_passage(
    passage: str, context: str, rules: QuotationRules
) -> list[tuple[int, int]]:
    """Cited quotations that fall in `passage`, in its own offsets. They are
    found in `context` (the passage with the words around it), so a quotation
    or citation that runs past the passage's edge is still seen."""
    offset = context.find(passage) if context else -1
    if offset == -1:
        return cited_quotation_spans(passage, rules)
    end = offset + len(passage)
    return [
        (max(start, offset) - offset, min(stop, end) - offset)
        for start, stop in cited_quotation_spans(context, rules)
        if start < end and stop > offset
    ]
