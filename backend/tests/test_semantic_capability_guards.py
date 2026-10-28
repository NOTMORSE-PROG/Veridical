"""F3: manuscript quotation alone cannot certify external or aggregate properties."""

import json
from types import SimpleNamespace

import pytest

from app.checks.consistency import run_semantic_checks_with_consistency
from app.checks.semantic import run_semantic_checks
from app.checks.support import _capability_failure, apply_support, check_support
from app.config import Settings
from app.models.enums import ResultOutcome
from tests.test_semantic_support_gate import QUOTE, Client, Session, criterion, extraction


def local_item():
    item = criterion()
    item.id = 73
    item.text = "One written reference entry states author, year, title and publisher"
    item.evidence = "Inspect the written entry"
    item.levels[0]["descriptor"] = "A requested written field is missing"
    item.levels[1]["descriptor"] = "The requested written fields are present"
    return item


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
@pytest.mark.parametrize("obligation", ["criterion", "evidence_request", "level_descriptor"])
@pytest.mark.parametrize(
    ("requirement", "capability"),
    [
        ("The publications are peer-reviewed", "external_verification_required"),
        ("The devices are certified", "external_verification_required"),
        ("The professional legitimacy of sources is established", "external_verification_required"),
        ("The reliability\nof sources is established", "external_verification_required"),
        ("Most devices meet the stated conditions", "collection_assessment_required"),
        ("Every device is documented", "collection_assessment_required"),
        ("Each requirement is met", "collection_assessment_required"),
        ("All the devices are documented", "collection_assessment_required"),
        ("Every one of the items is documented", "collection_assessment_required"),
        ("A few references are questionable", "collection_assessment_required"),
        ("Professional tone is maintained throughout", "collection_assessment_required"),
    ],
)
async def test_support_model_cannot_supply_missing_assessment_capability(
    runner, obligation, requirement, capability
):
    item = local_item()
    if obligation == "criterion":
        item.text = requirement
    elif obligation == "evidence_request":
        item.evidence = requirement
    else:
        item.levels[1]["descriptor"] = requirement
    client = Client("supported")
    result = (await runner(Session(), 6, [item], extraction(), client, Settings()))[0]
    assert result.outcome == ResultOutcome.escalated and result.score is None
    assert "verdict" not in result.detail and "level" not in result.detail
    assert result.detail["proposed_verdict"] == "Exceptional"
    assert result.detail["evidence"][0]["quote"] == QUOTE
    support = result.detail["support_check"]
    assert support["scope_guard"] == "independent_assessment_required"
    assert support["missing_capabilities"] == [capability]
    assert support["guarded_obligations"] == [{"key": obligation, "capabilities": [capability]}]
    assert sum(kind == "semantic_support" for kind, _, _ in client.calls) == 1


@pytest.mark.parametrize("term", ["few-shot learning", "majority-vote algorithm"])
def test_local_written_algorithm_name_is_not_a_collection_assessment(term):
    item = local_item()
    item.text = "The text names the " + term
    batch = SimpleNamespace(label="Written term", scope_patterns_file=None)
    assert _capability_failure(batch, item, {"verdict": "Exceptional"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
async def test_local_written_metadata_remains_eligible_without_external_certification(runner):
    result = (
        await runner(Session(), 6, [local_item()], extraction(), Client("supported"), Settings())
    )[0]
    assert result.outcome == ResultOutcome.passed
    assert result.detail["support_check"]["status"] == "supported"
    assert result.detail["evidence"][0]["quote"] == QUOTE


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["Limited", "Exceptional"])
async def test_absent_policy_override_and_repeated_quote_cannot_bypass_both_capabilities(verdict):
    item = local_item()
    item.evidence = "Evaluate the reliability of sources"
    for level in item.levels:
        level["descriptor"] = (
            "Most references are professionally legitimate; a few are questionable"
        )
    batch = SimpleNamespace(
        label="Unrelated collection", context_text=QUOTE, scope_patterns_file=None
    )
    detail = {"verdict": verdict, "evidence": [{"quote": QUOTE}] * 20, "score": 1}
    support = (await check_support(batch, [(item, detail)], Client("supported"), check_run_id=6))[
        73
    ]
    outcome, guarded = apply_support(ResultOutcome.passed, detail, support)
    assert outcome == ResultOutcome.escalated
    assert "score" not in guarded and "verdict" not in guarded
    assert guarded["proposed_verdict"] == verdict
    assert len(guarded["evidence"]) == 20
    assert support["missing_capabilities"] == [
        "collection_assessment_required",
        "external_verification_required",
    ]
    assert {row["key"] for row in support["guarded_obligations"]} == {
        "evidence_request",
        "level_descriptor",
    }


@pytest.mark.asyncio
async def test_secondary_request_contains_only_owned_quotes_and_not_surrounding_context():
    batch = SimpleNamespace(
        label="Whole manuscript",
        context_text="UNSUBMITTED_CONTEXT_SENTINEL: surrounding table of contents",
        source_coverage={"reason": "UNSUBMITTED_COVERAGE_SENTINEL"},
        scope_patterns_file=None,
    )
    detail = {"verdict": "Exceptional", "evidence": [{"quote": QUOTE}]}
    client = Client("supported")
    await check_support(batch, [(local_item(), detail)], client, check_run_id=8)
    assert len(client.calls) == 1
    kind, prompt, context = client.calls[0]
    assert kind == "semantic_support" and context["prompt_version"] == "v3"
    assert "UNSUBMITTED_CONTEXT_SENTINEL" not in prompt
    assert "UNSUBMITTED_COVERAGE_SENTINEL" not in prompt
    task = json.loads(prompt.split("--- TASK JSON ---\n", 1)[1])
    assert "source_text" not in task and "source_coverage" not in task
    assert task["items"][0]["candidate_quotes"] == [QUOTE]
    assert task["source_obligation_policy"] == "source_obligations_v5_owned_quotes"
