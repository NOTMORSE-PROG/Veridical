"""F4 separates reported attainment from incompatible factual assertions."""

import pytest

from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import Statement
from app.checks.agreement.pair import (
    Candidate,
    PairingError,
    PairVerdict,
    check_contradiction,
    different_reported_phases,
    parse_verdict_batch,
    run_agreement_pairing,
)
from app.config import Settings


def statement(kind, text):
    return Statement(kind=kind, text=text, anchor="Â¶3", page=None, paragraph=3, cue="intent_list")


class Reply:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    async def complete(self, *args, **kwargs):
        self.calls += 1
        return {"verdicts": [self.verdict]}


@pytest.mark.parametrize(
    "reason",
    ["objective_reported_not_done", "stated_target_missed", "incompatible_descriptive_claims"],
)
def test_honestly_unmet_goal_cannot_supply_a_factual_conflict(reason):
    goal = "To build a login module using school credentials."
    result = "The login module was not implemented due to time constraints."
    verdict = PairVerdict(
        index=0,
        verdict="contradictory",
        reasoning="Not implemented.",
        reason_category=reason,
        objective_quote="build a login module",
        finding_quote="The login module was not implemented",
    )
    problem, quotes = check_contradiction(
        Candidate(statement("intent", goal), statement("outcome", result), 0.9),
        verdict,
        load_cues(),
        min_quote_chars=12,
    )
    assert problem in {
        "reason_not_named",
        "objective_is_not_descriptive_assertion",
        "objective_assertion_scope_unassessed",
    }
    assert quotes is None


@pytest.mark.parametrize(
    "verdict,kind", [("unmet", "agreement_unmet"), ("partial", "agreement_partial")]
)
async def test_attainment_gap_is_low_non_scoring_and_retains_both_statements(verdict, kind):
    reply = Reply(
        dict(
            index=0,
            verdict=verdict,
            reasoning="The result states the remaining work.",
            reported_attainment=verdict,
            context_basis="none",
        )
    )
    result = await run_agreement_pairing(
        reply,
        [statement("intent", "To provide offline access in AmberDesk.")],
        [
            statement(
                "outcome",
                "AmberDesk was deployed for online access only; "
                "offline access was not implemented.",
            )
        ],
        check_run_id=None,
        settings=Settings(_env_file=None),
    )
    assert reply.calls == 1 and len(result.flags) == 1
    flag = result.flags[0]
    assert flag.severity.value == "low" and flag.detail["kind"] == kind
    assert flag.detail["affects_readiness"] is False
    assert flag.detail["reported_attainment"] == verdict
    assert flag.detail["intent_text"] and flag.detail["outcome_text"]


async def test_explained_gap_keeps_unmet_attainment_without_a_conflict_flag():
    reply = Reply(
        dict(
            index=0,
            verdict="consistent_in_context",
            reasoning="Offline access was not achieved because the trial was restricted online.",
            reported_attainment="unmet",
            context_basis="limitation",
        )
    )
    result = await run_agreement_pairing(
        reply,
        [statement("intent", "To provide offline access in AmberDesk.")],
        [
            statement(
                "outcome",
                "AmberDesk was deployed for online access only; "
                "offline access was not implemented.",
            )
        ],
        check_run_id=None,
        settings=Settings(_env_file=None),
    )
    assert not result.flags and result.verdict_counts == {"consistent_in_context": 1}
    assert result.explained_in_context[0]["reported_attainment"] == "unmet"
    assert result.explained_in_context[0]["context_basis"] == "limitation"


@pytest.mark.parametrize(
    "goal",
    [
        "To determine whether the Atlas module was implemented for offline reports.",
        "To evaluate the benefits if the Atlas module was implemented for offline reports.",
        "To determine whether Dr. Smith's Atlas module was implemented for offline reports.",
    ],
)
def test_cropped_assertion_inside_a_question_or_condition_is_not_a_fact(goal):
    finding = "The Atlas module was not implemented for offline reports."
    verdict = PairVerdict(
        index=0,
        verdict="contradictory",
        reasoning="Opposite statements.",
        reason_category="incompatible_descriptive_claims",
        objective_quote="Atlas module was implemented",
        finding_quote="Atlas module was not implemented",
    )
    problem, quotes = check_contradiction(
        Candidate(statement("intent", goal), statement("outcome", finding), 0.9),
        verdict,
        load_cues(),
        min_quote_chars=12,
    )
    assert problem == "objective_assertion_scope_unassessed" and quotes is None


@pytest.mark.parametrize(
    "verdict,attainment,basis",
    [
        ("unmet", "achieved", "none"),
        ("partial", "achieved", "none"),
        ("consistent", "unmet", "none"),
        ("consistent_in_context", "achieved", "none"),
        ("consistent_in_context", "unknown", "unknown"),
    ],
)
def test_incoherent_attainment_or_explanation_fields_are_rejected(verdict, attainment, basis):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PairVerdict(
            index=0,
            verdict=verdict,
            reasoning="r",
            reported_attainment=attainment,
            context_basis=basis,
        )


@pytest.mark.parametrize(
    "finding",
    [
        "If the Atlas module was not implemented, users would need paper receipts.",
        "Was the Atlas module not implemented for offline receipts?",
        "The Atlas module will never be implemented for offline receipts.",
    ],
)
def test_nonassertive_finding_cannot_supply_a_cropped_factual_conflict(finding):
    objective = "To implement the Atlas module. The Atlas module was implemented."
    quote = (
        "Atlas module was not implemented"
        if "was not" in finding
        else "Atlas module will never be implemented"
    )
    if finding.startswith("Was"):
        quote = "Atlas module not implemented"
    verdict = PairVerdict(
        index=0,
        verdict="contradictory",
        reasoning="Incompatible.",
        reason_category="incompatible_descriptive_claims",
        objective_quote="Atlas module was implemented",
        finding_quote=quote,
    )
    problem, quotes = check_contradiction(
        Candidate(statement("intent", objective), statement("outcome", finding), 0.9),
        verdict,
        load_cues(),
        min_quote_chars=12,
    )
    assert problem == "finding_assertion_scope_unassessed" and quotes is None


@pytest.mark.parametrize("enveloped", [True, False])
def test_response_envelopes_preserve_all_answer_fields(enveloped):
    item = dict(
        index=0,
        verdict="unmet",
        reasoning="Not delivered.",
        reported_attainment="unmet",
        context_basis="none",
    )
    values = [item]
    parsed = parse_verdict_batch({"verdicts": values} if enveloped else values, 1)
    assert parsed[0].reported_attainment == "unmet"
    assert parsed[0].reasoning == item["reasoning"]
    assert values == [item]


@pytest.mark.parametrize("enveloped", [True, False])
@pytest.mark.parametrize("problem", ["duplicate", "missing", "extra", "invalid", "incoherent"])
def test_both_response_envelopes_reject_an_invalid_complete_batch(enveloped, problem):
    item = dict(
        index=0,
        verdict="consistent",
        reasoning="Completed.",
        reported_attainment="achieved",
        context_basis="none",
    )
    values = [item, dict(item, index=1)]
    if problem == "duplicate":
        values[1]["index"] = 0
    elif problem == "missing":
        values.pop()
    elif problem == "extra":
        values.append(dict(item, index=2))
    elif problem == "invalid":
        values[1]["verdict"] = "unsupported"
    else:
        values[1]["reported_attainment"] = "unmet"
    with pytest.raises(PairingError):
        parse_verdict_batch({"verdicts": values} if enveloped else values, 2)


def test_achieved_objective_can_keep_a_source_explained_exclusion():
    item = PairVerdict(
        index=0,
        verdict="consistent_in_context",
        reasoning="The intended desktop search worked; voice search was outside scope.",
        reported_attainment="achieved",
        context_basis="scope",
    )
    assert item.reported_attainment == "achieved"


@pytest.mark.parametrize(
    "left,right,different",
    [
        ("Completed for Release Two.", "Release One omitted the feature.", True),
        ("Completed for version 2.1.", "Version 2.0 omitted the feature.", True),
        ("Completed for phase II.", "Phase 2 completed the feature.", False),
        ("Completed for release one.", "Release 1 completed the feature.", False),
        ("The release was completed.", "The release is complete.", False),
        ("Completed for release 2.", "The feature was not completed.", False),
        (
            "The evaluated build supports offline export.",
            "The evaluated build allowed offline export.",
            False,
        ),
        (
            "The current release supports PDF export.",
            "The current release provides PDF export.",
            False,
        ),
        ("To build a receipt viewer.", "The build passed acceptance tests.", False),
        ("Completed for phase five.", "Phase 5 completed the feature.", False),
        ("Completed for release 02.", "Release 2 completed the feature.", False),
        ("To build one dashboard.", "The implementation can build two dashboards.", False),
        ("To release two tools.", "The service could release one tool.", False),
    ],
)
def test_explicit_phase_qualifiers_are_source_data(left, right, different):
    assert different_reported_phases(left, right, load_cues()) is different


async def test_different_releases_remain_unassessed_without_spending_generation():
    reply = Reply({})
    result = await run_agreement_pairing(
        reply,
        [statement("intent", "To provide offline export. It was completed for Release Two.")],
        [statement("outcome", "Release One was not deployed with offline export.")],
        check_run_id=None,
        settings=Settings(_env_file=None),
        allow_absence_leads=False,
    )
    assert reply.calls == 0
    assert result.n_compared == 0 and result.n_locally_unassessed == 1
    assert not result.flags
    assert result.undetermined[0]["reported_attainment"] == "unknown"
    assert result.undetermined[0]["comparison_method"] == "phase_guard"


@pytest.mark.parametrize("cropped", [True, False])
def test_shared_release_mentions_cannot_hide_the_phases_of_quoted_assertions(cropped):
    objective = (
        "To implement offline export in Release Two. Offline export was "
        "implemented for Release One."
    )
    finding = "Offline export was never implemented for Release Two."
    verdict = PairVerdict(
        index=0,
        verdict="contradictory",
        reasoning="Incompatible.",
        reason_category="incompatible_descriptive_claims",
        objective_quote="Offline export was implemented"
        if cropped
        else "Offline export was implemented for Release One.",
        finding_quote="Offline export was never implemented" if cropped else finding,
    )
    problem, quotes = check_contradiction(
        Candidate(statement("intent", objective), statement("outcome", finding), 0.9),
        verdict,
        load_cues(),
        min_quote_chars=12,
    )
    assert problem == "different_quoted_phases" and quotes is None
