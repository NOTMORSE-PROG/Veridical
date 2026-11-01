"""Quotations marked and cited in the text (BUG-632).

A student who quotes a Library thesis inside quotation marks, with a citation
beside the quotation, is using the source the honest way. The shared-wording
rule (`lexical.py`) cannot tell that apart from copying, because a quotation
shares its wording with its source by definition. So the student's cited
quotations are left out of that comparison, the way indented block quotes
are left out of passage matching (`app/ingest/blockquotes.py`). Copied prose
beside the quotation is still compared.

A quotation in quotation marks whose citation the rules do not recognise
is also left out when the Library thesis quotes the same words with a
citation: two theses quoting one source are not reuse of each other. Words
the student copied without quotation marks are always compared, even when
the Library thesis quoted them from elsewhere. Only the checked thesis's
text is ever left out.

Leaving text out can only remove evidence. A pair that does not share
wording with everything compared is not confirmed by comparing less.

What counts as a quotation mark and as a citation is data
(`data/cited_quotations.json`). A quotation needs a citation next to it:
most quotation marks in a thesis are button labels, section titles or code,
and those are compared like any other text.
"""

import json
import re
from bisect import bisect_left
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.checks.reuse.lexical import wording_evidence
from app.config import Settings

# The word `lexical.words` counts, matched on the original text so that
# offsets stay valid.
_WORD = re.compile(r"[a-z]+(?:[-'][a-z]+)*", re.IGNORECASE)


@dataclass(frozen=True)
class QuotationRules:
    pairs: tuple[tuple[str, str], ...]
    min_words: int
    max_words: int
    after_words: int
    before_words: int
    search_chars: int
    opening_follows: re.Pattern
    closing_precedes: re.Pattern
    after: tuple[re.Pattern, ...]
    before: tuple[re.Pattern, ...]


@dataclass(frozen=True)
class Quotation:
    start: int
    end: int
    cited: bool


@lru_cache(maxsize=4)
def load_quotation_rules(path: Path) -> QuotationRules:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return QuotationRules(
        pairs=tuple((open_mark, close_mark) for open_mark, close_mark in raw["quote_pairs"]),
        min_words=raw["min_quote_words"],
        max_words=raw["max_quote_words"],
        after_words=raw["after_words"],
        before_words=raw["before_words"],
        search_chars=raw["citation_search_chars"],
        opening_follows=re.compile(raw["symmetric_opening_mark_follows"]),
        closing_precedes=re.compile(raw["symmetric_closing_mark_precedes"]),
        after=tuple(re.compile(p) for p in raw["citations_after"].values()),
        before=tuple(re.compile(p) for p in raw["citations_before"].values()),
    )


class _Scan:
    """One text, with its word starts so word counts between two offsets
    are a lookup rather than a rescan."""

    def __init__(self, text: str, rules: QuotationRules):
        self.text = text
        self.rules = rules
        self.word_starts = [m.start() for m in _WORD.finditer(text)]

    def words(self, start: int, end: int) -> int:
        return bisect_left(self.word_starts, end) - bisect_left(self.word_starts, start)

    def opens(self, i: int, symmetric: bool) -> bool:
        if not symmetric:
            return True
        text = self.text
        before_ok = i == 0 or bool(self.rules.opening_follows.fullmatch(text[i - 1]))
        return before_ok and i + 1 < len(text) and not text[i + 1].isspace()

    def closes(self, i: int, symmetric: bool) -> bool:
        if not symmetric:
            return True
        text = self.text
        after_ok = i + 1 == len(text) or bool(self.rules.closing_precedes.fullmatch(text[i + 1]))
        return after_ok and i > 0 and not text[i - 1].isspace()

    def opening_marks(self) -> dict[int, tuple[str, bool]]:
        """Each valid opening mark's offset, with its pair's closing mark and
        whether the pair is symmetric."""
        found: dict[int, tuple[str, bool]] = {}
        for open_mark, close_mark in self.rules.pairs:
            symmetric = open_mark == close_mark
            i = self.text.find(open_mark)
            while i != -1:
                if self.opens(i, symmetric):
                    found.setdefault(i, (close_mark, symmetric))
                i = self.text.find(open_mark, i + 1)
        return found

    def closed_before(self, i: int, close_mark: str, symmetric: bool, limit: int) -> bool:
        j = self.text.find(close_mark, i + 1, limit)
        while j != -1:
            if self.closes(j, symmetric):
                return True
            j = self.text.find(close_mark, j + 1, limit)
        return False

    def cited(self, start: int, end: int) -> bool:
        """A citation starts within `after_words` words after the quotation,
        or ends within `before_words` words before it."""
        text, rules = self.text, self.rules
        after_limit = min(len(text), end + rules.search_chars)
        for pattern in rules.after:
            found = pattern.search(text, end, after_limit)
            if found and self.words(end, found.start()) <= rules.after_words:
                return True
        before_start = max(0, start - rules.search_chars)
        for pattern in rules.before:
            last = None
            for found in pattern.finditer(text, before_start, start):
                last = found
            if last and self.words(last.end(), start) <= rules.before_words:
                return True
        return False


def quotations(text: str, rules: QuotationRules) -> list[Quotation]:
    """Every quotation in `text`, cited or not, quotation marks included.

    A span that holds an opening mark it does not also close is not a
    quotation: a stray or unclosed mark would otherwise pair with a later
    quotation's closing mark and swallow the text between them. A quoted
    term inside a quotation is closed inside it, so it does not count."""
    scan = _Scan(text, rules)
    marks = scan.opening_marks()
    openings = sorted(marks)
    found: list[Quotation] = []
    for open_mark, close_mark in rules.pairs:
        symmetric = open_mark == close_mark
        start = text.find(open_mark)
        while start != -1:
            next_from = start + 1
            close = (
                _closing_mark(scan, start, close_mark, symmetric)
                if scan.opens(start, symmetric)
                else None
            )
            if (
                close is not None
                and all(
                    scan.closed_before(i, *marks[i], close)
                    for i in openings[
                        bisect_left(openings, start + 1) : bisect_left(openings, close)
                    ]
                )
                and scan.words(start + 1, close) >= rules.min_words
            ):
                found.append(Quotation(start, close + 1, scan.cited(start, close + 1)))
                next_from = close + 1
            start = text.find(open_mark, next_from)
    return sorted(found, key=lambda q: q.start)


def _closing_mark(scan: _Scan, start: int, close_mark: str, symmetric: bool) -> int | None:
    i = scan.text.find(close_mark, start + 1)
    while i != -1 and scan.words(start + 1, i) <= scan.rules.max_words:
        if scan.closes(i, symmetric):
            return i
        i = scan.text.find(close_mark, i + 1)
    return None


def cited_quotation_spans(text: str, rules: QuotationRules) -> list[tuple[int, int]]:
    """(start, end) of each quotation in `text` with a citation beside it."""
    return [(q.start, q.end) for q in quotations(text, rules) if q.cited]


def quotations_in_passage(passage: str, context: str, rules: QuotationRules) -> list[Quotation]:
    """Quotations that fall in `passage`, in its own offsets. They are found
    in `context` (the passage with the words around it), so a quotation or
    citation that runs past the passage's edge is still seen."""
    offset = context.find(passage) if context else -1
    if offset == -1:
        return quotations(passage, rules)
    end = offset + len(passage)
    return [
        Quotation(max(q.start, offset) - offset, min(q.end, end) - offset, q.cited)
        for q in quotations(context, rules)
        if q.start < end and q.end > offset
    ]


def pair_wording(
    own: str, own_context: str, other: str, other_context: str, settings: Settings
) -> dict:
    """Shared wording between the checked passage (`own`) and a Library
    passage (`other`), with the checked passage's quotations left out as the
    module docstring says. `cited_quotation_only` is set when the pair shares wording only
    inside those quotations."""
    rules = load_quotation_rules(settings.reuse_cited_quotation_rules_file)
    own_quotes = quotations_in_passage(own, own_context, rules)
    other_quotes = quotations_in_passage(other, other_context, rules)
    cited_there = [other[q.start : q.end] for q in other_quotes if q.cited]
    own_out = [
        (q.start, q.end)
        for q in own_quotes
        if q.cited
        or any(
            wording_evidence(own[q.start : q.end], theirs, settings)["confirmed"]
            for theirs in cited_there
        )
    ]
    raw = wording_evidence(own, other, settings)
    if not own_out:
        return {**raw, "cited_quotation_only": False}
    left = wording_evidence(own, other, settings, a_left_out=own_out)
    return {
        **left,
        "confirmed": left["confirmed"] and raw["confirmed"],
        "cited_quotation_only": raw["confirmed"] and not left["confirmed"],
        "with_quotations": {
            "shingle_share": raw["shingle_share"],
            "longest_run": raw["longest_run"],
        },
    }
