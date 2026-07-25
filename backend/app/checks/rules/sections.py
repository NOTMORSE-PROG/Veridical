"""Section-presence and section-order rules (F3.2). Reuses the same
heading-synonym vocabulary that drove heading DETECTION during ingestion
(V-004, `app/ingest/data/heading_patterns.json`) to decide which section a
criterion is talking about — one vocabulary, not a duplicated one, so
teaching VERIDICAL a new section name in one place teaches it everywhere.

Registered LAST among the packaged rules (see `app/checks/rules/__init__.py`
import order): "has an X" is the most generic phrasing a structural
criterion can take, so more specific rules (references, pages, margins,
tables, citation style) get first refusal.
"""

from collections.abc import Iterator

from app.checks.rules import RuleContext, RuleOutcome, RuleSpec, register_rule
from app.checks.rules.keywords import contains_any, full_text, load_keywords
from app.ingest.patterns import load_patterns
from app.ingest.schemas import SectionNode, SectionTree
from app.models.enums import ResultOutcome

_KEYWORDS = load_keywords()
_HEADING_PATTERNS = load_patterns()

REQUIRED_SECTION_RULE_ID = "required_section_present"
SECTION_ORDER_RULE_ID = "section_order"


def walk_sections(tree: SectionTree) -> Iterator[SectionNode]:
    """Depth-first, document-reading order — reused by V-017's semantic
    context builder to slice section text spans, not just by this rule."""

    def _walk_nodes(nodes: list[SectionNode]) -> Iterator[SectionNode]:
        for node in nodes:
            yield node
            yield from _walk_nodes(node.children)

    yield from _walk_nodes(tree.nodes)


def identify_target_section(criterion) -> str | None:
    """Which section (by ingestion's own synonym vocabulary, or a
    "chapter N" reference) a criterion's wording is talking about, if any.
    Shared by `required_section_present` (this rule) and V-017's semantic
    context builder — one identification mechanism, not two."""
    text = full_text(criterion)
    lowered = text.lower()
    for synonym in _HEADING_PATTERNS.unnumbered_sections:
        if synonym in lowered:
            return synonym
    chapter_match = _HEADING_PATTERNS.chapter.search(text)
    if chapter_match:
        return f"chapter {chapter_match.group('num')}".lower()
    return None


def find_section_node(target: str, tree: SectionTree) -> SectionNode | None:
    """First node whose title matches `target` (substring either
    direction, casefolded) — the actual lookup the required-section rule
    runs, and what V-017 uses to locate a criterion's text span."""
    for node in walk_sections(tree):
        title = node.title.casefold()
        if target in title or title in target:
            return node
    return None


def _matches_required_section(criterion) -> bool:
    return identify_target_section(criterion) is not None


def _node_anchor(node: SectionNode) -> str:
    if node.page is not None:
        return f"page {node.page}"
    if node.paragraph is not None:
        return f"paragraph {node.paragraph}"
    return "document"


def _run_required_section(criterion, ctx: RuleContext) -> RuleOutcome:
    target = identify_target_section(criterion)
    if target is None:
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="document",
            detail={"reason": "Could not identify which section this criterion refers to."},
        )
    node = find_section_node(target, ctx.section_tree)
    if node is not None:
        return RuleOutcome(
            outcome=ResultOutcome.passed,
            anchor=_node_anchor(node),
            detail={"target": target, "matched_title": node.title},
        )
    return RuleOutcome(
        outcome=ResultOutcome.failed,
        anchor="not found in document structure",
        detail={
            "target": target,
            "reason": f"No section matching '{target}' was found in the parsed section tree.",
        },
    )


def _matches_section_order(criterion) -> bool:
    text = full_text(criterion)
    return contains_any(text, _KEYWORDS.order_nouns) and contains_any(
        text, _KEYWORDS.section_reference_nouns
    )


def _numeric_prefix(numbering: str | None) -> float | None:
    if not numbering:
        return None
    head = numbering.split(".")[0]
    return float(head) if head.isdigit() else None


def _run_section_order(criterion, ctx: RuleContext) -> RuleOutcome:
    top_level = [n for n in ctx.section_tree.nodes if n.level == 1]
    numbered = [(n, _numeric_prefix(n.numbering)) for n in top_level]
    numbered = [(n, v) for n, v in numbered if v is not None]
    if len(numbered) < 2:
        return RuleOutcome(
            outcome=ResultOutcome.not_applicable,
            anchor="document",
            detail={"reason": "Not enough numbered top-level sections to check ordering."},
        )
    prev_node, prev_value = numbered[0]
    for node, value in numbered[1:]:
        if value <= prev_value:
            return RuleOutcome(
                outcome=ResultOutcome.failed,
                anchor=_node_anchor(node),
                detail={
                    "reason": (
                        f"'{node.title}' (numbered {node.numbering}) appears out of order "
                        f"after '{prev_node.title}' (numbered {prev_node.numbering})."
                    ),
                },
            )
        prev_node, prev_value = node, value
    return RuleOutcome(
        outcome=ResultOutcome.passed,
        anchor="document",
        detail={"checked_sections": [n.title for n, _ in numbered]},
    )


register_rule(
    RuleSpec(
        rule_id=SECTION_ORDER_RULE_ID,
        description="Checks numbered top-level sections appear in increasing order.",
        matches=_matches_section_order,
        run=_run_section_order,
    )
)
register_rule(
    RuleSpec(
        rule_id=REQUIRED_SECTION_RULE_ID,
        description="Checks a named section exists in the parsed section tree.",
        matches=_matches_required_section,
        run=_run_required_section,
    )
)
