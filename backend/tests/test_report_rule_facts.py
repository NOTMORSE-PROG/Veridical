"""F3.2/F8: report observations retain the saved rule's actual narrow basis."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.checks.rules import get_rule
from app.checks.rules.sections import REQUIRED_SECTION_RULE_ID
from app.ingest.schemas import PageGeometry, SectionNode, TableBlock
from app.models.enums import CheckKind, CriterionType, ResultOutcome
from app.report.rule_facts import project_rule_facts, rule_facts_presentation
from app.report.service import _to_criterion_result
from tests.test_checks_structural_rules import FakeCitation, FakeCriterion, _ctx, _tree


def test_real_heading_lookup_observation_survives_report_projection():
    spec = get_rule(REQUIRED_SECTION_RULE_ID)
    criterion = FakeCriterion(text="The manuscript must include an Abstract")
    produced = spec.run(
        criterion, _ctx(section_tree=_tree(SectionNode(title="ABSTRACT", level=1, page=2)))
    )
    result = SimpleNamespace(
        id=1,
        judgment_revision=0,
        kind=CheckKind.structural,
        outcome=produced.outcome,
        score=100,
        detail={**produced.detail, "rule_id": spec.rule_id, "anchor": produced.anchor},
    )
    rubric_row = SimpleNamespace(id=2, type=CriterionType.structural, text=criterion.text, weight=1)
    projected = _to_criterion_result(result, rubric_row, importance="med")
    assert projected.model_dump().get("rule_facts") == {
        "rule_id": REQUIRED_SECTION_RULE_ID,
        "target": "abstract",
        "matched_title": "ABSTRACT",
    }
    assert projected.anchor == "page 2"
    assert projected.reasoning is None
    assert projected.score == 100 and projected.outcome == "passed"
    assert projected.rule_scope_unconfirmed is False


@pytest.mark.parametrize(
    "mutation", ["missing", "wrong_input", "wrong_version", "wrong_rule", "malformed"]
)
def test_legacy_scope_qualification_uses_saved_provenance_without_regrading(mutation):
    from app.checks.rules.presence import presence_scope_record
    from app.report.judgments import _original

    criterion = SimpleNamespace(
        id=2,
        type=CriterionType.structural,
        text="The manuscript must include an Abstract",
        evidence=None,
        weight=1,
    )
    detail = {
        "rule_id": "required_section_present",
        "target": "abstract",
        "matched_title": "ABSTRACT",
    }
    if mutation != "missing":
        marker = presence_scope_record(criterion)
        if mutation == "wrong_input":
            marker["input_digest"] = "private-digest-does-not-bind"
        elif mutation == "wrong_version":
            marker["version"] = "unknown"
        elif mutation == "wrong_rule":
            marker["rule_id"] = "page_limit"
        detail["section_presence_scope"] = [] if mutation == "malformed" else marker
    result = SimpleNamespace(
        id=1,
        judgment_revision=0,
        kind=CheckKind.structural,
        outcome=ResultOutcome.passed,
        score=100,
        detail=detail,
    )
    before = deepcopy(detail)
    projected = _to_criterion_result(result, criterion, importance="med")
    original = _original(result, criterion)
    assert projected.rule_scope_unconfirmed and original.rule_scope_unconfirmed
    assert projected.outcome == "passed" and projected.score == 100
    assert detail == before and result.judgment_revision == 0
    assert "private-digest" not in projected.model_dump_json()


def test_scope_marker_is_not_reconstructed_from_todays_matcher_on_read(monkeypatch):
    from app.checks.rules import presence

    criterion = SimpleNamespace(
        id=2,
        type=CriterionType.structural,
        text="The manuscript must include an Abstract",
        evidence=None,
        weight=1,
    )
    detail = {
        "rule_id": "required_section_present",
        "target": "abstract",
        "matched_title": "ABSTRACT",
        "section_presence_scope": presence.presence_scope_record(criterion),
    }
    result = SimpleNamespace(
        id=1,
        judgment_revision=0,
        kind=CheckKind.structural,
        outcome=ResultOutcome.passed,
        score=100,
        detail=detail,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Report reads must not execute the current grammar")

    monkeypatch.setattr(presence, "is_presence_claim", forbidden)
    monkeypatch.setattr(presence, "load_presence_grammar", forbidden)
    assert not _to_criterion_result(result, criterion, importance="med").rule_scope_unconfirmed
    criterion.evidence = "At most 250 words."
    assert _to_criterion_result(result, criterion, importance="med").rule_scope_unconfirmed


def _project(detail, outcome="passed", **kwargs):
    return project_rule_facts(
        detail,
        criterion_type=kwargs.get("criterion_type", "structural"),
        kind=kwargs.get("kind", "structural"),
        outcome=outcome,
    )


@pytest.mark.parametrize(
    ("rule", "text", "ctx", "expected"),
    [
        (
            "required_section_present",
            "The manuscript must include an Abstract",
            _ctx(section_tree=_tree(SectionNode(title="ABSTRACT", level=1))),
            {"target": "abstract", "matched_title": "ABSTRACT"},
        ),
        (
            "required_section_present",
            "The manuscript must include a Glossary",
            _ctx(),
            {"target": "glossary", "matched_title": None},
        ),
        (
            "section_order",
            "Chapters must appear in order",
            _ctx(
                section_tree=_tree(
                    SectionNode(title="Same title", level=1, numbering="1"),
                    SectionNode(title="Same title", level=1, numbering="2"),
                )
            ),
            {"checked_sections": ["Same title", "Same title"]},
        ),
        (
            "page_limit",
            "must not exceed 100 pages",
            _ctx(page_count=10),
            {"actual_count": 10, "expected": {"kind": "max", "low": 100.0, "high": None}},
        ),
        (
            "reference_count_min_max",
            "exactly 0 references",
            _ctx(),
            {"actual_count": 0, "expected": {"kind": "exact", "low": 0.0, "high": None}},
        ),
        (
            "table_formatting_presence",
            "Tables must carry captions",
            _ctx(tables=[TableBlock(rows=[["A"]], caption="Table A")]),
            {"total_tables": 1, "captioned": 1},
        ),
        (
            "margins_spacing",
            "1 inch margins",
            _ctx(geometry=[PageGeometry(page=1, width=612, height=792, margins=(72, 72, 72, 72))]),
            {"checked_pages_or_sections": 1, "side": None, "actual_pts": None},
        ),
        (
            "margins_spacing",
            "1 inch margins",
            _ctx(geometry=[PageGeometry(page=1, width=612, height=792, margins=(50, 72, 72, 72))]),
            {"checked_pages_or_sections": None, "side": "left", "actual_pts": 50.0},
        ),
        (
            "citation_style_sniff",
            "References must use APA style",
            _ctx(citations=[FakeCitation(order_index=0, raw_text="Smith (2020). Study.")]),
            {"threshold_relation": "met"},
        ),
        (
            "citation_style_sniff",
            "References must use APA style",
            _ctx(citations=[FakeCitation(order_index=0, raw_text="Smith, 2020. Study.")]),
            {"threshold_relation": "below"},
        ),
    ],
)
def test_genuine_producer_observations_all_seven_families(rule, text, ctx, expected):
    produced = get_rule(rule).run(FakeCriterion(text=text), ctx)
    saved = {**produced.detail, "rule_id": rule, "secret_provider": "PRIVATE_SENTINEL"}
    before = deepcopy(saved)
    facts = _project(saved, produced.outcome.value)
    assert facts is not None
    assert facts.model_dump() == {"rule_id": rule, **expected}
    paragraphs, headings = rule_facts_presentation(facts)
    assert paragraphs
    assert "PRIVATE_SENTINEL" not in str((facts, paragraphs, headings))
    assert saved == before


PAGE = {
    "rule_id": "page_limit",
    "actual_pages": 10,
    "expected": {"kind": "max", "low": 20.0, "high": None},
}


@pytest.mark.parametrize(
    "value", [True, False, "10", -1, 0, 1.5, float("inf"), float("nan"), None, [], {}]
)
def test_rejects_unreliable_page_counts(value):
    assert _project({**PAGE, "actual_pages": value}) is None


@pytest.mark.parametrize(
    "bound",
    [
        {"kind": "range", "low": 12.0},
        {"kind": "range", "low": 12.0, "high": 2.0},
        {"kind": "max", "low": True},
        {"kind": "max", "low": "20"},
        {"kind": "max", "low": float("nan")},
        {"kind": "future", "low": 20.0},
        {"kind": "max", "low": 20.0, "high": 30.0},
    ],
)
def test_rejects_incomplete_or_malformed_saved_bounds(bound):
    assert _project({**PAGE, "expected": bound}) is None


@pytest.mark.parametrize(
    "outcome",
    ["unverifiable", "not_applicable", "api_down", "quota_exhausted", "escalated", "unknown"],
)
def test_completed_count_is_not_inferred_for_unassessed_states(outcome):
    assert _project(PAGE, outcome) is None


@pytest.mark.parametrize("change", [{"basis": "llm"}, {"basis": "structural-alignment"}])
def test_actual_execution_basis_is_required(change):
    assert _project({**PAGE, **change}) is None


def test_manual_grade_never_substitutes_for_original_automated_outcome():
    saved = {
        "rule_id": "required_section_present",
        "target": "glossary",
        "resolution": {"type": "mark_pass", "reason": "Private reason"},
        "original_result": {"outcome": "escalated"},
    }
    facts = _project(saved, "passed")
    assert facts is not None and facts.matched_title is None
    assert "searched for" in rule_facts_presentation(facts)[0][0]
    assert "Private reason" not in str(facts.model_dump())
    legacy = {**PAGE, "resolution": saved["resolution"]}
    assert _project(legacy, "failed") is not None
    assert _project({**legacy, "original_result": {"outcome": "failed"}}, "passed") is None
    assert (
        _project({**PAGE, "resolution": saved["resolution"], "original_result": {"outcome": {}}})
        is not None
    )


@pytest.mark.parametrize(
    "detail",
    [
        {"rule_id": "required_section_present", "target": " ", "matched_title": "ABSTRACT"},
        {"rule_id": "required_section_present", "target": "abstract", "matched_title": None},
        {"rule_id": "section_order", "checked_sections": ["one", " "]},
        {"rule_id": "table_formatting_presence", "total_tables": 1, "captioned": 2},
        {"rule_id": "table_formatting_presence", "total_tables": 0, "captioned": 0},
        {
            "rule_id": "margins_spacing",
            "checked_pages_or_sections": 1,
            "side": "left",
            "actual_pts": 50.0,
        },
        {"rule_id": "margins_spacing", "side": "left", "actual_pts": float("inf")},
        {"rule_id": "citation_style_sniff", "matched_ratio": True, "threshold": 0.5},
        {"rule_id": "citation_style_sniff", "matched_ratio": 1.2, "threshold": 0.5},
        {"rule_id": "citation_style_sniff", "matched_ratio": 0.8, "threshold": "0.5"},
        {"rule_id": "unknown", "actual_pages": 10},
    ],
)
def test_malformed_and_unknown_shapes_stay_absent(detail):
    assert _project(detail) is None


def test_criterion_and_execution_types_must_both_be_structural():
    assert _project(PAGE, criterion_type="semantic") is None
    assert _project(PAGE, kind="semantic") is None


def test_fractional_saved_bounds_are_not_rounded_or_replaced_with_current_settings():
    facts = _project({**PAGE, "expected": {"kind": "range", "low": 9.5, "high": 10.5}})
    assert "between 9.5 and 10.5, inclusive" in rule_facts_presentation(facts)[0][1]
