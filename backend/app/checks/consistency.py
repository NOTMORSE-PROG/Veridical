"""Self-consistency voting (V-022, F3.4, D-006 amended by D-011): wraps
`app.checks.semantic`'s per-batch grading with TWO independent passes per
criterion, spending a THIRD tie-break call only when they disagree — never
a blind N=3 (D-011 halves the old quota cost). The agreement score IS the
confidence signal (D-006, one mechanism, used everywhere); the threshold
decision on what to DO with a low score belongs to `app.checks.escalation`
(V-023), called here at persist time so every criterion gets exactly one
CheckResult row, never two competing writes.

This is the entry point `app.pipeline.machine`'s semantic stage calls
instead of `semantic.run_semantic_checks` directly (that function stays,
single-pass, for anything that still wants the cheaper V2 behavior and for
the existing unit-test suite it was built against).
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.escalation import gate_vote
from app.checks.semantic import (
    PROMPT_VERSION,
    GradedVerdict,
    SemanticBatch,
    _grade_single_criterion,
    _persist,
    build_semantic_batches,
    grade_batch_verdicts,
)
from app.checks.signals import compute_shadow_signals, shadow_signals_as_detail
from app.config import Settings, get_settings
from app.ingest.schemas import ExtractionResult
from app.llm.base import LLMClient
from app.models.enums import ResultOutcome
from app.models.run import CheckResult

PASS_1 = "pass_1"
PASS_2 = "pass_2"
TIE_BREAK = "tie_break"


@dataclass(frozen=True)
class VoteResult:
    majority_verdict: str | None
    agreement: float
    votes: list[str | None]
    winner: GradedVerdict | None  # the pass whose evidence/reasoning we show
    escalation_reason: str | None  # set only when no pass could be voted at all


def _tally(votes: list[str]) -> tuple[str | None, float]:
    """Majority + agreement fraction over however many real votes came in
    (2 or 3 — never a blind N=3). A tie (e.g. 1-1-1 across three distinct
    verdicts) has no majority at all: `None`, never picked "by
    technicality" (ticket edge case)."""
    n = len(votes)
    counts = {v: votes.count(v) for v in set(votes)}
    verdict, count = max(counts.items(), key=lambda kv: kv[1])
    if count * 2 > n:
        return verdict, round(count / n, 3)
    return None, round(count / n, 3)


async def _vote_for_criterion(
    criterion: Any,
    batch: SemanticBatch,
    llm: LLMClient,
    check_run_id: int | None,
    anchor_kind: str,
    g1: GradedVerdict,
    g2: GradedVerdict,
) -> VoteResult:
    if g1.verdict is None or g2.verdict is None:
        reasons = [g.escalation_reason for g in (g1, g2) if g.escalation_reason]
        return VoteResult(None, 0.0, [g1.verdict, g2.verdict], None, "; ".join(reasons))

    if g1.verdict == g2.verdict:
        return VoteResult(g1.verdict, 1.0, [g1.verdict, g2.verdict], g1, None)

    # Disagreement: spend exactly one tie-break call (D-011), scoped to
    # this single criterion only — the rest of the batch is unaffected.
    tie = await _grade_single_criterion(
        criterion, batch, llm, check_run_id, anchor_kind, consistency_pass=TIE_BREAK
    )
    if tie is None:
        return VoteResult(
            None,
            0.0,
            [g1.verdict, g2.verdict, None],
            None,
            "Tie-break grading could not verify a verdict.",
        )
    tie_verdict, tie_anchors = tie
    g3 = GradedVerdict(
        criterion.id,
        tie_verdict.verdict,
        tie_verdict.reasoning,
        tie_verdict.evidence_quotes,
        tie_anchors,
        None,
    )
    votes = [g1.verdict, g2.verdict, g3.verdict]
    majority, agreement = _tally(votes)
    winner = next((g for g in (g1, g2, g3) if g.verdict == majority), None)
    return VoteResult(majority, agreement, votes, winner, None)


def _vote_detail(
    batch: SemanticBatch, vote: VoteResult, shadow_detail: dict[str, Any]
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "basis": "llm",
        "agreement": vote.agreement,
        "votes": vote.votes,
        "context_label": batch.label,
        "prompt_version": PROMPT_VERSION,
        "shadow": shadow_detail,
    }
    if vote.winner is not None:
        detail["verdict"] = vote.winner.verdict
        detail["reasoning"] = vote.winner.reasoning
        detail["evidence"] = [
            {"quote": q, "anchor": a}
            for q, a in zip(vote.winner.quotes or [], vote.winner.anchors or [], strict=True)
        ]
    else:
        detail["reason"] = (
            vote.escalation_reason or "The self-consistency vote reached no majority."
        )
    return detail


@dataclass(frozen=True)
class VotedCriterion:
    """One criterion's complete voting outcome, before persistence — the
    reusable core both the pipeline (persists via `_vote_batch`) and
    `tools/golden_harness.py` (V-025, never persists — golden excerpts
    aren't real check_run rows) build on, so the harness grades through
    the EXACT SAME production code path, never a reimplementation."""

    criterion: Any
    vote: VoteResult
    outcome: ResultOutcome
    detail: dict[str, Any]


async def vote_batch(
    batch: SemanticBatch,
    batch_criteria: list[Any],
    llm: LLMClient,
    settings: Settings,
    anchor_kind: str,
    *,
    check_run_id: int | None = None,
) -> list[VotedCriterion]:
    """Grades every criterion in a batch with V-022's N=2+tie-break voting
    and V-023's escalation gate — no DB writes, no `check_run_id` required
    (`None` is a valid, harness-only value; it only ever rides along in
    LLM-call audit rows and cache keys, never a persisted foreign key
    here)."""
    pass_1 = await grade_batch_verdicts(
        batch, batch_criteria, llm, check_run_id, anchor_kind, consistency_pass=PASS_1
    )
    pass_2 = await grade_batch_verdicts(
        batch, batch_criteria, llm, check_run_id, anchor_kind, consistency_pass=PASS_2
    )
    shadow_detail = shadow_signals_as_detail(compute_shadow_signals(batch.context_text, settings))

    voted: list[VotedCriterion] = []
    for criterion in batch_criteria:
        vote = await _vote_for_criterion(
            criterion,
            batch,
            llm,
            check_run_id,
            anchor_kind,
            pass_1[criterion.id],
            pass_2[criterion.id],
        )
        outcome, score = gate_vote(vote.majority_verdict, vote.agreement, settings)
        detail = _vote_detail(batch, vote, shadow_detail)
        if score is not None:
            detail["score"] = score
        voted.append(VotedCriterion(criterion, vote, outcome, detail))
    return voted


async def _vote_batch(
    session: AsyncSession,
    check_run_id: int,
    batch: SemanticBatch,
    batch_criteria: list[Any],
    llm: LLMClient,
    settings: Settings,
    anchor_kind: str,
) -> list[CheckResult]:
    voted = await vote_batch(
        batch, batch_criteria, llm, settings, anchor_kind, check_run_id=check_run_id
    )
    return [await _persist(session, check_run_id, v.criterion, v.outcome, v.detail) for v in voted]


async def run_semantic_checks_with_consistency(
    session: AsyncSession,
    check_run_id: int,
    criteria: list[Any],
    extraction: ExtractionResult,
    llm: LLMClient,
    settings: Settings | None = None,
) -> list[CheckResult]:
    """Entry point for the pipeline's semantic stage (V-018/machine.py):
    same batching as V2 (`build_semantic_batches`), but every criterion is
    graded by TWO independent passes + a conditional tie-break instead of
    one, and persisted with its agreement score (D-006) already gated
    through V-023's escalation threshold."""
    settings = settings or get_settings()
    if not criteria:
        return []
    batches, missing = build_semantic_batches(criteria, extraction)
    results = [
        await _persist(
            session,
            check_run_id,
            criterion,
            ResultOutcome.failed,
            {
                "score": 0.0,
                "basis": "structural-alignment",
                "reason": f"Referenced section '{target}' was not found in the manuscript.",
            },
        )
        for criterion, target in missing
    ]
    for batch, batch_criteria in batches:
        results.extend(
            await _vote_batch(
                session, check_run_id, batch, batch_criteria, llm, settings, extraction.anchor_kind
            )
        )
    return results
