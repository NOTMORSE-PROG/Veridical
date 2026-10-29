"""Section word-count rule (F3.2): "the abstract must be at most 250 words"
counted from the manuscript's own text instead of estimated by a model.

Dynamic by construction: the section comes from the criterion's own wording
through ingestion's heading vocabulary, the bound from its own numbers
(digits or words, `numerals.py`), and the accepted phrasings from
`claim_scope_grammar.json`. Any format naming any recognized section works
without a code change.

Precision first (charter): words are counted only between the located
section heading and the next recorded heading. The count stops at a keyword
line or at another recognized section's heading; any other heading-like line
inside the span (a repeated title, an unrecognized or numbered heading), a
missing next heading, unresolved reading order, or a span over the configured
page limit makes the result unverifiable instead of a count that could run
into the next section. A miss within the configured tolerance is escalated.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from statistics import median

from app.checks.rules import RuleContext, RuleOutcome, RuleSpec, register_rule
from app.checks.rules.bounds import Bound, extract_bound
from app.checks.rules.claim_scope import (
    WORD_COUNT_RULE_ID,
    complete_word_count_claim,
    load_claim_grammar,
)
from app.checks.rules.keywords import claim_text
from app.checks.rules.numerals import normalize_numerals
from app.ingest.schemas import SectionNode, TextBlock
from app.models.enums import ResultOutcome

_DATA_FILE = Path(__file__).parent / "data" / "word_count_rule.json"
# Zero-width space/joiners, word joiner and BOM: invisible in the PDF, never words.
_INVISIBLE = re.compile("[" + "".join(map(chr, (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))) + "]")
_WORD = re.compile(r"\S*[^\W_]\S*")
_SENTENCE_END = ".!?;,"


@lru_cache
def _end_of_prose(path: Path = _DATA_FILE) -> tuple[re.Pattern[str], ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(re.compile(p, re.IGNORECASE) for p in raw["end_of_prose_patterns"])


def _word_units() -> frozenset[str]:
    units = load_claim_grammar()["values"]["word_units"]
    return frozenset(re.sub(r"\\(.)", r"\1", unit) for unit in units.split("|"))


def _word_bound(criterion) -> Bound | None:
    """Read from the same wording the claim grammar accepted. Only a lower,
    upper or two-sided limit settles a word count: a bare "250 words" does
    not say which side the format means."""
    bound = extract_bound(normalize_numerals(claim_text(criterion)))
    if bound is None or bound.kind == "exact" or bound.unit not in _word_units():
        return None
    return bound


def _sections():
    """Imported on use: `sections.py` registers the most generic rule and
    must load after this module (`_load_default_rules` order)."""
    from app.checks.rules import sections

    return sections


def _matches(criterion) -> bool:
    target = _sections().identify_target_section(criterion)
    return complete_word_count_claim(criterion, target) and _word_bound(criterion) is not None


def _key(text: str) -> str:
    return " ".join(_INVISIBLE.sub("", text).casefold().strip(" :.\t").split())


def _other_section(text: str, own_title: str, ctx: RuleContext) -> bool:
    """A standalone line naming a different recognized section (or a
    chapter) ends the count even when the section tree did not record it."""
    patterns = _sections()._current_heading_patterns()
    key = _key(text)
    if not key or len(key) > ctx.heading_max_chars or key == _key(own_title):
        return False
    names = patterns.unnumbered_sections | patterns.toc_titles | patterns.reference_titles
    return key in names or patterns.chapter.fullmatch(key) is not None


def _heading_like(block: TextBlock, body_font: float, ctx: RuleContext) -> bool:
    """Short, unpunctuated and set apart (all capitals, numbered, bold or
    larger than the section's own text): the span may contain another
    section, so its end is not established."""
    patterns = _sections()._current_heading_patterns()
    text = _INVISIBLE.sub("", block.text).strip()
    if not text or len(text) > ctx.heading_max_chars or text[-1] in _SENTENCE_END:
        return False
    letters = [char for char in text if char.isalpha()]
    return (
        (len(letters) > 1 and all(char.isupper() for char in letters))
        or patterns.numbered.match(text) is not None
        or patterns.top_numbered.match(text) is not None
        or block.bold_ratio >= ctx.heading_bold_ratio_min
        or (body_font > 0 and block.max_font_size >= body_font + ctx.heading_size_delta)
    )


def _heading_lines(
    blocks: list[TextBlock], title: str, page: int, start: int, max_lines: int
) -> tuple[int, int] | None:
    """(first, last) index of the one line, or up to `max_lines` consecutive
    lines, on `page` that spell `title`; None when absent or not unique."""
    want = _key(title)
    found = []
    for index in range(start, len(blocks)):
        if blocks[index].page != page:
            continue
        joined = ""
        for width in range(max_lines):
            if index + width >= len(blocks) or blocks[index + width].page != page:
                break
            joined = f"{joined} {blocks[index + width].text}"
            if _key(joined) == want:
                found.append((index, index + width))
                break
    return found[0] if len(found) == 1 else None


def _successor(node: SectionNode, flat: list[SectionNode]) -> SectionNode | None:
    position = next(i for i, candidate in enumerate(flat) if candidate is node)
    return next((n for n in flat[position + 1 :] if n.level <= node.level), None)


def _span(node: SectionNode, ctx: RuleContext) -> tuple[list[TextBlock], str] | str:
    """The lines between the section heading and the next recorded heading,
    and that heading's label; or the reason no such span exists."""
    blocks = list(ctx.blocks)
    successor = _successor(node, list(_sections().walk_sections(ctx.section_tree)))
    if successor is None:
        return "No later heading was recorded, so where this section ends is not established."
    until = f'"{successor.title.strip()}"'
    if ctx.anchor_kind == "paragraph":
        if node.paragraph is None or successor.paragraph is None:
            return "The section's paragraph position was not recorded."
        lines = [
            b
            for b in blocks
            if b.paragraph is not None and node.paragraph < b.paragraph < successor.paragraph
        ]
        return lines, until
    if node.page is None or successor.page is None:
        return "The section's page was not recorded."
    max_lines = ctx.word_count_heading_max_lines
    heading = _heading_lines(blocks, node.title, node.page, 0, max_lines)
    if heading is None:
        return "The section heading line could not be located uniquely on its page."
    next_heading = _heading_lines(
        blocks, successor.title, successor.page, heading[1] + 1, max_lines
    )
    if next_heading is None:
        return "The next section's heading line could not be located."
    return blocks[heading[1] + 1 : next_heading[0]], until


def _prose(
    lines: list[TextBlock], until: str, *, node: SectionNode, ctx: RuleContext
) -> tuple[list[TextBlock], str, int] | str:
    """The section's own prose: up to a keyword line (left out with every
    later line) or another recognized section's heading. Any other
    heading-like line means the section's end is not established."""
    content = [b for b in lines if not b.is_furniture and _INVISIBLE.sub("", b.text).strip()]
    fonts = [b.max_font_size for b in content if b.max_font_size > 0]
    body_font = median(fonts) if fonts else 0.0
    for index, block in enumerate(content):
        text = _INVISIBLE.sub("", block.text).strip()
        if any(pattern.search(text) for pattern in _end_of_prose()):
            return content[:index], f'"{text}"', len(content) - index
        if _other_section(text, node.title, ctx):
            return content[:index], f'"{text}"', 0
        if _heading_like(block, body_font, ctx):
            return (
                f'A heading-like line ("{text}") appears inside "{node.title.strip()}", so '
                "where the section ends is not established."
            )
    return content, until, 0


def _count_words(lines: list[TextBlock]) -> int:
    text = ""
    for block in lines:
        for raw in block.text.splitlines():
            line = _INVISIBLE.sub("", raw).strip()
            # A word hyphenated across a line break counts once, whether the
            # break falls inside one extracted block or between two.
            joined = text.endswith("-") and line[:1].islower()
            text = text[:-1] + line if joined else f"{text} {line}"
    return len(_WORD.findall(text))


def _miss(bound: Bound, count: int) -> int:
    if bound.kind == "min":
        return max(0, int(bound.low) - count)
    if bound.kind == "max":
        return max(0, count - int(bound.low))
    return max(0, int(bound.low) - count, count - int(bound.high or bound.low))


def _unverifiable(anchor: str, reason: str, expected: dict, **detail) -> RuleOutcome:
    return RuleOutcome(
        outcome=ResultOutcome.unverifiable,
        anchor=anchor,
        detail={**detail, "reason": f"{reason} Count the section directly.", "expected": expected},
    )


def _run(criterion, ctx: RuleContext) -> RuleOutcome:
    sections = _sections()
    target = sections.identify_target_section(criterion)
    bound = _word_bound(criterion)
    if target is None or bound is None:
        return RuleOutcome(
            outcome=ResultOutcome.unverifiable,
            anchor="document",
            detail={"reason": "The word limit could not be read from this criterion's wording."},
        )
    expected = {"kind": bound.kind, "low": bound.low, "high": bound.high}
    if not ctx.source_identity_verified or not ctx.blocks:
        return _unverifiable(
            "document", "The manuscript text needed for a word count is not verified.", expected
        )
    node = sections.find_section_node(target, ctx.section_tree)
    if node is None:
        return RuleOutcome(
            outcome=ResultOutcome.escalated,
            anchor="not found in document structure",
            detail={
                "target": target,
                "expected": expected,
                "reason": (
                    f"No section matching '{target}' was found in the parsed section tree. "
                    "This may mean the section is genuinely missing, or that it exists under "
                    "wording VERIDICAL doesn't yet recognize -- check the manuscript directly "
                    "before deciding."
                ),
            },
        )
    anchor = sections._node_anchor(node)
    span = _span(node, ctx)
    if isinstance(span, str):
        return _unverifiable(anchor, span, expected)
    prose = _prose(*span, node=node, ctx=ctx)
    if isinstance(prose, str):
        return _unverifiable(anchor, prose, expected)
    counted, until, excluded = prose
    pages = {b.page for b in counted if b.page is not None}
    if pages & ctx.unreliable_pages or len(pages) > ctx.word_count_max_span_pages:
        return _unverifiable(
            anchor,
            f'The text of "{node.title.strip()}" could not be bounded reliably (its pages '
            "include unresolved reading order or it runs longer than expected).",
            expected,
        )
    count = _count_words(counted)
    detail = {
        "section_title": node.title.strip(),
        "counted_until": until,
        "actual_words": count,
        "excluded_lines": excluded,
        "expected": expected,
    }
    if count == 0:
        return _unverifiable(anchor, "No section text was found to count.", expected, **detail)
    miss = _miss(bound, count)
    if miss == 0:
        return RuleOutcome(outcome=ResultOutcome.passed, anchor=anchor, detail=detail)
    if miss <= ctx.word_count_tolerance_words:
        return RuleOutcome(
            outcome=ResultOutcome.unverifiable,
            anchor=anchor,
            detail={
                **detail,
                "reason": (
                    f"VERIDICAL counted {count} words, within {miss} of the stated limit. Word "
                    "counts differ between tools (hyphenated terms, numbers, keyword lines), so "
                    "confirm the count directly before deciding."
                ),
            },
        )
    return RuleOutcome(outcome=ResultOutcome.failed, anchor=anchor, detail=detail)


register_rule(
    RuleSpec(
        rule_id=WORD_COUNT_RULE_ID,
        description="Counts the words of a named section against a stated word limit.",
        matches=_matches,
        run=_run,
    )
)
