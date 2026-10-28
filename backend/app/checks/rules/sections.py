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
from app.checks.rules.keywords import contains_any, current_keywords, full_text, load_keywords
from app.checks.rules.presence import is_presence_claim, presence_scope_record
from app.ingest.patterns import HeadingPatterns, load_patterns
from app.ingest.schemas import SectionNode, SectionTree
from app.models.enums import ResultOutcome

_KEYWORDS = load_keywords()
_HEADING_PATTERNS = load_patterns()

REQUIRED_SECTION_RULE_ID = "required_section_present"
SECTION_ORDER_RULE_ID = "section_order"


def _current_heading_patterns() -> HeadingPatterns:
    from app.config import get_settings

    configured = get_settings().ingest_patterns_file
    return load_patterns(configured) if configured is not None else _HEADING_PATTERNS


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
    patterns = _current_heading_patterns()
    text = full_text(criterion)
    lowered = text.lower()
    for synonym in patterns.unnumbered_sections:
        if synonym in lowered:
            return synonym
    chapter_match = patterns.chapter.search(text)
    if chapter_match:
        return f"chapter {chapter_match.group('num')}".lower()
    return None


def _title_candidates(target: str) -> frozenset[str]:
    """BUG-048 follow-up: `target` and a document's real heading can both
    be genuine synonyms for the SAME section (a criterion asking about
    "the bibliography" against a document that titled its section
    "REFERENCES") without either string containing the other — the
    plain substring check below would falsely report the section
    missing. `reference_titles` already exists as this exact synonym
    family (F1.5, `app/ingest/references.py`'s own reference-list
    detection uses it the same way); reused here rather than duplicated,
    so teaching the app a new reference-section name in one place
    teaches it everywhere (same principle `identify_target_section`'s
    own docstring states for `unnumbered_sections`)."""
    patterns = _current_heading_patterns()
    if target in patterns.reference_titles:
        return patterns.reference_titles
    return frozenset({target})


def find_section_node(target: str, tree: SectionTree) -> SectionNode | None:
    """First node whose title matches `target` (substring either
    direction, casefolded) — the actual lookup the required-section rule
    runs, and what V-017 uses to locate a criterion's text span."""
    candidates = _title_candidates(target)
    for node in walk_sections(tree):
        title = node.title.casefold()
        if any(candidate in title or title in candidate for candidate in candidates):
            return node
    return None


def _matches_required_section(criterion) -> bool:
    return is_presence_claim(criterion, identify_target_section(criterion))


def _node_anchor(node: SectionNode) -> str:
    if node.page is not None:
        return f"page {node.page}"
    if node.paragraph is not None:
        return f"paragraph {node.paragraph}"
    return "document"


def _run_required_section(criterion, ctx: RuleContext) -> RuleOutcome:
    if not ctx.section_order_witnesses_verified:
        return RuleOutcome(
            outcome=ResultOutcome.unverifiable,
            anchor="document",
            detail={
                "reason": (
                    "The retained Word source does not establish a verified heading inventory. "
                    "Review the original manuscript; section presence has not been rated."
                ),
                "source_coverage": ctx.native_math_source_detail,
            },
        )
    target = identify_target_section(criterion)
    if not is_presence_claim(criterion, target):
        return RuleOutcome(
            outcome=ResultOutcome.unverifiable,
            anchor="document",
            detail={
                "reason": (
                    "The complete requirement could not be checked by heading presence. "
                    "Review the full requirement directly."
                )
            },
        )
    node = find_section_node(target, ctx.section_tree)
    if node is not None:
        return RuleOutcome(
            outcome=ResultOutcome.passed,
            anchor=_node_anchor(node),
            detail={
                "target": target,
                "matched_title": node.title,
                "section_presence_scope": presence_scope_record(criterion),
            },
        )
    # BUG-221 (charter rule 1): a title-match miss here is NOT proof the
    # section is genuinely absent -- it is equally consistent with the
    # section being present under phrasing `_title_candidates`'s synonym
    # set doesn't yet cover (exactly the false-negative class BUG-048 fixed
    # for the references section specifically; nothing here rules it out
    # for an arbitrary "chapter N" or other target). Reporting this as an
    # ordinary `failed` -- "Does not meet" -- states a claim the rule did
    # not actually verify: that the section is missing, not that the rule
    # couldn't find it. `escalated` puts it in front of the instructor
    # instead of deciding it silently, the same distinction `semantic.py`'s
    # identical "missing" branch now makes.
    return RuleOutcome(
        outcome=ResultOutcome.escalated,
        anchor="not found in document structure",
        detail={
            "target": target,
            "section_presence_scope": presence_scope_record(criterion),
            "reason": (
                f"No section matching '{target}' was found in the parsed section tree. "
                "This may mean the section is genuinely missing, or that it exists under "
                "wording VERIDICAL doesn't yet recognize -- check the manuscript directly "
                "before deciding."
            ),
        },
    )


def _matches_section_order(criterion) -> bool:
    text = full_text(criterion)
    kw = current_keywords()
    return contains_any(text, kw.order_nouns) and contains_any(text, kw.section_reference_nouns)


def _numeric_prefix(numbering: str | None) -> float | None:
    if not numbering:
        return None
    head = numbering.split(".")[0]
    return float(head) if head.isdigit() else None


def _run_section_order(criterion, ctx: RuleContext) -> RuleOutcome:
    source_limit = RuleOutcome(
        outcome=ResultOutcome.unverifiable,
        anchor="document",
        detail={
            "reason": (
                "The available heading tree may omit content affected by native Word equations. "
                "It cannot establish the order of all top-level sections. Review the original "
                "manuscript; no rating was assigned for section order."
            ),
            "source_coverage": ctx.native_math_source_detail,
        },
    )
    if not ctx.section_order_witnesses_verified:
        return source_limit
    top_level = [n for n in ctx.section_tree.nodes if n.level == 1]
    numbered = [(n, _numeric_prefix(n.numbering)) for n in top_level]
    numbered = [(n, v) for n, v in numbered if v is not None]
    if len(numbered) < 2:
        if not ctx.section_order_source_complete:
            return source_limit
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
    if not ctx.section_order_source_complete:
        return source_limit
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
