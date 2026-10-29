"""F3.1/F3.2: a heading observation cannot settle extra requirement clauses."""

import pytest

from app.checks.router import route_criterion
from app.checks.rules import get_rule
from app.ingest.schemas import SectionNode
from app.models.enums import CheckKind, ResultOutcome
from tests.test_checks_structural_rules import FakeCriterion, _ctx, _tree


@pytest.mark.parametrize(
    "text",
    [
        # BUG-498: the plain word limit is now settled completely by
        # `section_word_count`; an added qualifier keeps it compound.
        "Manuscript contains an abstract of at most 250 words in the past tense",
        "The Abstract must clearly explain the contribution",
        "The manuscript must not include an Abstract",
        "Include an Abstract only if requested",
        "The manuscript must include an Abstract and a Glossary",
        "The Abstract must include at least 3 references and contain at most 250 words",
        "The Abstract must not use APA style",
        "Abstract tables must have captions and explain results",
        "The Introduction must contain at least 3 citations and at most 250 words",
        "The Introduction must not use APA style",
    ],
)
def test_extra_clauses_cannot_fall_through_to_a_weaker_registered_rule(text):
    criterion = FakeCriterion(text=text)
    route = route_criterion(criterion, criterion_id=1, raw_type="structural")
    assert route.kind == CheckKind.semantic
    assert route.rule_id is None and route.degraded and not route.unroutable
    assert route.note


def test_evidence_instruction_is_part_of_the_complete_requirement():
    criterion = FakeCriterion(
        text="The manuscript must include an Abstract", evidence="At most 250 words."
    )
    route = route_criterion(criterion, criterion_id=1, raw_type="structural")
    assert route.kind == CheckKind.semantic and route.rule_id is None
    assert criterion.evidence == "At most 250 words."


@pytest.mark.parametrize(
    "text",
    [
        "The manuscript must include an Abstract",
        "Chapter 1 must be present",
        "The manuscript must include a Glossary",
    ],
)
def test_supported_single_presence_claim_keeps_its_narrow_rule(text):
    route = route_criterion(FakeCriterion(text=text), criterion_id=1, raw_type="structural")
    assert route.kind == CheckKind.structural and route.rule_id == "required_section_present"


def test_presence_only_heading_miss_still_requires_instructor_judgment():
    criterion = FakeCriterion(text="The manuscript must include a Glossary")
    result = get_rule("required_section_present").run(
        criterion, _ctx(section_tree=_tree(SectionNode(title="ABSTRACT", level=1)))
    )
    assert result.outcome == ResultOutcome.escalated


def test_declared_semantic_compound_keeps_semantic_routing():
    route = route_criterion(
        FakeCriterion(text="Manuscript contains an abstract of at most 250 words"),
        criterion_id=1,
        raw_type="semantic",
    )
    assert route.kind == CheckKind.semantic and not route.degraded


@pytest.mark.parametrize(
    "text",
    [
        "at least 15 references",
        "The bibliography must include at least 5 major references",
        "Chapters must appear in order",
        "1 inch margins",
        "Tables must be captioned",
        "APA year-in-parentheses pattern in references meets the configured threshold",
    ],
)
def test_supported_other_families_are_not_disabled_by_the_named_section_guard(text):
    route = route_criterion(FakeCriterion(text=text), criterion_id=1, raw_type="structural")
    assert route.kind == CheckKind.structural and route.rule_id != "required_section_present"


def test_direct_presence_execution_cannot_bypass_complete_input_recognition():
    criterion = FakeCriterion(text="Manuscript contains an abstract of at most 250 words")
    result = get_rule("required_section_present").run(
        criterion, _ctx(section_tree=_tree(SectionNode(title="ABSTRACT", level=1)))
    )
    assert result.outcome == ResultOutcome.unverifiable
    assert "section_presence_scope" not in result.detail


def test_prospective_presence_scope_binds_exact_text_and_evidence():
    from app.checks.rules.presence import presence_input_digest

    criterion = FakeCriterion(text="The manuscript must include an Abstract")
    result = get_rule("required_section_present").run(
        criterion, _ctx(section_tree=_tree(SectionNode(title="ABSTRACT", level=1)))
    )
    assert result.detail["section_presence_scope"] == {
        "version": "v1",
        "rule_id": "required_section_present",
        "input_digest": presence_input_digest(criterion),
    }
    assert presence_input_digest(criterion) != presence_input_digest(
        FakeCriterion(text=criterion.text, evidence="At most 250 words.")
    )


@pytest.mark.parametrize(
    "mutation",
    ["empty_patterns", "missing_subjects", "bad_pattern", "unknown_placeholder", "missing_target"],
)
def test_invalid_presence_grammar_does_not_restore_legacy_broad_matching(tmp_path, mutation):
    import json
    from pathlib import Path

    from app.checks.rules.presence import load_presence_grammar

    source = Path(__file__).parents[1] / "app/checks/rules/data/presence_rule_grammar.json"
    raw = json.loads(source.read_text("utf-8"))
    if mutation == "empty_patterns":
        raw["patterns"] = []
    elif mutation == "missing_subjects":
        raw.pop("subjects")
    elif mutation == "bad_pattern":
        raw["patterns"] = ["("]
    elif mutation == "unknown_placeholder":
        raw["patterns"] = ["{{missing}}"]
    else:
        raw["patterns"] = ["The manuscript must include an Abstract"]
    path = tmp_path / "presence.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises((ValueError, __import__("re").error)):
        load_presence_grammar(path)
