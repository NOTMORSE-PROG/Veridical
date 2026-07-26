"""Semantic grading pipeline (F3.3): batches semantic criteria by the
manuscript section they actually reference (never the whole manuscript
per criterion — D-001 quota discipline), calls Gemini once per batch
through the queue (V-009), and REJECTS any verdict whose quoted evidence
doesn't actually appear in the source text — retrying once at the
single-criterion level before escalating to the instructor rather than
ever showing unverifiable evidence (charter rule 1).

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

from app.checks.rules.sections import find_section_node, identify_target_section, walk_sections
from app.checks.schemas import GradeBatchResponse, GradeVerdict
from app.checks.signals import compute_shadow_signals, shadow_signals_as_detail
from app.config import Settings, get_settings
from app.errors import VeridicalError
from app.ingest.schemas import ExtractionResult, SectionNode, TextBlock
from app.llm.base import LLMClient
from app.models.enums import CheckKind, ResultOutcome
from app.models.run import CheckResult

PROMPT_TYPE = "semantic_grading"
PROMPT_VERSION = "v1"
_PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_TYPE}_{PROMPT_VERSION}.txt"
_WHOLE_DOCUMENT_KEY = "__whole_document__"

_WHITESPACE = re.compile(r"\s+")

_SCORE_BY_VERDICT = {"pass": 100.0, "partial": 50.0, "fail": 0.0}
_OUTCOME_BY_VERDICT = {
    "pass": ResultOutcome.passed,
    "partial": ResultOutcome.passed,
    "fail": ResultOutcome.failed,
}


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

    @property
    def context_text(self) -> str:
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
    criteria: list[Any], extraction: ExtractionResult
) -> tuple[list[tuple[SemanticBatch, list[Any]]], list[tuple[Any, str]]]:
    """Groups semantic criteria by the section they name (or "whole
    document" when none is identifiable) so one Gemini call can grade
    every criterion that shares the same context. Returns
    `(batches, missing)`: `missing` is (criterion, target) pairs whose
    named section genuinely does not exist in this manuscript — those are
    graded `failed` immediately by the caller, without spending a call,
    aligned with `required_section_present`'s identical situation.
    """
    flat_nodes = list(walk_sections(extraction.section_tree))
    groups: dict[str, list[Any]] = {}
    group_nodes: dict[str, SectionNode] = {}
    missing: list[tuple[Any, str]] = []

    for criterion in criteria:
        target = identify_target_section(criterion)
        if target is None:
            key = _WHOLE_DOCUMENT_KEY
        else:
            node = find_section_node(target, extraction.section_tree)
            if node is None:
                missing.append((criterion, target))
                continue
            key = node.title
            group_nodes[key] = node
        groups.setdefault(key, []).append(criterion)

    batches: list[tuple[SemanticBatch, list[Any]]] = []
    for key, batch_criteria in groups.items():
        if key == _WHOLE_DOCUMENT_KEY:
            batch_blocks = [b for b in extraction.blocks if not b.is_furniture]
            label = "whole document"
        else:
            node = group_nodes[key]
            idx = next(i for i, n in enumerate(flat_nodes) if n is node)
            # The span must run to the next SIBLING-OR-HIGHER heading, not
            # simply the next node in depth-first order — `walk_sections`
            # yields a node's own children immediately after it, so
            # `flat_nodes[idx + 1]` for a chapter is its own first
            # subsection (same page), which zeroed the span entirely (real
            # bug, found live grading the owner's real proposal PDF: a
            # chapter's whole context came back empty). A chapter's
            # context must include everything under it.
            next_node = next((n for n in flat_nodes[idx + 1 :] if n.level <= node.level), None)
            batch_blocks = _section_span_blocks(
                node, next_node, extraction.blocks, extraction.anchor_kind
            )
            label = node.title
        batches.append((SemanticBatch(label=label, blocks=batch_blocks), batch_criteria))
    return batches, missing


def _criteria_listing(criteria: list[Any]) -> str:
    lines = []
    for i, criterion in enumerate(criteria):
        line = f"{i}. {criterion.text}"
        if getattr(criterion, "evidence", None):
            line += f" (Evidence needed: {criterion.evidence})"
        lines.append(line)
    return "\n".join(lines)


def _build_prompt(batch: SemanticBatch, criteria: list[Any]) -> str:
    template = _PROMPT_FILE.read_text(encoding="utf-8")
    return (
        template.replace("{criteria_list}", _criteria_listing(criteria))
        .replace("{context_label}", batch.label)
        .replace("{context_text}", batch.context_text)
    )


def _normalized_layout(
    blocks: list[TextBlock],
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
        norm = _normalize(block.text)
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


def _verify_quotes(
    quotes: list[str], blocks: list[TextBlock], anchor_kind: str
) -> list[str] | None:
    """None means at least one quote is hallucinated (not found verbatim,
    contiguously, in the joined source) — the whole verdict is rejected
    (charter rule 1: never show unverifiable evidence), not just the bad
    quote."""
    full_text, spans = _normalized_layout(blocks)
    anchors = []
    for quote in quotes:
        normalized_quote = _normalize(quote)
        if not normalized_quote:
            return None
        pos = full_text.find(normalized_quote)
        if pos == -1:
            return None
        anchor = _anchor_at_position(pos, spans, anchor_kind)
        if anchor is None:
            return None
        anchors.append(anchor)
    return anchors


async def _call_grade(
    prompt: str, llm: LLMClient, check_run_id: int | None, *, consistency_pass: str = "single"
) -> GradeBatchResponse:
    # `consistency_pass` rides in **context: it differentiates the response-
    # cache key (D-011) so two independent voting passes over the SAME
    # batch/criteria are two real samples, not a cache hit of each other,
    # while a genuine re-run (Flow E, same pass) still hits cache for free.
    # The fake client (V-022) uses it to select a per-pass fixture so tests
    # can script a deterministic disagreement.
    response = await llm.complete(
        PROMPT_TYPE,
        prompt,
        prompt_version=PROMPT_VERSION,
        check_run_id=check_run_id,
        consistency_pass=consistency_pass,
    )
    try:
        return GradeBatchResponse.model_validate(response)
    except ValidationError as exc:
        raise SemanticGradeError(f"Semantic grading response failed validation: {exc}") from exc


async def _grade_single_criterion(
    criterion: Any,
    batch: SemanticBatch,
    llm: LLMClient,
    check_run_id: int | None,
    anchor_kind: str,
    *,
    consistency_pass: str = "single",
) -> tuple[GradeVerdict, list[str]] | None:
    """One-criterion retry, same context — used both when the batch
    response omitted a criterion and when its quotes failed containment,
    and (V-022) as the single-criterion tie-break call on a voting split."""
    prompt = _build_prompt(batch, [criterion])
    try:
        parsed = await _call_grade(prompt, llm, check_run_id, consistency_pass=consistency_pass)
    except SemanticGradeError:
        return None
    verdict = next((v for v in parsed.verdicts if v.index == 0), None)
    if verdict is None:
        return None
    anchors = _verify_quotes(verdict.evidence_quotes, batch.blocks, anchor_kind)
    if anchors is None:
        return None
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


async def grade_batch_verdicts(
    batch: SemanticBatch,
    batch_criteria: list[Any],
    llm: LLMClient,
    check_run_id: int | None,
    anchor_kind: str,
    *,
    consistency_pass: str = "single",
) -> dict[int, GradedVerdict]:
    """Grades one batch ONCE (whole-batch retry + per-criterion retry ladder
    already in place for hallucinated/missing verdicts) and returns the
    verdict per criterion id WITHOUT persisting anything — the reusable
    core both `run_semantic_checks` (single-pass, V2) and
    `app.checks.consistency` (N-pass voting, V-022) build on, per this
    module's original design note."""
    prompt = _build_prompt(batch, batch_criteria)
    parsed: GradeBatchResponse | None = None
    for _attempt in range(2):  # one try, one whole-batch retry (ticket: "retry once")
        try:
            parsed = await _call_grade(prompt, llm, check_run_id, consistency_pass=consistency_pass)
            break
        except SemanticGradeError:
            continue

    if parsed is None:
        reason = "Grading response could not be validated after a retry."
        return {c.id: GradedVerdict(c.id, None, None, None, None, reason) for c in batch_criteria}

    by_index = {v.index: v for v in parsed.verdicts}
    out: dict[int, GradedVerdict] = {}
    for i, criterion in enumerate(batch_criteria):
        verdict = by_index.get(i)
        anchors = (
            _verify_quotes(verdict.evidence_quotes, batch.blocks, anchor_kind) if verdict else None
        )
        if verdict is None or anchors is None:
            retry = await _grade_single_criterion(
                criterion, batch, llm, check_run_id, anchor_kind, consistency_pass=consistency_pass
            )
            if retry is None:
                reason = (
                    "Could not verify the quoted evidence after a retry."
                    if verdict is not None
                    else "No verdict was returned for this criterion, even after a retry."
                )
                out[criterion.id] = GradedVerdict(criterion.id, None, None, None, None, reason)
                continue
            verdict, anchors = retry
        out[criterion.id] = GradedVerdict(
            criterion.id, verdict.verdict, verdict.reasoning, verdict.evidence_quotes, anchors, None
        )
    return out


async def _persist(
    session: AsyncSession,
    check_run_id: int,
    criterion: Any,
    outcome: ResultOutcome,
    detail: dict[str, Any],
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
    results: list[CheckResult] = []
    for criterion in batch_criteria:
        g = graded[criterion.id]
        if g.verdict is None:
            results.append(
                await _persist(
                    session,
                    check_run_id,
                    criterion,
                    ResultOutcome.escalated,
                    {
                        "basis": "llm",
                        "reason": g.escalation_reason,
                        "prompt_version": PROMPT_VERSION,
                    },
                )
            )
            continue
        results.append(
            await _persist(
                session,
                check_run_id,
                criterion,
                _OUTCOME_BY_VERDICT[g.verdict],
                {
                    "score": _SCORE_BY_VERDICT[g.verdict],
                    "basis": "llm",
                    "verdict": g.verdict,
                    "reasoning": g.reasoning,
                    "evidence": [
                        {"quote": q, "anchor": a} for q, a in zip(g.quotes, g.anchors, strict=True)
                    ],
                    "context_label": batch.label,
                    "prompt_version": PROMPT_VERSION,
                    "shadow": shadow_detail,
                },
            )
        )
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
            await _grade_batch(
                session, check_run_id, batch, batch_criteria, llm, settings, extraction.anchor_kind
            )
        )
    return results
