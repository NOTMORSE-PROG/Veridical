"""Validation gate + retry loop (F2.2, proposal Fig 3.7).

`decompose.py` makes one Gemini call and validates the response SHAPE
(schema-valid, ≥1 criterion). This module adds a second, content-level
gate (coverage of the source text, no duplicate criteria), retries with
the failure fed back into the prompt, and — after exhausting retries —
NEVER fails the upload: it returns a `needs_review` outcome (possibly a
partial criteria set) for the review screen (V-012), per charter rule 1
(escalate low-confidence results to the instructor, don't guess).
"""

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.ingest.schemas import TextBlock
from app.llm.base import LLMClient
from app.models.audit import AuditLog
from app.rubric.decompose import RubricParseError, decompose_rubric
from app.rubric.schemas import ParsedCriterion

# Significant words only (skip 1-3 letter function words: "the", "and",
# "for", ...) — a coarse but dependency-free overlap signal; no NLP model
# needed for a coverage HEURISTIC (the real judgment stays with the LLM
# and, on persistent failure, the instructor).
_WORD_RE = re.compile(r"[a-z]{4,}")


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _duplicate_texts(criteria: list[ParsedCriterion]) -> list[str]:
    counts: dict[str, int] = {}
    for c in criteria:
        key = " ".join(sorted(_words(c.text)))
        if key:
            counts[key] = counts.get(key, 0) + 1
    return [key for key, count in counts.items() if count > 1]


def coverage_ratio(
    blocks: list[TextBlock],
    criteria: list[ParsedCriterion],
    *,
    min_line_chars: int,
    word_overlap_ratio: float,
) -> float:
    """Fraction of substantive source lines that show up (by word overlap)
    in SOME criterion's text/evidence — catches an answer that quietly
    ignores half the rubric (ticket edge case). 1.0 when there's no
    substantive text to lose (e.g. a near-empty rubric)."""
    lines = [
        b.text.strip()
        for b in blocks
        if not b.is_furniture and len(b.text.strip()) >= min_line_chars
    ]
    if not lines:
        return 1.0
    haystack: set[str] = set()
    for c in criteria:
        haystack |= _words(c.text)
        if c.evidence_needed:
            haystack |= _words(c.evidence_needed)
    covered = 0
    for line in lines:
        line_words = _words(line)
        if not line_words or len(line_words & haystack) / len(line_words) >= word_overlap_ratio:
            covered += 1
    return covered / len(lines)


@dataclass
class GateResult:
    ok: bool
    coverage_ratio: float
    reasons: list[str]


def run_gate(
    blocks: list[TextBlock], criteria: list[ParsedCriterion], settings: Settings
) -> GateResult:
    reasons: list[str] = []
    dupes = _duplicate_texts(criteria)
    if dupes:
        reasons.append(f"{len(dupes)} duplicate criterion text(s)")
    ratio = coverage_ratio(
        blocks,
        criteria,
        min_line_chars=settings.rubric_coverage_min_line_chars,
        word_overlap_ratio=settings.rubric_coverage_word_overlap_ratio,
    )
    if ratio < settings.rubric_coverage_min_ratio:
        reasons.append(
            f"only {ratio:.0%} of the source text is reflected in the parsed criteria "
            f"(need >= {settings.rubric_coverage_min_ratio:.0%})"
        )
    return GateResult(ok=not reasons, coverage_ratio=ratio, reasons=reasons)


@dataclass
class DecompositionOutcome:
    criteria: list[ParsedCriterion]
    needs_review: bool
    issues: list[str]
    attempts: int


async def attempt_decomposition(
    session: AsyncSession,
    rubric_id: int,
    blocks: list[TextBlock],
    raw_text: str,
    llm: LLMClient,
    settings: Settings,
) -> DecompositionOutcome:
    """Up to `rubric_parse_max_retries + 1` total attempts. Only a
    validation failure (`RubricParseError`, or the gate) triggers a
    retry — quota/API-down errors are infra failures, not content
    problems, and propagate immediately rather than burning retries
    against the daily meter for nothing (ticket edge case)."""
    total_attempts = settings.rubric_parse_max_retries + 1
    feedback: str | None = None
    last_criteria: list[ParsedCriterion] = []
    last_issues: list[str] = ["no attempt produced a schema-valid response"]

    for attempt in range(1, total_attempts + 1):
        try:
            criteria = await decompose_rubric(raw_text, llm, feedback=feedback)
        except RubricParseError as exc:
            last_issues = [str(exc)]
            await _log_attempt(
                session, rubric_id, attempt, ok=False, issues=last_issues, coverage=None
            )
            feedback = last_issues[0]
            continue

        gate = run_gate(blocks, criteria, settings)
        await _log_attempt(
            session,
            rubric_id,
            attempt,
            ok=gate.ok,
            issues=gate.reasons,
            coverage=gate.coverage_ratio,
        )
        if gate.ok:
            return DecompositionOutcome(
                criteria=criteria, needs_review=False, issues=[], attempts=attempt
            )
        last_criteria = criteria  # best-so-far partial — never discarded
        last_issues = gate.reasons
        feedback = "; ".join(gate.reasons)

    return DecompositionOutcome(
        criteria=last_criteria, needs_review=True, issues=last_issues, attempts=total_attempts
    )


async def _log_attempt(
    session: AsyncSession,
    rubric_id: int,
    attempt: int,
    *,
    ok: bool,
    issues: list[str],
    coverage: float | None,
) -> None:
    """rubric_id lives in the payload, not a typed column — audit_log's
    typed FK-shaped column (check_run_id) means something else, and this
    event predates any check_run."""
    session.add(
        AuditLog(
            event_type="rubric_parse_attempt",
            payload={
                "rubric_id": rubric_id,
                "attempt": attempt,
                "ok": ok,
                "issues": issues,
                "coverage_ratio": coverage,
            },
        )
    )
    await session.flush()
