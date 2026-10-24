"""F3/F8: recorded source limits survive private assessment presentation."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.ingest.analytical_source import AnalyticalSource
from app.models.enums import CheckKind, ResultOutcome
from app.report.judgments import _original
from app.report.schemas import PublicCriterionResultOut
from app.report.service import _to_criterion_result
from app.report.source_coverage import project_source_coverage


def recorded(detail):
    return SimpleNamespace(
        id=11,
        judgment_revision=1,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.passed,
        score=100,
        detail=detail,
    )


def test_positive_recorded_coverage_survives_both_private_projections():
    coverage = {
        "native_math_coverage_version": "v1",
        "native_math_inventory_state": "complete",
        "native_math_inventory_scope": "main_document_body",
        "n_native_equations_unassessed": 2,
        "n_native_equations_unlocated": 0,
        "native_math_excluded_paragraphs": [7],
        "native_math_coverage_items": [
            {"paragraph": 7, "kind": "table_cell", "n_occurrences": 1},
            {"paragraph": 7, "kind": "table_cell", "n_occurrences": 1},
        ],
    }
    detail = {
        "basis": "llm",
        "reasoning": "Retained explanation",
        "evidence": [],
        "source_coverage": coverage,
    }
    original = deepcopy(detail)
    result = recorded(detail)
    criterion = SimpleNamespace(id=3, text="Actual requirement", type=CheckKind.semantic, weight=1)
    context = _original(result).model_dump()
    canonical = _to_criterion_result(result, criterion, importance="med").model_dump()
    assert context.get("source_coverage") == coverage
    assert canonical.get("source_coverage") == coverage
    assert canonical["reasoning"] == context["reasoning"] == "Retained explanation"
    assert detail == original


@pytest.mark.parametrize("state", ["not_recorded", "unavailable", "not_applicable"])
def test_accepts_actual_non_complete_producer_shapes(state):
    coverage = AnalyticalSource(state, None, (), (), ()).detail()
    assert project_source_coverage(coverage).model_dump() == coverage


def complete(count=0, unlocated=0, excluded=None, locations=None):
    return {
        "native_math_coverage_version": "v1",
        "native_math_inventory_state": "complete",
        "native_math_inventory_scope": "main_document_body",
        "n_native_equations_unassessed": count,
        "n_native_equations_unlocated": unlocated,
        "native_math_excluded_paragraphs": excluded or [],
        "native_math_coverage_items": locations or [],
    }


def test_complete_zero_and_positive_unlocated_keep_measured_facts():
    assert project_source_coverage(complete()).model_dump() == complete()
    data = complete(1, 1, locations=[{"paragraph": None, "kind": "paragraph", "n_occurrences": 1}])
    assert project_source_coverage(data).model_dump() == data


@pytest.mark.parametrize("value", [None, {}, [], True, {"native_math_coverage_version": "v2"}])
def test_missing_or_malformed_never_becomes_measured_zero(value):
    projected = project_source_coverage(value)
    assert projected.native_math_inventory_state == (
        "not_recorded" if value is None else "unavailable"
    )
    assert projected.n_native_equations_unassessed is None
    assert projected.n_native_equations_unlocated is None
    assert projected.native_math_inventory_scope is None


@pytest.mark.parametrize(
    "change",
    [
        {"n_native_equations_unassessed": 2},
        {"n_native_equations_unlocated": 1},
        {"native_math_excluded_paragraphs": []},
        {"native_math_excluded_paragraphs": [7, 7]},
        {"n_native_equations_unassessed": True},
        {"n_native_equations_unassessed": "1"},
        {
            "native_math_coverage_items": [
                {"paragraph": True, "kind": "paragraph", "n_occurrences": 1}
            ]
        },
        {
            "native_math_coverage_items": [
                {"paragraph": 7, "kind": "paragraph", "n_occurrences": "1"}
            ]
        },
    ],
)
def test_conflicting_or_coerced_recorded_facts_are_unavailable(change):
    value = complete(1, 0, [7], [{"paragraph": 7, "kind": "paragraph", "n_occurrences": 1}])
    value.update(change)
    projected = project_source_coverage(value)
    assert projected.native_math_inventory_state == "unavailable"
    assert projected.n_native_equations_unassessed is None


def test_projection_allowlist_drops_formula_xml_paths_and_unrestricted_metadata():
    value = complete(
        1,
        0,
        [7],
        [
            {
                "paragraph": 7,
                "kind": "paragraph",
                "n_occurrences": 1,
                "formula": "PRIVATE FORMULA",
                "xml_path": [7, 2],
            }
        ],
    )
    value.update(formula="PRIVATE FORMULA", xml="PRIVATE XML", path="PRIVATE PACKAGE PATH")
    projected = project_source_coverage(value)
    assert projected.native_math_inventory_state == "complete"
    assert "PRIVATE" not in projected.model_dump_json()
    assert "xml_path" not in projected.model_dump_json()
    assert "source_coverage" not in PublicCriterionResultOut.model_fields
