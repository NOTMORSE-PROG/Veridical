"""V-017 unit tests: semantic grading batching, evidence containment, and
the retry/escalate loop. No DB — `CheckResult` rows are constructed but
never committed against a real session (a lightweight fake session
records `.add`/`.commit` calls, since none of this module's own logic
depends on what the DB does with them).
"""

from dataclasses import dataclass
from typing import Any

from app.checks.semantic import build_semantic_batches, run_semantic_checks
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.models.enums import ResultOutcome


@dataclass
class FakeCriterion:
    id: int
    text: str
    evidence: str | None = None
    # V-069: default None keeps every existing test exercising the exact
    # pre-ticket pass/partial/fail path.
    levels: list[dict] | None = None


class FakeSession:
    """Records every persisted `CheckResult` in `.added`, in commit order —
    enough for these tests to assert on without a real Postgres."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None


class ScriptedLLM:
    """Returns queued responses in order, one per `complete()` call —
    lets a test script "batch response, then retry response" precisely."""

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)
        self.calls: list[str] = []

    async def complete(self, prompt_type, prompt, *, prompt_version="unversioned", **context):
        self.calls.append(prompt)
        return self._responses.pop(0)


def _extraction(
    *, blocks: list[TextBlock], tree: SectionTree, anchor_kind: str = "page"
) -> ExtractionResult:
    return ExtractionResult(
        page_count=10,
        anchor_kind=anchor_kind,
        image_only=False,
        text_chars=sum(len(b.text) for b in blocks),
        section_tree=tree,
        blocks=blocks,
        images=[],
    )


# --- build_semantic_batches ------------------------------------------------------


def test_batches_group_by_named_section():
    tree = SectionTree(
        source="heuristics",
        nodes=[
            SectionNode(title="ABSTRACT", level=1, page=1),
            SectionNode(title="CHAPTER 1 INTRODUCTION", level=1, page=2),
        ],
    )
    blocks = [
        TextBlock(page=1, text="Abstract text here.", max_font_size=11, bold_ratio=0.0),
        TextBlock(page=2, text="Chapter one prose.", max_font_size=11, bold_ratio=0.0),
    ]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [
        FakeCriterion(id=1, text="The Abstract must be concise"),
        FakeCriterion(id=2, text="Chapter 1 clearly states the problem"),
    ]
    batches, missing = build_semantic_batches(criteria, extraction)
    assert missing == []
    labels = {b.label for b, _ in batches}
    assert labels == {"ABSTRACT", "CHAPTER 1 INTRODUCTION"}
    for _batch, batch_criteria in batches:
        assert len(batch_criteria) == 1


def test_batches_fall_back_to_whole_document_when_no_section_named():
    tree = SectionTree(source="heuristics", nodes=[SectionNode(title="ABSTRACT", level=1, page=1)])
    blocks = [TextBlock(page=1, text="Some prose.", max_font_size=11, bold_ratio=0.0)]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="The overall argument is coherent and well developed")]
    batches, missing = build_semantic_batches(criteria, extraction)
    assert missing == []
    assert len(batches) == 1
    assert batches[0][0].label == "whole document"


def test_criteria_naming_a_missing_section_are_reported_separately():
    tree = SectionTree(source="heuristics", nodes=[SectionNode(title="ABSTRACT", level=1, page=1)])
    blocks = [TextBlock(page=1, text="Some prose.", max_font_size=11, bold_ratio=0.0)]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="The Glossary defines all key terms")]
    batches, missing = build_semantic_batches(criteria, extraction)
    assert batches == []
    assert len(missing) == 1
    assert missing[0][0].id == 1


def test_criteria_sharing_a_section_are_batched_into_one_group():
    tree = SectionTree(source="heuristics", nodes=[SectionNode(title="ABSTRACT", level=1, page=1)])
    blocks = [TextBlock(page=1, text="Some prose.", max_font_size=11, bold_ratio=0.0)]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [
        FakeCriterion(id=1, text="The Abstract states the objective"),
        FakeCriterion(id=2, text="The Abstract states the methodology"),
    ]
    batches, _ = build_semantic_batches(criteria, extraction)
    assert len(batches) == 1
    assert len(batches[0][1]) == 2


# --- run_semantic_checks: fake-LLM mode (deterministic fixture) ------------------


async def test_fake_llm_mode_returns_deterministic_fixture_verdicts():
    from app.llm.fake import FakeLLMClient

    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [
        TextBlock(
            page=1,
            text="This is a test sentence used as evidence.",
            max_font_size=11,
            bold_ratio=0.0,
        )
    ]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [
        FakeCriterion(id=1, text="Criterion A needs the overall argument to be strong"),
        FakeCriterion(id=2, text="Criterion B needs the overall methodology to be sound"),
        FakeCriterion(id=3, text="Criterion C needs the overall writing to be clear"),
    ]
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, FakeLLMClient())
    assert [r.outcome for r in results] == [
        ResultOutcome.passed,
        ResultOutcome.passed,
        ResultOutcome.failed,
    ]
    assert [r.score for r in results] == [100.0, 50.0, 0.0]
    assert results[0].detail["verdict"] == "pass"
    assert results[0].detail["evidence"][0]["anchor"] == "page 1"


async def test_missing_section_criterion_never_calls_the_llm():
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [TextBlock(page=1, text="Prose.", max_font_size=11, bold_ratio=0.0)]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="The Glossary must define all terms")]

    llm = ScriptedLLM([])  # would raise IndexError if ever called
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert results[0].outcome == ResultOutcome.failed
    assert "not found" in results[0].detail["reason"]
    assert llm.calls == []


# --- hallucinated evidence: retry once, then escalate ----------------------------


async def test_hallucinated_quote_triggers_single_criterion_retry_then_succeeds():
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [
        TextBlock(
            page=1,
            text="The real sentence in the document.",
            max_font_size=11,
            bold_ratio=0.0,
        )
    ]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="Some criterion")]

    llm = ScriptedLLM(
        [
            {  # batch response: hallucinated quote, not present in the source
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "pass",
                        "reasoning": "Looks fine.",
                        "evidence_quotes": ["a sentence that does not exist"],
                    }
                ]
            },
            {  # single-criterion retry: a real quote this time
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "pass",
                        "reasoning": "Confirmed on retry.",
                        "evidence_quotes": ["The real sentence in the document."],
                    }
                ]
            },
        ]
    )
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert results[0].outcome == ResultOutcome.passed
    assert results[0].detail["reasoning"] == "Confirmed on retry."
    assert len(llm.calls) == 2


async def test_hallucinated_quote_still_hallucinated_on_retry_escalates():
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [
        TextBlock(
            page=1,
            text="The real sentence in the document.",
            max_font_size=11,
            bold_ratio=0.0,
        )
    ]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="Some criterion")]

    bad_response = {
        "verdicts": [
            {
                "index": 0,
                "verdict": "pass",
                "reasoning": "Looks fine.",
                "evidence_quotes": ["a sentence that does not exist"],
            }
        ]
    }
    llm = ScriptedLLM([bad_response, dict(bad_response)])
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert results[0].outcome == ResultOutcome.escalated
    assert "verify" in results[0].detail["reason"]
    assert len(llm.calls) == 2


async def test_malformed_response_retries_whole_batch_then_escalates_all():
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [TextBlock(page=1, text="Prose.", max_font_size=11, bold_ratio=0.0)]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="A"), FakeCriterion(id=2, text="B")]

    llm = ScriptedLLM([{"not_verdicts": []}, {"also_wrong": True}])
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert all(r.outcome == ResultOutcome.escalated for r in results)
    assert len(llm.calls) == 2


async def test_missing_index_in_batch_response_triggers_single_retry():
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [TextBlock(page=1, text="Some real prose here.", max_font_size=11, bold_ratio=0.0)]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="A"), FakeCriterion(id=2, text="B")]

    llm = ScriptedLLM(
        [
            {  # batch response only covers index 0, not 1
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "pass",
                        "reasoning": "ok",
                        "evidence_quotes": ["Some real prose here."],
                    }
                ]
            },
            {  # single-criterion retry for criterion B
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "fail",
                        "reasoning": "still missing",
                        "evidence_quotes": ["Some real prose here."],
                    }
                ]
            },
        ]
    )
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert results[0].outcome == ResultOutcome.passed
    assert results[1].outcome == ResultOutcome.failed
    assert len(llm.calls) == 2


async def test_no_semantic_criteria_makes_no_llm_call():
    tree = SectionTree(source="heuristics", nodes=[])
    extraction = _extraction(blocks=[], tree=tree)
    llm = ScriptedLLM([])
    session = FakeSession()
    results = await run_semantic_checks(session, 1, [], extraction, llm)
    assert results == []
    assert llm.calls == []


# --- V-068 Q1: unverified quotes survive a still-hallucinated retry ---------------


async def test_still_hallucinated_retry_carries_the_raw_quotes_as_unverified():
    """REGRESSION (V-068 Q1): the retry's own quotes/reasoning used to be
    thrown away entirely when they ALSO failed verification -- the panel
    said "could not verify the quoted evidence" while showing none of it.
    They must now survive into the escalated CheckResult's own detail."""
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [
        TextBlock(
            page=1, text="The real sentence in the document.", max_font_size=11, bold_ratio=0.0
        )
    ]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="Some criterion")]

    bad_response = {
        "verdicts": [
            {
                "index": 0,
                "verdict": "pass",
                "reasoning": "Looks fine.",
                "evidence_quotes": ["a sentence that does not exist"],
            }
        ]
    }
    llm = ScriptedLLM([bad_response, dict(bad_response)])
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert results[0].outcome == ResultOutcome.escalated
    assert results[0].detail["unverified_evidence"] == ["a sentence that does not exist"]


async def test_batch_hallucinated_quote_survives_a_retry_that_returns_nothing():
    """REGRESSION (backend-critic finding, V-068): the previous fix only
    covered the retry REACHING a verdict whose quotes also failed
    verification. This is the adjacent branch it missed: the ORIGINAL
    batch verdict had real quotes that failed verification, and the retry
    itself produced no usable verdict at all (the model dropped the
    criterion from its single-criterion response) -- the batch verdict's
    own quotes/reasoning were being discarded here too, reproducing the
    ticket's exact bug through a path its own new test didn't reach."""
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [
        TextBlock(
            page=1, text="The real sentence in the document.", max_font_size=11, bold_ratio=0.0
        )
    ]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="Some criterion")]

    llm = ScriptedLLM(
        [
            {  # batch response: hallucinated quote, real reasoning
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "pass",
                        "reasoning": "Batch pass reasoning.",
                        "evidence_quotes": ["a sentence that does not exist"],
                    }
                ]
            },
            {  # single-criterion retry: model drops the criterion (wrong index)
                "verdicts": [
                    {
                        "index": 5,
                        "verdict": "pass",
                        "reasoning": "irrelevant",
                        "evidence_quotes": ["irrelevant"],
                    }
                ]
            },
        ]
    )
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert results[0].outcome == ResultOutcome.escalated
    assert results[0].detail["unverified_evidence"] == ["a sentence that does not exist"]
    assert "verify" in results[0].detail["reason"]


def test_bare_verdict_list_is_accepted_not_treated_as_low_confidence():
    """REGRESSION (V-054, found live in the audit log): Gemini intermittently
    returns the verdicts array WITHOUT the documented {"verdicts": ...}
    wrapper. Strict parsing turned that into a failed pass, which the voting
    layer reported to the instructor as "the AI wasn't sure" — a parser quirk
    masquerading as a confidence signal about the manuscript.
    """
    from app.checks.schemas import GradeBatchResponse
    from app.checks.semantic import _unwrap_verdicts

    bare = [{"index": 0, "verdict": "fail", "reasoning": "r", "evidence_quotes": ["q"]}]
    wrapped = {"verdicts": bare}

    assert GradeBatchResponse.model_validate(_unwrap_verdicts(bare)).verdicts[0].index == 0
    # The documented shape must keep working untouched.
    assert GradeBatchResponse.model_validate(_unwrap_verdicts(wrapped)).verdicts[0].index == 0


def test_unwrapping_does_not_loosen_the_verdict_contract():
    """A bare list is re-enveloped, never waved through: each element still
    has to satisfy GradeVerdict (here: no evidence quotes)."""
    import pytest
    from pydantic import ValidationError

    from app.checks.schemas import GradeBatchResponse
    from app.checks.semantic import _unwrap_verdicts

    bad = [{"index": 0, "verdict": "fail", "reasoning": "r", "evidence_quotes": []}]
    with pytest.raises(ValidationError):
        GradeBatchResponse.model_validate(_unwrap_verdicts(bad))


# --- V-069: levelled criteria (single-pass path) ----------------------------

TIP_SCALE = [
    {"level": 1, "name": "Beginner", "descriptor": "no clear structure", "points": 1},
    {"level": 2, "name": "Acceptable", "descriptor": "states the topic", "points": 2},
    {"level": 3, "name": "Proficient", "descriptor": "states and previews", "points": 3},
    {"level": 4, "name": "Exemplary", "descriptor": "engaging and complete", "points": 4},
]


def test_criteria_listing_injects_the_scale_for_a_levelled_criterion_only():
    from app.checks.semantic import _criteria_listing

    criteria = [
        FakeCriterion(id=1, text="Levelled one", levels=TIP_SCALE),
        FakeCriterion(id=2, text="Ordinary pass/fail one", levels=None),
    ]
    listing = _criteria_listing(criteria)
    assert "Beginner (1) = no clear structure" in listing
    assert "Exemplary (4) = engaging and complete" in listing
    lines = listing.splitlines()
    assert "Beginner" not in lines[1]  # the non-levelled criterion's own line is untouched


async def test_levelled_criterion_verdict_becomes_a_scored_level():
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [
        TextBlock(
            page=1,
            text="The introduction states and previews.",
            max_font_size=11,
            bold_ratio=0.0,
        )
    ]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="Introduction quality", levels=TIP_SCALE)]
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "Proficient",
                        "reasoning": "States and previews the structure.",
                        "evidence_quotes": ["The introduction states and previews."],
                    }
                ]
            }
        ]
    )
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert results[0].outcome == ResultOutcome.passed
    assert results[0].score == 75.0
    assert results[0].detail["level"] == {
        "name": "Proficient",
        "ordinal": 3,
        "points": 3.0,
        "max_points": 4.0,
    }


async def test_levelled_criterion_unrecognized_verdict_escalates():
    """A model that ignores the per-criterion scale instruction and answers
    plain "pass" for a levelled criterion must escalate -- never silently
    scored as if "pass" were one of this criterion's own levels (charter
    rule 1: never guess)."""
    tree = SectionTree(source="heuristics", nodes=[])
    blocks = [TextBlock(page=1, text="Some prose.", max_font_size=11, bold_ratio=0.0)]
    extraction = _extraction(blocks=blocks, tree=tree)
    criteria = [FakeCriterion(id=1, text="Introduction quality", levels=TIP_SCALE)]
    llm = ScriptedLLM(
        [
            {
                "verdicts": [
                    {
                        "index": 0,
                        "verdict": "pass",
                        "reasoning": "Looks fine.",
                        "evidence_quotes": ["Some prose."],
                    }
                ]
            }
        ]
    )
    session = FakeSession()
    results = await run_semantic_checks(session, 1, criteria, extraction, llm)
    assert results[0].outcome == ResultOutcome.escalated
    assert "unrecognized verdict" in results[0].detail["reason"]
