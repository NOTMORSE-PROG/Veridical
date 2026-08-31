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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.annotators import (
    ROLE_PASS_1,
    ROLE_PASS_2,
    ROLE_TIE_BREAK,
    perspective_for,
)
from app.checks.escalation import gate_vote
from app.checks.injection import detect_injection_signal
from app.checks.levels import outcome_and_score
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


def _stance(role: str, prompt_version: str) -> str:
    """The stance text for one voting role — empty for prompt versions that
    have no stance slot (v1), so an A/B run can grade both versions through
    this same path without the older prompt silently gaining a new variable.
    """
    if prompt_version == "v1":
        return ""
    return perspective_for(role).stance


PASS_1 = "pass_1"
PASS_2 = "pass_2"
TIE_BREAK = "tie_break"

# BUG-045: shown verbatim to the instructor via EscalatedPanel.tsx's
# `item.reason` (escalation.py's `list_escalated` reads `detail["reason"]`
# straight off the persisted CheckResult). Worded as a fact about the
# document and about what the vote can and can't tell the instructor here,
# never as an accusation against the student (ground rule 3) -- the same
# language could describe a false positive (a capstone about prompt
# injection) without being wrong about either case.
INJECTION_SUSPECTED_REASON = (
    "This document contains text that appears to address an automated "
    "grader rather than the reader. Both AI grading passes read the same "
    "document text, so their agreement on this criterion should not be "
    "treated as confidence here; please review it directly."
)


@dataclass(frozen=True)
class VoteResult:
    majority_verdict: str | None
    agreement: float
    votes: list[str | None]
    winner: GradedVerdict | None  # the pass whose evidence/reasoning we show
    escalation_reason: str | None  # set only when no pass could be voted at all
    # V-068 Q1/Q2: raw quotes from a pass that produced real model output
    # but failed containment verification — set only alongside
    # `escalation_reason` (no winner), never alongside a real winner.
    # Deduplicated (both passes commonly fail on the identical quote).
    unverified_quotes: list[str] | None = None


def _collect_unverified_quotes(*graded: GradedVerdict) -> list[str] | None:
    quotes: list[str] = []
    for g in graded:
        if g.verdict is None and g.quotes:
            for q in g.quotes:
                if q not in quotes:
                    quotes.append(q)
    return quotes or None


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
    *,
    prompt_version: str = PROMPT_VERSION,
    skip_tie_break: bool = False,
) -> VoteResult:
    if g1.verdict is None or g2.verdict is None:
        # dict.fromkeys, not a plain list: the two grading passes fail with
        # the SAME reason far more often than not (same manuscript, same
        # unverifiable quote) — joining without dedup produced a literal
        # doubled sentence in real output (V-055 report screen review).
        reasons = dict.fromkeys(g.escalation_reason for g in (g1, g2) if g.escalation_reason)
        unverified = _collect_unverified_quotes(g1, g2)
        return VoteResult(None, 0.0, [g1.verdict, g2.verdict], None, "; ".join(reasons), unverified)

    if g1.verdict == g2.verdict:
        return VoteResult(g1.verdict, 1.0, [g1.verdict, g2.verdict], g1, None)

    if skip_tie_break:
        # BUG-045: the batch is already forced to escalate by
        # `vote_batch`'s injection override regardless of what a tie-break
        # would decide, so spending a real Gemini call on a verdict that
        # gets discarded two lines later is pure quota waste (ground rule
        # 2). The two real votes are still shown honestly (no fabricated
        # third vote).
        majority, agreement = _tally([g1.verdict, g2.verdict])
        return VoteResult(majority, agreement, [g1.verdict, g2.verdict], None, None)

    # Disagreement: spend exactly one tie-break call (D-011), scoped to
    # this single criterion only — the rest of the batch is unaffected.
    tie = await _grade_single_criterion(
        criterion,
        batch,
        llm,
        check_run_id,
        anchor_kind,
        consistency_pass=TIE_BREAK,
        prompt_version=prompt_version,
        annotator_stance=_stance(ROLE_TIE_BREAK, prompt_version),
    )
    if tie is None:
        return VoteResult(
            None,
            0.0,
            [g1.verdict, g2.verdict, None],
            None,
            "Tie-break grading could not verify a verdict.",
            _collect_unverified_quotes(g1, g2),
        )
    tie_verdict, tie_anchors = tie
    if tie_anchors is None:
        # The tie-break reached a verdict but its quotes ALSO failed
        # verification — never promote to a decided majority; carry the
        # raw quotes through as unverified evidence like every other
        # verification failure in this module.
        unverified = _collect_unverified_quotes(g1, g2) or list(tie_verdict.evidence_quotes or [])
        return VoteResult(
            None,
            0.0,
            [g1.verdict, g2.verdict, None],
            None,
            "Could not verify the quoted evidence after a retry.",
            unverified or None,
        )
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
        if vote.unverified_quotes:
            detail["unverified_evidence"] = vote.unverified_quotes
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
    prompt_version: str = PROMPT_VERSION,
) -> list[VotedCriterion]:
    """Grades every criterion in a batch with V-022's N=2+tie-break voting
    and V-023's escalation gate — no DB writes, no `check_run_id` required
    (`None` is a valid, harness-only value; it only ever rides along in
    LLM-call audit rows and cache keys, never a persisted foreign key
    here)."""
    # Each pass reads the SAME text under a genuinely different stance
    # (V-054/D-017). Before this, both passes were the same model, same
    # prompt, at temperature 0 — a repeated near-deterministic sample, so
    # their agreement carried almost no information even though that
    # agreement IS the confidence signal (D-006).
    pass_1 = await grade_batch_verdicts(
        batch,
        batch_criteria,
        llm,
        check_run_id,
        anchor_kind,
        consistency_pass=PASS_1,
        prompt_version=prompt_version,
        annotator_stance=_stance(ROLE_PASS_1, prompt_version),
    )
    pass_2 = await grade_batch_verdicts(
        batch,
        batch_criteria,
        llm,
        check_run_id,
        anchor_kind,
        consistency_pass=PASS_2,
        prompt_version=prompt_version,
        annotator_stance=_stance(ROLE_PASS_2, prompt_version),
    )
    shadow_detail = shadow_signals_as_detail(compute_shadow_signals(batch.context_text, settings))
    # BUG-045: computed once per batch (regex, no LLM call, no quota cost),
    # not per criterion -- every criterion in a batch shares the same
    # context_text, so one match poisons the whole batch's vote the same
    # way.
    injection_signal = detect_injection_signal(batch.context_text)

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
            prompt_version=prompt_version,
            skip_tie_break=injection_signal.suspected,
        )
        outcome, score = gate_vote(
            vote.majority_verdict, vote.agreement, settings, criterion=criterion
        )
        detail = _vote_detail(batch, vote, shadow_detail)
        if vote.majority_verdict is not None:
            # V-069: `gate_vote` already confirmed this verdict decides
            # something -- re-deriving it here (rather than having
            # `gate_vote` return it) keeps `gate_vote`'s own return shape
            # unchanged for every existing caller (see its docstring).
            verdict_outcome, _verdict_score, level = outcome_and_score(
                criterion, vote.majority_verdict
            )
            if outcome == ResultOutcome.passed and level is not None:
                detail["level"] = level.as_detail()
            elif verdict_outcome == ResultOutcome.escalated and "reason" not in detail:
                # `backend-critic` finding, live-reproduced: BOTH passes can
                # agree (agreement 1.0, a real `vote.winner`) on a verdict
                # string that still doesn't name any of THIS criterion's own
                # levels (a model that answers "Good" instead of the exact
                # required level name) -- `_vote_detail`'s "has a winner"
                # branch never sets `detail["reason"]`, so this landed in
                # the panel as an unexplained "Agreement N/N," reading as
                # confidence when the AI hasn't actually decided anything
                # this criterion's own scale can score -- the identical
                # overreliance trap BUG-045 closed for prompt injection,
                # reopened here. Same message `semantic.py`'s single-pass
                # path already uses for this exact case.
                detail["reason"] = (
                    f"The grading response used an unrecognized verdict "
                    f"({vote.majority_verdict!r}) for this criterion's own scale."
                )
        if injection_signal.suspected:
            # Overrides the vote's own outcome regardless of what it
            # decided (including a "perfect agreement" auto-accept) --
            # that agreement is exactly what BUG-045 found cannot be
            # trusted once a batch matches this signal. The vote's own
            # verdict/reasoning/evidence, if any, are left in `detail`
            # untouched so the instructor can still see what the AI said;
            # only the outcome and the reason shown change.
            outcome, score = ResultOutcome.escalated, None
            detail["injection_suspected"] = True
            detail["injection_matched_pattern"] = injection_signal.matched_pattern_id
            detail["injection_matched_snippet"] = injection_signal.matched_snippet
            detail["reason"] = INJECTION_SUSPECTED_REASON
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
    cancellation_boundary: Callable[[], Awaitable[None]] | None = None,
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
    results: list[CheckResult] = []
    for criterion, target in missing:
        results.append(
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
        )
        if cancellation_boundary is not None:
            await cancellation_boundary()
    for batch, batch_criteria in batches:
        results.extend(
            await _vote_batch(
                session, check_run_id, batch, batch_criteria, llm, settings, extraction.anchor_kind
            )
        )
        # One batch is the bounded LLM unit: both passes finish and every
        # returned result persists before this boundary. No later batch may
        # spend quota after a cancellation request has become visible.
        if cancellation_boundary is not None:
            await cancellation_boundary()
    return results
