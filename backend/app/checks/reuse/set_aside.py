"""Shared wording between a passage pair, with honest shared text left out.

Two kinds of text share their wording with a Library thesis without being
reuse of it: a quotation marked and cited (BUG-632, `quotations.py`) and a
listed standard text such as a standard's definition or a scale legend
(BUG-637, `standard_texts.py`). Both are left out of the checked passage
before the shared-wording rule (`lexical.py`, BUG-620) is applied.

Leaving text out can only remove evidence: a pair that does not share
wording with everything compared is not confirmed by comparing less.
"""

from functools import lru_cache
from pathlib import Path

from app.checks.reuse.lexical import wording_evidence, words
from app.checks.reuse.quotations import quotations_left_out
from app.checks.reuse.standard_texts import (
    StandardSpan,
    load_standard_texts,
    standard_text_spans,
)
from app.config import Settings


def standard_text_lists(settings: Settings):
    """The public standard-text list plus the deployment list, if any."""
    return load_standard_texts(
        settings.reuse_public_standard_texts_file, settings.reuse_standard_texts_file
    )


@lru_cache(maxsize=4096)
def _standard_spans(text: str, public: Path, deployment: Path | None) -> tuple[StandardSpan, ...]:
    # The same checked passage is compared with several Library candidates.
    return tuple(standard_text_spans(text, load_standard_texts(public, deployment)))


def _shared_with(span_text: str, other: str, size: int) -> bool:
    """Whether any `size`-word run of `span_text` is also in `other`."""
    mine, theirs = words(span_text), words(other)
    runs = {tuple(theirs[i : i + size]) for i in range(len(theirs) - size + 1)}
    return any(tuple(mine[i : i + size]) in runs for i in range(len(mine) - size + 1))


def _set_aside_reason(own, other, settings, quoted, standard_out) -> str:
    def stops(left_out):
        return (
            bool(left_out)
            and not (wording_evidence(own, other, settings, a_left_out=left_out)["confirmed"])
        )

    if stops(standard_out):
        return "standard_text"
    if stops(quoted):
        return "cited_quotation"
    return "cited_quotation_and_standard_text"


def pair_wording(
    own: str, own_context: str, other: str, other_context: str, settings: Settings
) -> dict:
    """The wording evidence for a checked passage (`own`) and a Library
    passage (`other`), with the checked passage's cited quotations and
    standard texts left out.

    `confirmed` is the decision. When the pair shares wording only inside
    left-out text, `set_aside` says why: "standard_text" or "cited_quotation"
    when that kind alone accounts for it, "cited_quotation_and_standard_text"
    when only both together do. `standard_texts` names the standard texts
    the two passages share, so a report can say what was left out without
    quoting it."""
    quoted = quotations_left_out(own, own_context, other, other_context, settings)
    standard = _standard_spans(
        own, settings.reuse_public_standard_texts_file, settings.reuse_standard_texts_file
    )
    standard_out = [(s.start, s.end) for s in standard]
    size = settings.reuse_confirm_shingle_words
    names = sorted(
        {
            name
            for s in standard
            for name, start, end in s.parts
            if _shared_with(own[start:end], other, size)
        }
    )
    raw = wording_evidence(own, other, settings)
    if not quoted and not standard_out:
        return {**raw, "set_aside": None, "standard_texts": []}
    left = wording_evidence(own, other, settings, a_left_out=quoted + standard_out)
    set_aside = None
    if raw["confirmed"] and not left["confirmed"]:
        set_aside = _set_aside_reason(own, other, settings, quoted, standard_out)
    return {
        **left,
        "confirmed": left["confirmed"] and raw["confirmed"],
        "set_aside": set_aside,
        "standard_texts": names,
        "before_leaving_out": {
            "shingle_share": raw["shingle_share"],
            "longest_run": raw["longest_run"],
        },
    }
