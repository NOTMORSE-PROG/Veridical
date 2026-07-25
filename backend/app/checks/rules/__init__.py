"""Structural rule registry (F3.1/F3.2).

The router (`app/checks/router.py`) matches a structural criterion against
this registry to decide whether a deterministic rule exists for it
(V-015); this ticket (V-016) populates it with real rule families and
gives each one an executor (`run`), not just a matcher.

Matching is data-driven, never hardcoded per rubric (ground rule 7): each
`RuleSpec` carries its own `matches` predicate, sourced from
`app/checks/rules/keywords.py` + `bounds.py` data files, the same
convention as `app/ingest/patterns.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.ingest.schemas import PageGeometry, SectionTree, TableBlock
from app.models.enums import ResultOutcome


class CriterionLike(Protocol):
    """Minimal shape a rule's `matches`/`run` predicate needs — satisfied
    by the `Criterion` ORM model, but kept structural so tests don't need
    a DB row to exercise routing."""

    text: str
    evidence: str | None


class CitationLike(Protocol):
    order_index: int
    raw_text: str
    parse_status: str


@dataclass(frozen=True)
class RuleContext:
    """Everything a structural rule needs to evaluate one manuscript —
    built once per check_run (`app.checks.rules.context.build_rule_context`)
    and handed to every rule, so no rule queries the DB or the raw store
    itself (keeps rules pure functions, per the ticket's own AC)."""

    manuscript_id: int
    anchor_kind: str  # "page" | "paragraph" — DOCX has no pages (F1 contract)
    page_count: int
    section_tree: SectionTree
    geometry: list[PageGeometry]
    tables: list[TableBlock]
    citations: list[CitationLike]
    # Config values a rule needs but shouldn't reach into Settings for
    # directly (keeps rules pure functions of their inputs, easy to test).
    margin_tolerance_pts: float
    citation_style_min_ratio: float
    table_caption_min_ratio: float


@dataclass(frozen=True)
class RuleOutcome:
    """A rule's verdict on one criterion. `anchor` is always a human-
    readable locator string (never blank) — screen 4h/4i's contract for
    "every fail carries an anchor the UI can jump to" (V-016 AC); passes
    carry one too, pointing at the evidence that satisfied the rule."""

    outcome: ResultOutcome
    anchor: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class RuleSpec:
    """One implemented structural rule family.

    `rule_id` is the stable identifier persisted onto routing decisions and
    the audit log; `matches` decides, from the criterion's own text/evidence
    (never from knowledge of a specific rubric), whether this rule applies;
    `run` actually evaluates it against a manuscript's `RuleContext`.
    """

    rule_id: str
    description: str
    matches: Callable[[CriterionLike], bool]
    run: Callable[[CriterionLike, RuleContext], RuleOutcome]


_REGISTRY: dict[str, RuleSpec] = {}


def register_rule(spec: RuleSpec) -> RuleSpec:
    """Idempotent by `rule_id` — re-importing a rule module (tests, reload)
    must not raise or duplicate registry entries."""
    _REGISTRY[spec.rule_id] = spec
    return spec


def registered_rules() -> list[RuleSpec]:
    """Stable order (registration order) so routing is deterministic."""
    return list(_REGISTRY.values())


def find_matching_rule(criterion: CriterionLike) -> RuleSpec | None:
    for spec in _REGISTRY.values():
        if spec.matches(criterion):
            return spec
    return None


def get_rule(rule_id: str) -> RuleSpec | None:
    return _REGISTRY.get(rule_id)


def _clear_registry_for_tests() -> None:
    """Test-only escape hatch — production code never calls this; the
    registry is meant to only grow (rule modules register at import time)."""
    _REGISTRY.clear()


def _snapshot_registry() -> dict[str, RuleSpec]:
    """Test-only: capture the current registry so a test can clear it,
    exercise honest pre-registration behavior, then hand the real default
    rules back for whichever test runs next."""
    return dict(_REGISTRY)


def _restore_registry(snapshot: dict[str, RuleSpec]) -> None:
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


def _load_default_rules() -> None:
    """Imports every packaged rule module for its import-time
    `register_rule(...)` side effect. Idempotent (register_rule replaces by
    id), so calling this more than once (tests) is harmless. Uses
    `importlib` (not a plain `import` block) because the load ORDER is
    semantically meaningful here — `find_matching_rule` returns the FIRST
    match, and `required_section_present` (sections.py) is the most
    generic matcher, so it must load last (module docstring in
    sections.py) — an isort-sorted import block would silently break
    that ordering."""
    import importlib

    for module_name in ("geometry", "pages", "references", "tables", "sections"):
        importlib.import_module(f"app.checks.rules.{module_name}")


_load_default_rules()
