"""Semantic grading pipeline (F3.3): bounded criterion batches share the
full available source, with no silent truncation. Quote containment and a
separate evidence-support review precede any accepted rubric judgment.

Single-pass in V2 by design (self-consistency arrives V-022): the call
interface here (`run_semantic_checks`) takes a plain `LLMClient` and
returns persisted results, so N-pass voting can wrap this module later
without reshaping it.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.levels import level_scale_prompt_fragment, outcome_and_score
from app.checks.rules.sections import find_section_node, identify_target_section
from app.checks.schemas import GradeVerdict
from app.checks.signals import compute_shadow_signals, shadow_signals_as_detail
from app.checks.source_quality import source_limitation_detail
from app.checks.support import apply_support, check_support_available, context_limitation
from app.config import Settings, get_settings
from app.errors import ApiDownError, VeridicalError
from app.ingest.analytical_source import analytical_source
from app.ingest.patterns import load_patterns
from app.ingest.references import non_reference_blocks, select_references
from app.ingest.schemas import ExtractionResult, SectionNode, TextBlock
from app.llm.base import LLMClient
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult

PROMPT_TYPE = "semantic_grading"
# v4 consumes reviewer stances and represents missing evidence as uncertainty.
# Its new cache identity cannot reuse the earlier v3 judgment contract.
PROMPT_VERSION = "v5"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"
_WHOLE_DOCUMENT_KEY = "__whole_document__"

_WHITESPACE = re.compile(r"\s+")


class SemanticGradeError(VeridicalError):
    """The model's structured output didn't validate against the verdict
    contract. Caught internally by the batch retry loop — never raised
    past this module (same convention as V-010's `RubricParseError`)."""


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


@dataclass(frozen=True)
class SemanticBatch:
    label: str
    blocks: list[TextBlock]
    body_context: str | None = None
    reference_context: str | None = None
    scope_patterns_file: Path | None = None
    source_scopes: tuple[tuple[TextBlock, ...], ...] | None = None
    source_context: str | None = None
    source_coverage: dict | None = None

    @property
    def context_text(self) -> str:
        if self.source_context is not None:
            return self.source_context
        return "\n".join(b.text for b in self.blocks if not b.is_furniture and b.text.strip())


def _section_span_blocks(
    node: SectionNode, next_node: SectionNode | None, blocks: list[TextBlock], anchor_kind: str
) -> list[TextBlock]:
    if anchor_kind == "page":
        start, end = node.page or 0, next_node.page if next_node else None
        return [
            b
            for b in blocks
            if b.page is not None and start <= b.page and (end is None or b.page < end)
        ]
    start = node.paragraph or 0
    end = next_node.paragraph if next_node else None
    return [
        b
        for b in blocks
        if b.paragraph is not None and start <= b.paragraph and (end is None or b.paragraph < end)
    ]


def build_semantic_batches(
    criteria: list[Any], extraction: ExtractionResult, *, settings: Settings | None = None
) -> tuple[list[tuple[SemanticBatch, list[Any]]], list[tuple[Any, str]]]:
    """Share full source across bounded batches instead of inferring an
    evidence boundary from one incidental heading word. Returns
    `(batches, missing)`: `missing` is (criterion, target) pairs whose
    named section could not be located by title match -- BUG-221: a lookup
    miss is not proof of genuine absence (see `_title_candidates`'s own
    BUG-048 comment), so these are escalated to the instructor by the
    caller, without spending a call, never auto-failed. Same treatment as
    `required_section_present`'s identical situation.
    """
    missing: list[tuple[Any, str]] = []
    eligible = []
    for criterion in criteria:
        target = identify_target_section(criterion)
        if target is not None and find_section_node(target, extraction.section_tree) is None:
            missing.append((criterion, target))
            continue
        eligible.append(criterion)
    # All eligible criteria now see the same source. Coalesce their cohorts
    # rather than charging the full text repeatedly for keyword-derived hints.
    if not eligible:
        return [], missing
    settings = settings or get_settings()
    size = settings.semantic_batch_max_criteria
    patterns = load_patterns(settings.ingest_patterns_file)
    selected = select_references(extraction, patterns)
    body = non_reference_blocks(extraction, patterns, selection=selected, exclude_heading=True)
    source = analytical_source(extraction)
    scopes = source.scopes_for([b for b in extraction.blocks if not b.is_furniture])
    batch = SemanticBatch(
        "whole document",
        [b for scope in scopes for b in scope],
        source.context(body) if not selected.issues and selected.span else None,
        source.context(selected.span) if not selected.issues else None,
        settings.semantic_evidence_scope_patterns_file,
        source_scopes=scopes,
        source_context=source.context(extraction.blocks),
        source_coverage=source.detail(),
    )
    return [
        (batch, eligible[start : start + size]) for start in range(0, len(eligible), size)
    ], missing


def _criteria_listing(criteria: list[Any]) -> str:
    lines = []
    for i, criterion in enumerate(criteria):
        line = f"{i}. {criterion.text}"
        if getattr(criterion, "evidence", None):
            line += f" (Evidence needed: {criterion.evidence})"
        scale = level_scale_prompt_fragment(criterion)
        if scale:
            line += f" [{scale}]"
        lines.append(line)
    return "\n".join(lines)


def prompt_file_for(prompt_version: str) -> Path:
    return Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{prompt_version}.txt"


def _build_prompt(
    batch: SemanticBatch,
    criteria: list[Any],
    *,
    prompt_version: str = PROMPT_VERSION,
    annotator_stance: str = "",
) -> str:
    """`prompt_version` is a parameter (not just the module constant) so an
    A/B run can grade the SAME items under two prompts in one process and
    compare them with a paired test (V-054). `annotator_stance` is empty for
    v1, which has no stance slot — replacing an absent placeholder is a no-op,
    so both versions build correctly from the same call site.
    """
    template = prompt_file_for(prompt_version).read_text(encoding="utf-8")
    return (
        template.replace("{annotator_stance}", annotator_stance)
        .replace("{criteria_list}", _criteria_listing(criteria))
        .replace("{context_label}", batch.label)
        .replace("{context_text}", batch.context_text)
    )


def _normalized_layout(
    blocks: list[TextBlock],
    *,
    preserve_case: bool = False,
) -> tuple[str, list[tuple[int, int, TextBlock]]]:
    """Joins block texts into one normalized string, remembering each
    block's [start, end) character span within it. Ingestion blocks are
    per LINE (V-004), but a model's evidence quote is often a full
    sentence spanning several lines/blocks — searching block-by-block
    would reject every such quote as "hallucinated" even when it's a real,
    contiguous excerpt. Joining first (real bug, found live grading the
    owner's real proposal PDF) is what makes multi-line quotes verifiable.
    """
    parts: list[str] = []
    spans: list[tuple[int, int, TextBlock]] = []
    cursor = 0
    for block in blocks:
        norm = _WHITESPACE.sub(" ", block.text).strip() if preserve_case else _normalize(block.text)
        if not norm:
            continue
        parts.append(norm)
        spans.append((cursor, cursor + len(norm), block))
        cursor += len(norm) + 1  # +1 for the join separator below
    return " ".join(parts), spans


def _anchor_at_position(
    pos: int, spans: list[tuple[int, int, TextBlock]], anchor_kind: str
) -> str | None:
    for start, end, block in spans:
        if start <= pos < end:
            if anchor_kind == "page" and block.page is not None:
                return f"page {block.page}"
            if block.paragraph is not None:
                return f"paragraph {block.paragraph}"
            return None
    return None


def _drop_claimed_quotes(
    quotes: list[str], anchors: list[str], claimed: set[str]
) -> tuple[list[str], list[str]]:
    """BUG-176/BUG-155: `_verify_quotes` only checks PROVENANCE (does this
    text exist in the manuscript) — at whole-document batch scope, that
    degenerates to "this text exists somewhere", so an arbitrary sentence
    verifies identically as evidence for every criterion in the batch. The
    minimum-viable half of the fix (both tickets' own "at minimum" ask):
    a quote's mere existence never establishes it is ABOUT the criterion
    it's attached to, but the one thing structurally provable without a
    relevance model is that the SAME quote cannot honestly be the
    strongest evidence for two different criteria at once. Drops any
    quote (and its paired anchor) already claimed by an earlier criterion
    in this same batch, preserving the 1:1 quote<->anchor pairing
    `_verify_quotes` already returns."""
    kept_quotes: list[str] = []
    kept_anchors: list[str] = []
    for quote, anchor in zip(quotes, anchors, strict=True):
        if _normalize(quote) not in claimed:
            kept_quotes.append(quote)
            kept_anchors.append(anchor)
    return kept_quotes, kept_anchors


def _verify_quotes(
    quotes: list[str],
    blocks: list[TextBlock],
    anchor_kind: str,
    *,
    scopes: tuple[tuple[TextBlock, ...], ...] | None = None,
) -> list[str] | None:
    """Reject the verdict if any quote is absent or its source location
    is missing or ambiguous. A supported page/paragraph is not a claim
    of an exact bounding box or of the quote's relevance to the criterion."""
    source_scopes = scopes if scopes is not None else (tuple(blocks),)
    layouts = [_normalized_layout(list(scope), preserve_case=True) for scope in source_scopes]
    folded_layouts = None
    anchors = []
    for quote in quotes:
        normalized_quote = _WHITESPACE.sub(" ", quote).strip()
        if not normalized_quote:
            return None
        source_layouts = layouts
        if not any(normalized_quote in text for text, _spans in source_layouts):
            # Retain case-insensitive compatibility only when it identifies
            # one supported location. Exact source spelling takes precedence.
            if folded_layouts is None:
                folded_layouts = [_normalized_layout(list(scope)) for scope in source_scopes]
            source_layouts = folded_layouts
            normalized_quote = normalized_quote.lower()
        locations: set[str | None] = set()
        for source_text, source_spans in source_layouts:
            pos = source_text.find(normalized_quote)
            while pos != -1:
                locations.add(_anchor_at_position(pos, source_spans, anchor_kind))
                pos = source_text.find(normalized_quote, pos + 1)
        # Multiple occurrences on the same page support a page-only anchor,
        # not an exact rectangle. Never choose between different locations.
        if len(locations) != 1 or None in locations:
            return None
        anchors.append(next(iter(locations)))
    return anchors


@dataclass(frozen=True)
class GradeBatchCandidates:
    """Safely attributed candidates still require source, scale and vote checks."""

    verdicts: list[GradeVerdict]
    invalid_indices: tuple[int, ...]


def _indexed_candidates(response: Any, expected_count: int) -> GradeBatchCandidates:
    envelope = _unwrap_verdicts(response)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("verdicts"), list):
        raise SemanticGradeError("Grading response did not contain a verdict list.")
    entries = envelope["verdicts"]
    if not entries:
        raise SemanticGradeError("Grading response contained no indexed items.")
    indices = []
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get("index")) is not int:
            raise SemanticGradeError("Grading response had an invalid verdict index.")
        indices.append(entry["index"])
    if len(indices) != len(set(indices)):
        raise SemanticGradeError(f"Grading response had duplicate verdict indices: {indices}")
    if any(index < 0 or index >= expected_count for index in indices):
        raise SemanticGradeError("Grading response had an out-of-range verdict index.")
    valid, invalid = [], []
    for entry in entries:
        try:
            valid.append(GradeVerdict.model_validate(entry))
        except ValidationError:
            invalid.append(entry["index"])
    return GradeBatchCandidates(valid, tuple(invalid))


async def _call_grade(
    prompt: str,
    llm: LLMClient,
    check_run_id: int | None,
    *,
    expected_count: int,
    consistency_pass: str = "single",
    prompt_version: str = PROMPT_VERSION,
    validation_attempt: int = 0,
    recovery_phase: str = "batch",
) -> GradeBatchCandidates:
    # `consistency_pass` rides in **context: it differentiates the response-
    # cache key (D-011) so two independent voting passes over the SAME
    # batch/criteria are two real samples, not a cache hit of each other,
    # while a genuine re-run (Flow E, same pass) still hits cache for free.
    # The fake client (V-022) uses it to select a per-pass fixture so tests
    # can script a deterministic disagreement.
    response = await llm.complete(
        PROMPT_TYPE,
        prompt,
        prompt_version=prompt_version,
        check_run_id=check_run_id,
        consistency_pass=consistency_pass,
        validation_attempt=validation_attempt,
        grading_response_contract=get_settings().semantic_grading_response_contract,
        recovery_phase=recovery_phase,
    )
    # BUG-177 remains global: ambiguous attribution invalidates the envelope.
    # Identifiable invalid content cannot invalidate a different criterion.
    return _indexed_candidates(response, expected_count)


def _unwrap_verdicts(response: Any) -> Any:
    """Accept a bare verdict LIST as well as the documented
    `{"verdicts": [...]}` envelope.

    Found live (V-054) via the audit log: Gemini intermittently drops the
    wrapper and returns the array directly, for the same prompt that produced
    a correctly wrapped response moments earlier. Strict parsing turned that
    into a failed pass, which the voting layer then reported as low
    confidence — so a parser quirk was surfacing to the instructor as "the AI
    wasn't sure", inflating the escalation rate with a cause that has nothing
    to do with the manuscript (charter rule 9: states must stay distinct).

    This is unambiguous to repair rather than a guess: a top-level list can
    only be the verdicts array, and every element still has to validate
    against `GradeVerdict`, so nothing is loosened about the contract itself.
    """
    if isinstance(response, list):
        return {"verdicts": response}
    return response


async def _grade_single_criterion(
    criterion: Any,
    batch: SemanticBatch,
    llm: LLMClient,
    check_run_id: int | None,
    anchor_kind: str,
    *,
    consistency_pass: str = "single",
    prompt_version: str = PROMPT_VERSION,
    annotator_stance: str = "",
    recovery_phase: str = "single_criterion",
) -> tuple[GradeVerdict, list[str] | None] | None:
    """One-criterion retry, same context — used both when the batch
    response omitted a criterion and when its quotes failed containment,
    and (V-022) as the single-criterion tie-break call on a voting split.

    The `anchors` half of the returned tuple is `None` when the model DID
    return a verdict but its quotes failed containment verification too —
    the caller still gets the raw `verdict` (quotes + reasoning) back so it
    can show them as UNVERIFIED evidence instead of silently discarding
    them (V-068 Q1: this is the exact gap that made the panel say "could
    not verify the quoted evidence" while showing none of it). Charter
    rule 1 still holds: unverified quotes never become a decided
    pass/fail, which is the caller's job to enforce, not this function's.
    A bare `None` return means the model produced no usable verdict for
    this criterion at all."""
    prompt = _build_prompt(
        batch, [criterion], prompt_version=prompt_version, annotator_stance=annotator_stance
    )
    try:
        parsed = await _call_grade(
            prompt,
            llm,
            check_run_id,
            expected_count=1,
            consistency_pass=consistency_pass,
            prompt_version=prompt_version,
            recovery_phase=recovery_phase,
        )
    except SemanticGradeError:
        return None
    verdict = next((v for v in parsed.verdicts if v.index == 0), None)
    if verdict is None:
        return None
    anchors = _verify_quotes(
        verdict.evidence_quotes, batch.blocks, anchor_kind, scopes=batch.source_scopes
    )
    return verdict, anchors


@dataclass(frozen=True)
class GradedVerdict:
    """One criterion's outcome from a single grading PASS, before any
    voting (V-022) or persistence happens — `verdict is None` means this
    pass could not produce a verifiable verdict even after its own
    single-criterion retry (`escalation_reason` explains why)."""

    criterion_id: int
    verdict: str | None
    reasoning: str | None
    quotes: list[str] | None
    anchors: list[str] | None
    escalation_reason: str | None
    failure_outcome: ResultOutcome | None = None
    failure_phase: str | None = None
    request_attempted: bool = True


def _downgrade_if_claimed(verdict: GradedVerdict, claimed: set[str]) -> GradedVerdict:
    """BUG-176/BUG-155 (backend review finding, live-reproduced): applies
    the same anti-reuse guard `grade_batch_verdicts` already applies
    WITHIN one grading pass, but at the self-consistency VOTE level —
    `app.checks.consistency.vote_batch` calls `grade_batch_verdicts`
    TWICE independently (pass_1, pass_2) plus a per-criterion tie-break,
    each with no visibility into what a SIBLING criterion's WINNING
    evidence already used. Two internally-clean passes can still each
    borrow the same quote for two DIFFERENT criteria if pass_1's winner
    is criterion A and pass_2's winner (via tie-break) is criterion B —
    the exact BUG-176 disease, reachable one layer higher than the
    within-pass fix alone reaches. `claimed` is threaded in from
    `vote_batch`, spanning the whole batch across every pass and any
    tie-break, not just one grading call. A verdict whose quotes are
    ENTIRELY already claimed is downgraded to no-verdict (never promote
    unverified/borrowed evidence to a decided pass/fail, charter rule 1);
    a verdict with SOME surviving quotes keeps its verdict with only the
    unclaimed ones."""
    if verdict.verdict is None or not verdict.quotes or not verdict.anchors:
        return verdict
    kept_quotes, kept_anchors = _drop_claimed_quotes(verdict.quotes, verdict.anchors, claimed)
    if kept_quotes:
        if len(kept_quotes) == len(verdict.quotes):
            return verdict
        return GradedVerdict(
            verdict.criterion_id,
            verdict.verdict,
            verdict.reasoning,
            kept_quotes,
            kept_anchors,
            None,
        )
    return GradedVerdict(
        verdict.criterion_id,
        None,
        verdict.reasoning,
        verdict.quotes,
        None,
        "Every quoted piece of evidence for this criterion was already used "
        "for a different criterion in this batch.",
    )


async def grade_batch_verdicts(
    batch: SemanticBatch,
    batch_criteria: list[Any],
    llm: LLMClient,
    check_run_id: int | None,
    anchor_kind: str,
    *,
    consistency_pass: str = "single",
    prompt_version: str = PROMPT_VERSION,
    annotator_stance: str = "",
) -> dict[int, GradedVerdict]:
    """Grades one batch ONCE (whole-batch retry + per-criterion retry ladder
    already in place for hallucinated/missing verdicts) and returns the
    verdict per criterion id WITHOUT persisting anything — the reusable
    core both `run_semantic_checks` (single-pass, V2) and
    `app.checks.consistency` (N-pass voting, V-022) build on, per this
    module's original design note."""
    prompt = _build_prompt(
        batch, batch_criteria, prompt_version=prompt_version, annotator_stance=annotator_stance
    )
    settings = get_settings()
    parsed: GradeBatchCandidates | None = None
    for _attempt in range(settings.semantic_batch_validation_attempts):
        try:
            parsed = await _call_grade(
                prompt,
                llm,
                check_run_id,
                expected_count=len(batch_criteria),
                consistency_pass=consistency_pass,
                prompt_version=prompt_version,
                validation_attempt=_attempt,
            )
            break
        except ApiDownError:
            if consistency_pass != "pass_2":
                # No proposal exists yet; retain the owned pipeline outage floor.
                raise
            return {
                c.id: GradedVerdict(
                    c.id,
                    None,
                    None,
                    None,
                    None,
                    "The grading service was unavailable during this pass. No rating was assigned.",
                    ResultOutcome.api_down,
                    f"{consistency_pass}:batch",
                )
                for c in batch_criteria
            }
        except SemanticGradeError:
            continue

    if parsed is None:
        reason = "Grading response could not be validated after a retry."
        return {c.id: GradedVerdict(c.id, None, None, None, None, reason) for c in batch_criteria}

    by_index = {v.index: v for v in parsed.verdicts}
    out: dict[int, GradedVerdict] = {}
    # BUG-176/BUG-155: quotes already attached to an earlier criterion in
    # THIS batch are never valid evidence for a later one -- tracked
    # across the whole loop, not reset per-criterion.
    claimed_quotes: set[str] = set()
    recovered_criteria = 0
    recovery_unavailable = False
    for i, criterion in enumerate(batch_criteria):
        verdict = by_index.get(i)
        if verdict is not None and verdict.verdict == "uncertain":
            out[criterion.id] = GradedVerdict(
                criterion.id,
                None,
                verdict.reasoning,
                verdict.evidence_quotes,
                None,
                verdict.reasoning,
            )
            continue
        quotes = verdict.evidence_quotes if verdict else None
        anchors = (
            _verify_quotes(quotes, batch.blocks, anchor_kind, scopes=batch.source_scopes)
            if quotes
            else None
        )
        if anchors is not None:
            quotes, anchors = _drop_claimed_quotes(quotes, anchors, claimed_quotes)
            if not quotes:
                anchors = None  # every quote was borrowed -- same as hallucinated
        if verdict is None or anchors is None:
            if recovered_criteria >= settings.semantic_item_recovery_max_criteria:
                out[criterion.id] = GradedVerdict(
                    criterion.id,
                    None,
                    verdict.reasoning if verdict else None,
                    verdict.evidence_quotes if verdict else None,
                    None,
                    "This criterion remains unassessed because the configured item-recovery "
                    "budget was reached. Review its source before grading.",
                )
                continue
            recovered_criteria += 1
            request_attempted = not recovery_unavailable
            try:
                if recovery_unavailable:
                    raise ApiDownError("Item recovery is unavailable for this pass")
                retry = await _grade_single_criterion(
                    criterion,
                    batch,
                    llm,
                    check_run_id,
                    anchor_kind,
                    consistency_pass=consistency_pass,
                    prompt_version=prompt_version,
                    annotator_stance=annotator_stance,
                    recovery_phase="criterion_repair",
                )
            except ApiDownError:
                recovery_unavailable = True
                out[criterion.id] = GradedVerdict(
                    criterion.id,
                    None,
                    verdict.reasoning if verdict else None,
                    verdict.evidence_quotes if verdict else None,
                    None,
                    "The grading service was unavailable for this criterion's evidence repair. "
                    "No rating was assigned.",
                    ResultOutcome.api_down,
                    f"{consistency_pass}:criterion_repair",
                    request_attempted,
                )
                continue
            if retry is None:
                if verdict is not None:
                    # The ORIGINAL batch verdict had quotes that failed
                    # containment, and the retry produced nothing at all
                    # (dropped the criterion, or errored) -- fall back to
                    # the batch verdict's own quotes/reasoning as
                    # unverified evidence, same principle as the
                    # retry_anchors-is-None branch below (backend review
                    # finding, V-068: this branch was still discarding
                    # real model output, reproducing the ticket's own bug
                    # through an adjacent path the first pass missed).
                    out[criterion.id] = GradedVerdict(
                        criterion.id,
                        None,
                        verdict.reasoning,
                        quotes if quotes else verdict.evidence_quotes,
                        None,
                        "Could not verify the quoted evidence after a retry.",
                    )
                else:
                    out[criterion.id] = GradedVerdict(
                        criterion.id,
                        None,
                        None,
                        None,
                        None,
                        "This criterion's grading response could not be validated after its "
                        "item retry."
                        if i in parsed.invalid_indices
                        else "No verdict was returned for this criterion, even after a retry.",
                    )
                continue
            retry_verdict, retry_anchors = retry
            if retry_verdict.verdict == "uncertain":
                out[criterion.id] = GradedVerdict(
                    criterion.id,
                    None,
                    retry_verdict.reasoning,
                    retry_verdict.evidence_quotes,
                    None,
                    retry_verdict.reasoning,
                )
                continue
            if retry_anchors is not None:
                retry_quotes, retry_anchors = _drop_claimed_quotes(
                    retry_verdict.evidence_quotes, retry_anchors, claimed_quotes
                )
                if not retry_quotes:
                    retry_anchors = None
            if retry_anchors is None:
                # The retry reached a verdict but its quotes ALSO failed
                # containment (or were themselves already claimed by a
                # sibling criterion) — never promote unverified quotes to
                # a decided pass/fail (charter rule 1), but the raw
                # quotes/reasoning are real model output, worth carrying
                # to the panel as "could not verify" rather than dropping
                # (V-068 Q1/Q2).
                out[criterion.id] = GradedVerdict(
                    criterion.id,
                    None,
                    retry_verdict.reasoning,
                    retry_verdict.evidence_quotes,
                    None,
                    "Could not verify the quoted evidence after a retry.",
                )
                continue
            verdict, quotes, anchors = retry_verdict, retry_quotes, retry_anchors
        claimed_quotes.update(_normalize(q) for q in quotes)
        out[criterion.id] = GradedVerdict(
            criterion.id, verdict.verdict, verdict.reasoning, quotes, anchors, None
        )
    return out


async def record_ungraded(
    session: AsyncSession,
    check_run_id: int,
    criteria: list[Any],
    *,
    outcome: ResultOutcome,
    reason: str,
    commit: bool = True,
    cause: str | None = None,
) -> list[CheckResult]:
    """Record criteria the AI never got to grade, as an honest STATE rather
    than a verdict (V-050, availability floor).

    Used when the day's AI budget is spent or the API is unreachable: the
    run finishes and hands these to the instructor instead of stalling until
    the quota resets, which could be after the defense. `outcome` stays
    `quota_exhausted`/`api_down` — never `escalated`, which would claim the
    AI looked and was unsure — and the scoring engine already excludes both
    from the composite rather than counting them as passes (F8.1).

    With `commit=False`, records remain pending for the caller's owned stage
    transaction. The default retains the existing per-result publication.
    """
    return [
        await _persist(
            session,
            check_run_id,
            criterion,
            outcome,
            {
                "basis": "not-graded",
                "reason": reason,
                **({"ungraded_cause": cause} if cause is not None else {}),
            },
            commit=commit,
        )
        for criterion in criteria
    ]


async def _persist(
    session: AsyncSession,
    check_run_id: int,
    criterion: Any,
    outcome: ResultOutcome,
    detail: dict[str, Any],
    *,
    commit: bool = True,
) -> CheckResult:
    result = CheckResult(
        check_run_id=check_run_id,
        criterion_id=criterion.id,
        kind=CheckKind.semantic,
        outcome=outcome,
        score=detail.get("score"),
        detail={k: v for k, v in detail.items() if k != "score"},
    )
    session.add(result)
    if commit:
        await session.commit()
    return result


async def _grade_batch(
    session: AsyncSession,
    check_run_id: int,
    batch: SemanticBatch,
    batch_criteria: list[Any],
    llm: LLMClient,
    settings: Settings,
    anchor_kind: str,
) -> list[CheckResult]:
    graded = await grade_batch_verdicts(
        batch, batch_criteria, llm, check_run_id, anchor_kind, consistency_pass="single"
    )
    shadow_detail = shadow_signals_as_detail(compute_shadow_signals(batch.context_text, settings))
    candidates = []
    for criterion in batch_criteria:
        g = graded[criterion.id]
        if (
            g.verdict is not None
            and outcome_and_score(criterion, g.verdict)[0] != ResultOutcome.escalated
        ):
            candidates.append(
                (
                    criterion,
                    {
                        "verdict": g.verdict,
                        "evidence": [
                            {"quote": q, "anchor": a}
                            for q, a in zip(g.quotes or [], g.anchors or [], strict=True)
                        ],
                    },
                )
            )
    support = await check_support_available(batch, candidates, llm, check_run_id=check_run_id)
    results: list[CheckResult] = []
    for criterion in batch_criteria:
        g = graded[criterion.id]
        if g.verdict is None:
            escalated_detail: dict[str, Any] = {
                "source_coverage": batch.source_coverage,
                "basis": "llm",
                "reason": g.escalation_reason,
                "prompt_version": PROMPT_VERSION,
            }
            if g.failure_phase:
                escalated_detail["failure_phase"] = g.failure_phase
                escalated_detail["request_attempted"] = g.request_attempted
            if g.reasoning:
                escalated_detail["proposed_reasoning"] = g.reasoning
            if g.quotes:
                escalated_detail["unverified_evidence"] = g.quotes
            results.append(
                await _persist(
                    session,
                    check_run_id,
                    criterion,
                    g.failure_outcome or ResultOutcome.escalated,
                    escalated_detail,
                )
            )
            continue
        outcome, score, level = outcome_and_score(criterion, g.verdict)
        if outcome == ResultOutcome.escalated:
            # V-069: the model returned a verdict string that doesn't name
            # any of THIS criterion's own levels (and isn't pass/partial/
            # fail either) -- never guessed onto the nearest-looking rung
            # (charter rule 1). Same shape as the `g.verdict is None`
            # branch above, distinct reason text.
            results.append(
                await _persist(
                    session,
                    check_run_id,
                    criterion,
                    ResultOutcome.escalated,
                    {
                        "source_coverage": batch.source_coverage,
                        "basis": "llm",
                        "reason": (
                            f"The grading response used an unrecognized verdict "
                            f"({g.verdict!r}) for this criterion's own scale."
                        ),
                        "prompt_version": PROMPT_VERSION,
                    },
                )
            )
            continue
        detail: dict[str, Any] = {
            "source_coverage": batch.source_coverage,
            "score": score,
            "basis": "llm",
            "verdict": g.verdict,
            "reasoning": g.reasoning,
            "evidence": [
                {"quote": q, "anchor": a} for q, a in zip(g.quotes, g.anchors, strict=True)
            ],
            "context_label": batch.label,
            "prompt_version": PROMPT_VERSION,
            "shadow": shadow_detail,
        }
        if level is not None:
            detail["level"] = level.as_detail()
        outcome, detail = apply_support(outcome, detail, support[criterion.id])
        results.append(await _persist(session, check_run_id, criterion, outcome, detail))
    return results


async def run_semantic_checks(
    session: AsyncSession,
    check_run_id: int,
    criteria: list[Any],
    extraction: ExtractionResult,
    llm: LLMClient,
    settings: Settings | None = None,
) -> list[CheckResult]:
    """Entry point V-018's orchestrator calls with every `structural`-
    ineligible-turned-semantic and every genuinely `semantic` criterion in
    one check_run."""
    settings = settings or get_settings()
    if not criteria:
        return []
    limitation = source_limitation_detail(extraction)
    if limitation is not None:
        return [
            await _persist(
                session,
                check_run_id,
                criterion,
                ResultOutcome.escalated,
                {"basis": "source-quality", **limitation},
            )
            for criterion in criteria
        ]
    batches, missing = build_semantic_batches(criteria, extraction, settings=settings)
    # BUG-221 (charter rule 1): this used to grade `failed` immediately --
    # a title-match miss is not proof the section is genuinely absent, only
    # that the lookup couldn't find it (the same false-negative class
    # BUG-048 already fixed once, for references specifically; nothing
    # here rules it out for an arbitrary "chapter N" target). Stating
    # "Does not meet" for a section the check never actually read is
    # exactly the dishonest-verdict risk ground rule 3 exists to prevent.
    # Escalated instead, same as `required_section_present`'s identical
    # situation -- a human decides, nothing is auto-failed on a lookup miss.
    results = [
        await _persist(
            session,
            check_run_id,
            criterion,
            ResultOutcome.escalated,
            {
                "basis": "structural-alignment",
                "reason": (
                    f"No section matching '{target}' was found in the manuscript's parsed "
                    "structure. This may mean the section is genuinely missing, or that it "
                    "exists under wording VERIDICAL doesn't yet recognize -- check the "
                    "manuscript directly before deciding."
                ),
            },
        )
        for criterion, target in missing
    ]
    for batch, batch_criteria in batches:
        limitation = context_limitation(batch, settings)
        if limitation is not None:
            results.extend(
                [
                    await _persist(session, check_run_id, c, ResultOutcome.escalated, limitation)
                    for c in batch_criteria
                ]
            )
            continue
        results.extend(
            await _grade_batch(
                session, check_run_id, batch, batch_criteria, llm, settings, extraction.anchor_kind
            )
        )
    return results
