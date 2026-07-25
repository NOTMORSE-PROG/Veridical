"""Structural rule registry (F3.1/F3.2).

The router (`app/checks/router.py`) matches a structural criterion against
this registry to decide whether a deterministic rule exists for it. The
registry starts EMPTY here on purpose — V-015 (this ticket) only builds the
matching mechanism; V-016 populates it by importing its rule modules (each
one calls `register_rule` at import time) and wiring that import into the
pipeline package's `__init__`. A structural criterion that matches nothing
degrades to semantic grading with an honest note, never a dead end (charter
rule 1) — this is what makes the registry safe to grow incrementally instead
of needing every rule day one.

Matching is data-driven, never hardcoded per rubric (ground rule 7): each
`RuleSpec` carries its own `matches` predicate, and rule modules are expected
to source their keyword/pattern lists from config or data files, the same
convention as `app/ingest/patterns.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class CriterionLike(Protocol):
    """Minimal shape a rule's `matches` predicate needs — satisfied by the
    `Criterion` ORM model, but kept structural so tests don't need a DB
    row to exercise routing."""

    text: str
    evidence: str | None


@dataclass(frozen=True)
class RuleSpec:
    """One implemented structural rule family.

    `rule_id` is the stable identifier persisted onto routing decisions and
    the audit log; `matches` decides, from the criterion's own text/evidence
    (never from knowledge of a specific rubric), whether this rule applies.
    """

    rule_id: str
    description: str
    matches: Callable[[CriterionLike], bool]


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


def _clear_registry_for_tests() -> None:
    """Test-only escape hatch — production code never calls this; the
    registry is meant to only grow (rule modules register at import time)."""
    _REGISTRY.clear()
