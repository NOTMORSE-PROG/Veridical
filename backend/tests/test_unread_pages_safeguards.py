"""Safeguards around partly read manuscripts and automatic grading: text that
was not read never becomes a finding, and a claimed weakness or evidence that
cannot show the criterion still waits for the instructor."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.checks.forensics.service import _compute_forensics_findings
from app.checks.semantic import SemanticBatch
from app.checks.source_quality import unread_page_scope, without_unread_pages
from app.checks.support import adverse_rating, apply_support, check_support
from app.ingest.schemas import ExtractionResult, PageReadingOrder, SectionTree, TextBlock
from app.models.enums import CheckKind, ResultOutcome
from app.pipeline import machine
from app.report.service import _automatic_grade
from tests.test_checks_consistency import TIP_SCALE

PAGES = {
    1: "Chapter 3 describes the usability evaluation with twenty-nine clinic staff.",
    2: "All hypotheses were directional and were tested with one-tailed t-tests.",
    3: "Task time improved after training, t(28) = 1.80, p = .041.",
    4: "The appendix lists the questionnaire items.",
}


def _source(unresolved=()):
    blocks = [
        TextBlock(
            page=page,
            text=text,
            bbox=(50, 100, 500, 120),
            max_font_size=11,
            bold_ratio=0.0,
            source_line_ids=[f"{page}:0:0"],
        )
        for page, text in PAGES.items()
    ]
    return ExtractionResult(
        page_count=len(PAGES),
        anchor_kind="page",
        image_only=False,
        text_chars=sum(len(text) for text in PAGES.values()),
        blocks=blocks,
        images=[],
        section_tree=SectionTree(source="none", nodes=[]),
        reading_order=[
            PageReadingOrder(
                page=page,
                status="unresolved" if page in unresolved else "ordered",
                reason="native_table_grid_invalid" if page in unresolved else None,
                source_inventory_sha256="a" * 64,
                source_line_count=1,
                ordered_source_line_ids=[f"{page}:0:0"],
            )
            for page in PAGES
        ],
    )


def _p_value_flags(extraction):
    findings = _compute_forensics_findings(extraction)
    return [draft for draft in findings.flag_drafts if "p_value" in draft.detail["kind"]]


def test_one_tailed_declaration_on_an_unread_page_is_not_a_decision_error():
    assert _p_value_flags(_source()) == []
    partial = _source(unresolved=(2,))
    readable = without_unread_pages(partial, unread_page_scope(partial, 0.5))
    assert _p_value_flags(readable) == []


LEVELLED = SimpleNamespace(id=1, text="Introduction", evidence=None, levels=TIP_SCALE)
UNSUPPORTED = {"status": "unsupported", "reason": "The quote does not show the claimed weakness."}


def _detail(level_name, ordinal):
    return {"verdict": level_name, "level": {"name": level_name, "ordinal": ordinal}}


def test_the_lowest_level_and_a_fail_are_adverse():
    assert adverse_rating(LEVELLED, ResultOutcome.passed, _detail("Beginner", 1))
    assert not adverse_rating(LEVELLED, ResultOutcome.passed, _detail("Acceptable", 2))
    plain = SimpleNamespace(id=2, text="States objectives", levels=None)
    assert adverse_rating(plain, ResultOutcome.failed, {"verdict": "fail"})
    assert not adverse_rating(plain, ResultOutcome.passed, {"verdict": "pass"})


@pytest.mark.parametrize(
    ("level", "ordinal", "kept"), [("Beginner", 1, False), ("Proficient", 3, True)]
)
def test_deciding_keeps_only_non_adverse_grades_without_support(level, ordinal, kept):
    detail = _detail(level, ordinal)
    adverse = adverse_rating(LEVELLED, ResultOutcome.passed, detail)
    outcome, updated = apply_support(
        ResultOutcome.passed, detail, UNSUPPORTED, decide=True, adverse=adverse
    )
    assert (outcome == ResultOutcome.passed) is kept
    assert ("support_caution" in updated) is kept


def test_evidence_that_cannot_show_the_criterion_is_never_a_kept_grade():
    support = {
        "status": "unsupported",
        "reason": "The submitted evidence consists only of a source identifier.",
        "caution": "The quoted evidence only identifies a source.",
        "scope_guard": "required_body_evidence_unconfirmed",
    }
    outcome, updated = apply_support(
        ResultOutcome.passed, _detail("Proficient", 3), support, decide=True
    )
    assert outcome == ResultOutcome.escalated and updated["support_withheld"]


async def test_the_evidence_review_is_told_which_pages_were_not_read():
    seen = {}

    class Reviewer:
        async def complete(self, prompt_type, prompt, **context):
            seen["prompt"] = prompt
            return {"items": []}

    batch = SemanticBatch(
        "whole document",
        [],
        source_coverage={"native_math_inventory_state": "not_applicable", "unread_pages": [2]},
    )
    detail = {"verdict": "Beginner", "evidence": [{"quote": "x", "anchor": "page 1"}]}
    await check_support(batch, [(LEVELLED, detail)], Reviewer(), check_run_id=None)
    assert '"source_unread_pages": [2]' in seen["prompt"]


def test_agreeing_passes_compare_level_names_like_the_vote():
    result = SimpleNamespace(outcome=ResultOutcome.passed)
    detail = {
        "basis": "llm",
        "verdict": "Proficient",
        "votes": ["Proficient", "Profi cient"],
    }
    assert _automatic_grade(result, detail, None)["agreeing_passes"] == 2


async def test_a_resumed_stage_finishes_a_missing_unread_pages_note(monkeypatch):
    monkeypatch.setattr(machine, "write_audit_event", AsyncMock())
    saved = SimpleNamespace(
        kind=CheckKind.statistical_forensics, outcome=ResultOutcome.passed, detail={"n_flags": 0}
    )
    closed = SimpleNamespace(
        kind=CheckKind.internal_agreement,
        outcome=ResultOutcome.escalated,
        detail={"coverage_reason": "source_reading_order_unresolved"},
    )
    assert machine._may_lack_unread_note(saved)
    assert not machine._may_lack_unread_note(closed)
    assert not machine._may_lack_unread_note(None)
    await machine._record_unread_pages(SimpleNamespace(commit=AsyncMock()), 1, saved, [2])
    assert saved.outcome == ResultOutcome.escalated
    assert not machine._may_lack_unread_note(saved)
