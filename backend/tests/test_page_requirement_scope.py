"""F3.1/F3.2: whole-document page bounds bind the complete criterion wording."""

import pytest

from app.checks.router import route_criterion
from app.checks.rules import get_rule
from app.models.enums import CheckKind, ResultOutcome
from tests.test_checks_structural_rules import FakeCriterion, _ctx


@pytest.mark.parametrize(
    "text",
    [
        "The PDF manuscript contains at least one page",
        "The PDF manuscript contains at least 1 page",
        "The document contains exactly twenty-three pages",
        "The manuscript must not exceed 100 pages",
    ],
)
def test_clear_whole_document_bound_uses_the_page_rule(text):
    route = route_criterion(FakeCriterion(text=text), criterion_id=1, raw_type="structural")
    assert route.kind == CheckKind.structural and route.rule_id == "page_limit"


@pytest.mark.parametrize(
    "text",
    [
        "Include at least 3 citations on page one",
        "The introduction must contain at least 3 pages",
        "Use at least 3 references and at most 80 pages",
        "The manuscript must include a two-page abstract",
        "The manuscript must include a 2page abstract",
        "The paper contains at least 1.5 pages",
        "The paper contains at least three point five pages",
        "The paper contains at least 10 pages excluding references",
        "The paper contains at least 10 pages and clearly explains its purpose",
    ],
)
def test_unsupported_page_scope_cannot_be_reclaimed_by_other_rule(text):
    route = route_criterion(FakeCriterion(text=text), criterion_id=1, raw_type="structural")
    assert route.kind == CheckKind.semantic and route.rule_id is None
    assert route.degraded and route.note


@pytest.mark.parametrize("anchor_kind,page_count", [("paragraph", 0), ("page", 0)])
def test_existing_unavailable_page_contract_is_not_changed_by_recognition(anchor_kind, page_count):
    result = get_rule("page_limit").run(
        FakeCriterion(text="The document contains at most 100 pages"),
        _ctx(anchor_kind=anchor_kind, page_count=page_count),
    )
    assert result.outcome == ResultOutcome.not_applicable


def test_direct_page_execution_refuses_a_local_or_compound_quantity():
    result = get_rule("page_limit").run(
        FakeCriterion(text="The introduction must contain at least 3 pages"), _ctx(page_count=20)
    )
    assert result.outcome == ResultOutcome.unverifiable


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_signals",
        "empty_units",
        "missing_groups",
        "bool_number",
        "negative_number",
        "unknown_kind",
        "empty_comparisons",
        "empty_patterns",
        "unknown_signal",
    ],
)
def test_invalid_page_capability_data_cannot_classify_the_constraint_unrelated(tmp_path, mutation):
    import json
    from pathlib import Path

    from app.checks.rules.page_bounds import load_page_grammar

    source = Path(__file__).parents[1] / "app/checks/rules/data/page_rule_grammar.json"
    raw = json.loads(source.read_text("utf-8"))
    if mutation == "empty_signals":
        raw["unit_signals"] = []
    elif mutation == "empty_units":
        raw["units"] = []
    elif mutation == "missing_groups":
        raw["pattern"] = "pages"
    elif mutation == "bool_number":
        raw["numbers"]["one"] = True
    elif mutation == "negative_number":
        raw["numbers"]["one"] = -1
    elif mutation == "unknown_kind":
        raw["comparisons"] = {"future": "at least"}
    elif mutation == "empty_comparisons":
        raw["comparisons"] = {}
    elif mutation == "empty_patterns":
        raw["pattern"] = ""
    else:
        raw["unit_signals"] = ["{{missing_signal}}"]
    path = tmp_path / "pages.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_page_grammar(path)
