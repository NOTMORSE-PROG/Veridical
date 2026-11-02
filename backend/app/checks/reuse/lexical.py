"""Shared-wording confirmation for F7 similarity matches (BUG-620).

The embedding is a static one: it averages word vectors and ignores order,
so two passages of the same genre (two requirements lists, two signature
blocks) can score above the similarity bar while sharing no wording at all.
A match is raised only when the two passages also share wording: word
shingle containment, or one long run of identical words. Both thresholds are
settings. Tokens are lower-cased words with punctuation and digits removed,
the measure the evaluation plan uses to confirm reuse findings (Broder 1997).
"""

import re
from collections.abc import Sequence

from app.config import Settings

_WORD = re.compile(r"[a-z]+(?:[-'][a-z]+)*")


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _tokens(text: str, left_out: Sequence[tuple[int, int]] = ()) -> list:
    """The words of `text`, with each left-out span replaced by a gap: a
    fresh object equal to nothing, so no shingle or run crosses it."""
    if not left_out:
        return words(text)
    tokens: list = []
    pos = 0
    for start, end in sorted(left_out):
        if start > pos:
            tokens += words(text[pos:start])
        if end > pos:
            tokens.append(object())
        pos = max(pos, end)
    return tokens + words(text[pos:])


def _shingles(tokens: list, size: int) -> set[tuple]:
    windows = (tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1))
    return {w for w in windows if all(isinstance(t, str) for t in w)}


def _overlap(x: list, y: list, size: int) -> float:
    first, second = _shingles(x, size), _shingles(y, size)
    if not first or not second:
        return 0.0
    return len(first & second) / min(len(first), len(second))


def _longest_run(x: list, y: list) -> int:
    best, previous = 0, [0] * (len(y) + 1)
    for i in range(1, len(x) + 1):
        current = [0] * (len(y) + 1)
        for j in range(1, len(y) + 1):
            if x[i - 1] == y[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best = current[j]
        previous = current
    return best


def shingle_overlap(a: str, b: str, size: int) -> float:
    """Shared word shingles over the smaller passage's shingles, so a short
    copy inside a longer passage still counts in full."""
    return _overlap(words(a), words(b), size)


def longest_common_run(a: str, b: str) -> int:
    """Length of the longest run of identical consecutive words."""
    return _longest_run(words(a), words(b))


def wording_evidence(
    a: str,
    b: str,
    settings: Settings,
    *,
    a_left_out: Sequence[tuple[int, int]] = (),
    b_left_out: Sequence[tuple[int, int]] = (),
) -> dict:
    """The two measures, and whether either confirms shared wording. The
    share test only counts when the shorter passage is long enough for half
    its shingles to mean something. Text in the left-out spans (character
    offsets into `a` and `b`) is not compared."""
    x, y = _tokens(a, a_left_out), _tokens(b, b_left_out)
    share = _overlap(x, y, settings.reuse_confirm_shingle_words)
    run = _longest_run(x, y)
    shorter = min(sum(isinstance(t, str) for t in x), sum(isinstance(t, str) for t in y))
    long_enough = shorter >= settings.reuse_confirm_share_min_words
    confirmed = (long_enough and share >= settings.reuse_confirm_shingle_share) or (
        run >= settings.reuse_confirm_run_words
    )
    return {"shingle_share": round(share, 3), "longest_run": run, "confirmed": confirmed}


def shares_wording(a: str, b: str, settings: Settings) -> bool:
    """Whether two passages share wording, not just a topic."""
    return wording_evidence(a, b, settings)["confirmed"]
