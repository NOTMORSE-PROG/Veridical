"""V-011: validation gate (coverage + duplicates) and the retry/review
loop. All on doubles — no network, no DB (a spy session stands in for
audit_log writes, which only need `.add()`/`.flush()`).
"""

from typing import Any

import pytest

from app.config import get_settings
from app.errors import QuotaExhaustedError
from app.ingest.schemas import TextBlock
from app.llm.base import LLMClient
from app.models.audit import AuditLog
from app.rubric.schemas import ParsedCriterion
from app.rubric.validate import (
    attempt_decomposition,
    coverage_ratio,
    run_gate,
)


def _block(text: str) -> TextBlock:
    return TextBlock(page=1, text=text, max_font_size=11.0, bold_ratio=0.0, is_furniture=False)


def _criterion(text: str, evidence: str = "", weight: float = 10.0, type_: str = "structural"):
    return ParsedCriterion(text=text, type=type_, evidence_needed=evidence, weight=weight)


class _SpySession:
    """Enough AsyncSession surface for `_log_attempt` — a real DB isn't
    needed to prove attempt-count/payload correctness."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class ScriptedLLM(LLMClient):
    """Each call pops the next scripted outcome: a response dict, or an
    exception instance to raise."""

    def __init__(self, outcomes: list[Any]):
        self.calls = 0
        self._outcomes = outcomes

    async def complete(
        self, prompt_type: str, prompt: str, *, prompt_version: str = "unversioned", **context: Any
    ) -> dict[str, Any]:
        self.calls += 1
        outcome = self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


SETTINGS = get_settings()

RAW_TEXT = (
    "Manuscript must include an abstract section.\nArgument in chapter four must be well developed."
)
BLOCKS = [_block(line) for line in RAW_TEXT.split("\n")]

GOOD_RESPONSE = {
    "criteria": [
        {
            "text": "Has an abstract",
            "type": "structural",
            "evidence_needed": "Manuscript must include an abstract section",
            "weight": 5,
        },
        {
            "text": "Argument is well developed",
            "type": "semantic",
            "evidence_needed": "Chapter four argument well developed",
            "weight": 15,
        },
    ]
}

LOW_COVERAGE_RESPONSE = {
    "criteria": [{"text": "Has a title page", "type": "structural", "weight": 1}]
}


def test_coverage_ratio_full_when_every_line_is_reflected():
    criteria = [_criterion("abstract required", "manuscript include abstract section")]
    blocks = [_block("Manuscript must include an abstract section.")]
    ratio = coverage_ratio(blocks, criteria, min_line_chars=10, word_overlap_ratio=0.3)
    assert ratio == 1.0


def test_coverage_ratio_zero_when_criteria_are_unrelated():
    criteria = [_criterion("has a title page")]
    blocks = [_block("The methodology must justify the chosen statistical test.")]
    ratio = coverage_ratio(blocks, criteria, min_line_chars=10, word_overlap_ratio=0.3)
    assert ratio == 0.0


def test_coverage_ratio_ignores_short_furniture_like_lines():
    criteria: list[ParsedCriterion] = []
    blocks = [_block("Page 3")]  # below the min_line_chars floor
    ratio = coverage_ratio(blocks, criteria, min_line_chars=20, word_overlap_ratio=0.3)
    assert ratio == 1.0  # nothing substantive to lose


def test_run_gate_passes_on_good_coverage_no_duplicates():
    criteria = [
        _criterion("Has an abstract", "manuscript include abstract section"),
        _criterion("Argument developed", "chapter four argument well developed"),
    ]
    result = run_gate(BLOCKS, criteria, SETTINGS)
    assert result.ok
    assert result.reasons == []


def test_run_gate_fails_on_duplicate_criteria():
    criteria = [
        _criterion("Has an abstract of at most 250 words", "abstract section word count"),
        _criterion("HAS AN ABSTRACT of at most 250 words!!", "abstract section word count"),
    ]
    result = run_gate(BLOCKS, criteria, SETTINGS)
    assert not result.ok
    assert any("duplicate" in reason for reason in result.reasons)


def test_run_gate_fails_on_low_coverage():
    result = run_gate(
        BLOCKS,
        [ParsedCriterion.model_validate(c) for c in LOW_COVERAGE_RESPONSE["criteria"]],
        SETTINGS,
    )
    assert not result.ok
    assert any("source text" in reason for reason in result.reasons)


async def test_retries_after_malformed_output_then_succeeds():
    """AC1: injected malformed LLM output -> retry -> clean result."""
    llm = ScriptedLLM([{"not_criteria": []}, GOOD_RESPONSE])
    session = _SpySession()
    outcome = await attempt_decomposition(session, 1, BLOCKS, RAW_TEXT, llm, SETTINGS)
    assert not outcome.needs_review
    assert outcome.attempts == 2
    assert llm.calls == 2
    assert [c.text for c in outcome.criteria] == ["Has an abstract", "Argument is well developed"]


async def test_persistent_failure_lands_in_needs_review_with_partial_never_raises():
    """AC2: exhausting all retries never raises — it's a needs_review
    outcome, never a dead-end error."""
    llm = ScriptedLLM([LOW_COVERAGE_RESPONSE])  # same bad answer every attempt
    session = _SpySession()
    outcome = await attempt_decomposition(session, 42, BLOCKS, RAW_TEXT, llm, SETTINGS)
    assert outcome.needs_review is True
    assert outcome.attempts == SETTINGS.rubric_parse_max_retries + 1
    assert llm.calls == SETTINGS.rubric_parse_max_retries + 1
    # The partial (invalid but schema-valid) criteria are kept, not dropped.
    assert len(outcome.criteria) == 1
    assert outcome.issues  # the reason is preserved for the review banner


async def test_every_attempt_is_logged_to_audit_log_with_attempt_number_and_reason():
    """AC3: every attempt logged to audit_log (attempt #, failure reason)."""
    llm = ScriptedLLM([{"criteria": []}, LOW_COVERAGE_RESPONSE, GOOD_RESPONSE])
    session = _SpySession()
    outcome = await attempt_decomposition(session, 7, BLOCKS, RAW_TEXT, llm, SETTINGS)
    assert not outcome.needs_review

    audit_rows = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audit_rows) == 3
    assert [row.payload["attempt"] for row in audit_rows] == [1, 2, 3]
    assert [row.payload["ok"] for row in audit_rows] == [False, False, True]
    assert all(row.payload["rubric_id"] == 7 for row in audit_rows)
    assert audit_rows[0].payload["issues"]  # schema failure reason present
    assert audit_rows[1].payload["issues"]  # coverage failure reason present
    assert audit_rows[2].payload["coverage_ratio"] == 1.0


async def test_quota_exhausted_propagates_without_burning_extra_retries():
    """Edge case: an infra failure (quota/api-down) is not a content
    problem — it must not be silently retried against the daily meter."""
    llm = ScriptedLLM([QuotaExhaustedError("daily quota reached")])
    session = _SpySession()
    with pytest.raises(QuotaExhaustedError):
        await attempt_decomposition(session, 1, BLOCKS, RAW_TEXT, llm, SETTINGS)
    assert llm.calls == 1  # no retry attempted for an infra failure
