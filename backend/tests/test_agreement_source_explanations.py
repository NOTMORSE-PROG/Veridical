"""Causal evidence retains nonachievement without assessing an author's justification."""

import pytest

from app.checks.agreement.context import PairContext
from app.checks.agreement.cues import load_cues
from app.checks.agreement.extract import Statement
from app.checks.agreement.pair import Candidate, judge_pairs
from app.checks.agreement.source_explanations import reported_resource_explanation
from app.config import Settings


def statement(kind, text):
    return Statement(kind=kind, text=text, anchor="Â¶6", page=None, paragraph=6, cue="outcome_list")


def explanation(text, *, verdict="unmet", attainment="unmet", context=None):
    return reported_resource_explanation(
        statement("outcome", text),
        context if context is not None else PairContext(),
        load_cues(),
        verdict=verdict,
        attainment=attainment,
        intent_text="To implement automatic cropping in the scanner.",
    )


def test_cause_quote_is_owned_and_does_not_upgrade_attainment():
    text = (
        "The scanner did not implement automatic cropping because the licensed "
        "camera was unavailable."
    )
    value = explanation(text)
    assert value["source_quote"] == text
    assert value["source_anchor"] == "Â¶6"
    assert value["cause_quote"] in text
    assert value["adequacy_assessed"] is False


@pytest.mark.parametrize(
    "constraint",
    [
        "the licensed camera was not available",
        "the licensed cameras were not available during testing",
        "the licensed camera is not available",
        "the licensed cameras are not available for evaluation",
        "the licensed camera remained not available throughout testing",
        "the licensed camera was not actually available",
    ],
)
def test_availability_negation_is_owned_by_the_full_constraint_predicate(constraint):
    text = f"The scanner did not implement automatic cropping because {constraint}."
    value = explanation(text)
    assert value["source_quote"] == text
    assert value["cause_quote"] == constraint + "."
    assert value["method"] == "source_causal_limitation_v2"
    assert value["adequacy_assessed"] is False


@pytest.mark.parametrize(
    "constraint",
    [
        "no licensed camera was not available",
        "neither licensed camera was not available",
        "the licensed camera was not not available",
        "the licensed camera was never not available",
        "the not available camera was not available",
        "the not available camera was unavailable",
        "the licensed camera was not unavailable",
        "the licensed camera was not actually unavailable",
        "the licensed camera was available",
    ],
)
def test_extra_or_denied_negation_cannot_supply_a_source_explanation(constraint):
    text = f"The scanner did not implement automatic cropping because {constraint}."
    assert explanation(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "The scanner did not implement automatic cropping.",
        (
            "The scanner did not implement automatic cropping because operators "
            "preferred manual cropping."
        ),
        "The scanner did not implement automatic cropping, not because the camera was unavailable.",
        (
            "If the scanner did not implement automatic cropping because the "
            "camera was unavailable, manual cropping would be used."
        ),
        "A prior study did not implement automatic cropping because the camera was unavailable.",
        (
            "Participants reported that the scanner did not implement automatic "
            "cropping because the camera was unavailable."
        ),
        (
            "For example, the scanner did not implement automatic cropping because "
            "the camera was unavailable."
        ),
        (
            "The scanner did not implement automatic cropping. The camera was "
            "unavailable because repairs were delayed."
        ),
        "Release One did not implement automatic cropping because the camera was unavailable.",
        "The scanner did not implement automatic cropping because the camera was not unavailable.",
        (
            "The scanner did not implement automatic cropping. The reminder "
            "service did not implement SMS because the gateway was unavailable."
        ),
        (
            "The scanner did not implement automatic cropping, but an independent "
            "audit task was delayed because its sensor was unavailable."
        ),
        (
            "The scanner did not implement automatic cropping because operators "
            "preferred manual work although a camera was unavailable."
        ),
        (
            "It is false that the scanner did not implement automatic cropping "
            "because the camera was unavailable."
        ),
        (
            "The scanner did not implement automatic cropping because the camera "
            "was never unavailable."
        ),
        (
            "The scanner did not implement automatic cropping because the camera "
            "was unavailable, which was not the reason for the missing feature."
        ),
        (
            "The scanner did not implement automatic cropping because the camera "
            "was unavailable, said the participants."
        ),
        "The scanner did not implement automatic cropping because there was no lack of equipment.",
        (
            "The scanner did not implement automatic cropping because time "
            "constraints were not a factor."
        ),
        (
            "The scanner did not implement automatic cropping because the camera "
            "was not actually unavailable."
        ),
        "The other system did not implement automatic cropping because the camera was unavailable.",
        "Another scanner did not implement automatic cropping because the camera was unavailable.",
        (
            "The scanner did not implement an audit of automatic cropping because "
            "the camera was unavailable."
        ),
        "The scanner did not implement automatic cropping because no camera was unavailable.",
        "The scanner did not implement automatic cropping because neither camera was unavailable.",
        (
            "The scanner did not implement automatic cropping for the southern "
            "clinic because the camera was unavailable."
        ),
    ],
)
def test_noncausal_ambiguous_and_attributed_sources_do_not_suppress_a_gap(text):
    assert explanation(text) is None


@pytest.mark.parametrize(
    "verdict,attainment",
    [
        ("cannot_determine", "unknown"),
        ("contradictory", "unmet"),
        ("consistent", "achieved"),
        ("unmet", "unknown"),
        ("partial", "unmet"),
    ],
)
def test_source_rule_cannot_repair_unknown_or_override_other_judgments(verdict, attainment):
    assert (
        explanation(
            "The scanner did not implement cropping because the camera was unavailable.",
            verdict=verdict,
            attainment=attainment,
        )
        is None
    )


def test_unrelated_single_clause_cannot_explain_the_selected_target():
    assert (
        explanation(
            "The scanner did not implement text messaging because the gateway was unavailable."
        )
        is None
    )


@pytest.mark.parametrize(
    "intent,outcome",
    [
        (
            "To implement offline export for the northern clinic.",
            (
                "The system did not implement offline export for the northern clinic "
                "because its server was unavailable."
            ),
        ),
        (
            "The system will implement automatic cropping.",
            "The system did not implement automatic cropping because the camera was unavailable.",
        ),
    ],
)
def test_matching_source_scope_and_modal_objectives_preserve_explanations(intent, outcome):
    assert (
        reported_resource_explanation(
            statement("outcome", outcome),
            PairContext(),
            load_cues(),
            verdict="unmet",
            attainment="unmet",
            intent_text=intent,
        )
        is not None
    )


def test_mismatched_source_scope_does_not_explain_the_selected_target():
    assert (
        reported_resource_explanation(
            statement(
                "outcome",
                (
                    "The system did not implement offline export for the southern clinic "
                    "because its server was unavailable."
                ),
            ),
            PairContext(),
            load_cues(),
            verdict="unmet",
            attainment="unmet",
            intent_text="To implement offline export for the northern clinic.",
        )
        is None
    )


class UnmetReply:
    async def complete(self, *args, **kwargs):
        return {
            "verdicts": [
                dict(
                    index=0,
                    verdict="unmet",
                    reasoning="Not implemented.",
                    reported_attainment="unmet",
                    context_basis="none",
                )
            ]
        }


async def test_source_classification_retains_raw_judgment_and_explicit_method():
    candidate = Candidate(
        statement("intent", "To implement automatic cropping in the scanner."),
        statement(
            "outcome",
            (
                "The scanner did not implement automatic cropping because the licensed "
                "camera was unavailable."
            ),
        ),
        0.9,
    )
    judged, counters = await judge_pairs(
        UnmetReply(),
        [candidate],
        check_run_id=None,
        settings=Settings(_env_file=None),
        cues=load_cues(),
    )
    item = judged[0]
    assert item.verdict.verdict == "unmet" and item.verdict.context_basis == "none"
    assert item.verdict.reported_attainment == "unmet"
    assert item.final == "consistent_in_context"
    assert item.source_explanation["method"] == "source_causal_limitation_v2"
    assert not any(counters.values())
