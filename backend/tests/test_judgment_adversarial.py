"""Adversarial challenge tests: Judgment Concurrency & Snapshot Edge Cases.

Empirically verifies:
1. Concurrency and revision conflicts (expected_revision, expected_judgment_revision,
   stale epochs, idempotent reject).
2. Strict validation of revision justification length (< 10 chars rejected,
   whitespace trimming, max length).
3. Snapshot immutability, timezone enforcement, non-finite scores, and provenance rules.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.checks.escalation import resolve_escalation
from app.errors import ConflictError
from app.models.enums import CheckKind, CheckRunStatus, CriterionType, ResultOutcome
from app.models.rubric import Criterion
from app.models.run import CheckResult, CheckRun, ReadinessReport
from app.report.assessment import assessment_write
from app.report.judgment_schemas import (
    JudgmentRecordIn,
    JudgmentRevisionIn,
    JudgmentSnapshot,
)
from app.report.judgment_snapshot import (
    judgment_grade_identity,
    manual_grade_consistent,
)
from app.report.judgments import (
    get_judgment_history,
    revise_judgment,
)

# ==============================================================================
# 1. REVISION JUSTIFICATION VALIDATION (< 10 CHARS REJECTED)
# ==============================================================================


@pytest.mark.parametrize(
    "invalid_reason",
    [
        "",  # 0 chars
        "a",  # 1 char
        "ab",  # 2 chars
        "12345",  # 5 chars
        "123456789",  # 9 chars
        "   ",  # whitespace only
        "\t\n\r  ",  # tab/newline whitespace
        "  123456789  ",  # 9 chars with surrounding whitespace (trimmed)
        " \t 123456789 \n ",  # 9 chars with mixed whitespace
    ],
)
def test_judgment_record_in_rejects_reasons_under_10_characters(invalid_reason):
    """Empirically verify that JudgmentRecordIn strictly rejects reasons < 10 chars."""
    with pytest.raises(ValidationError) as exc_info:
        JudgmentRecordIn.model_validate(
            {
                "expected_revision": 0,
                "resolution": "mark_pass",
                "reason": invalid_reason,
            }
        )
    errors = exc_info.value.errors()
    assert any("at least 10 characters" in str(e["msg"]) for e in errors)


@pytest.mark.parametrize(
    "invalid_reason",
    [
        "",
        "short",
        "123456789",
        "  123456789  ",
        " \t\n ",
    ],
)
def test_judgment_revision_in_rejects_reasons_under_10_characters(invalid_reason):
    """Empirically verify that JudgmentRevisionIn strictly rejects reasons < 10 chars."""
    with pytest.raises(ValidationError) as exc_info:
        JudgmentRevisionIn.model_validate(
            {
                "expected_revision": 1,
                "expected_judgment_revision": 1,
                "resolution": "mark_fail",
                "reason": invalid_reason,
            }
        )
    errors = exc_info.value.errors()
    assert any("at least 10 characters" in str(e["msg"]) for e in errors)


def test_judgment_record_in_accepts_exactly_10_characters():
    """Verify boundary condition: exactly 10 characters after trimming is accepted."""
    record = JudgmentRecordIn.model_validate(
        {
            "expected_revision": 0,
            "resolution": "mark_pass",
            "reason": "1234567890",
        }
    )
    assert record.reason == "1234567890"

    # With surrounding whitespace:
    record_padded = JudgmentRecordIn.model_validate(
        {
            "expected_revision": 0,
            "resolution": "mark_pass",
            "reason": "   1234567890   ",
        }
    )
    assert record_padded.reason == "1234567890"


def test_judgment_revision_in_rejects_exceeding_max_length():
    """Verify upper bound: reasons over max length (1000) are rejected."""
    # 1000 chars is valid:
    valid_max = JudgmentRevisionIn.model_validate(
        {
            "expected_revision": 1,
            "expected_judgment_revision": 1,
            "resolution": "mark_pass",
            "reason": "x" * 1000,
        }
    )
    assert len(valid_max.reason) == 1000

    # 1001 chars is rejected:
    with pytest.raises(ValidationError) as exc_info:
        JudgmentRevisionIn.model_validate(
            {
                "expected_revision": 1,
                "expected_judgment_revision": 1,
                "resolution": "mark_pass",
                "reason": "x" * 1001,
            }
        )
    assert any("at most 1000 characters" in str(e["msg"]) for e in exc_info.value.errors())


@pytest.mark.asyncio
async def test_resolve_escalation_rejects_short_reasons():
    """Verify that resolve_escalation enforces minimum 10 characters for exceptional resolutions."""
    mock_session = AsyncMock()
    for bad_reason in ("", "short", "123456789", "   \t\n   "):
        with pytest.raises(ConflictError, match="at least 10 characters"):
            await resolve_escalation(
                mock_session,
                check_run_id=1,
                check_result_id=10,
                instructor_id=1,
                resolution="needs_document",
                reason=bad_reason,
                commit=False,
            )


# ==============================================================================
# 2. CONCURRENT REVISION CONFLICTS & ConflictError VERIFICATION
# ==============================================================================


@pytest.mark.asyncio
async def test_assessment_write_stale_expected_revision_raises_conflict_error():
    """Verify assessment_write raises ConflictError when expected_revision
    does not match report.assessment_revision.
    """
    mock_run = MagicMock(spec=CheckRun)
    mock_run.status = CheckRunStatus.done

    mock_report = MagicMock(spec=ReadinessReport)
    mock_report.assessment_revision = 5  # Current DB revision is 5
    mock_report.decision = None

    mock_session = AsyncMock()
    mock_session.no_autoflush = MagicMock()
    mock_session.scalar.return_value = mock_run

    with patch("app.report.assessment.lock_assessment", new=AsyncMock(return_value=mock_report)):
        # 1. Stale revision (older: 4 != 5)
        with pytest.raises(ConflictError) as exc_info:
            async with assessment_write(
                mock_session, check_run_id=1, instructor_id=10, expected_revision=4
            ):
                pass
        assert "This assessment changed after you reviewed it" in str(exc_info.value)

        # 2. Ahead revision (future: 6 != 5)
        with pytest.raises(ConflictError) as exc_info:
            async with assessment_write(
                mock_session, check_run_id=1, instructor_id=10, expected_revision=6
            ):
                pass
        assert "This assessment changed after you reviewed it" in str(exc_info.value)

        # 3. Matching revision (5 == 5) succeeds and increments assessment_revision to 6
        async with assessment_write(
            mock_session, check_run_id=1, instructor_id=10, expected_revision=5
        ) as rep:
            assert rep.assessment_revision == 6


@pytest.mark.asyncio
async def test_assessment_write_rejects_negative_or_boolean_expected_revision():
    """Verify assessment_write strictly validates expected_revision types and range."""
    mock_run = MagicMock(spec=CheckRun)
    mock_run.status = CheckRunStatus.done

    mock_report = MagicMock(spec=ReadinessReport)
    mock_report.assessment_revision = 0
    mock_report.decision = None

    mock_session = AsyncMock()
    mock_session.no_autoflush = MagicMock()
    mock_session.scalar.return_value = mock_run

    with patch("app.report.assessment.lock_assessment", new=AsyncMock(return_value=mock_report)):
        for bad_token in (-1, -99, True, False, "0", None, 1.5):
            with pytest.raises(ConflictError, match="Reload and review the current assessment"):
                async with assessment_write(
                    mock_session, check_run_id=1, instructor_id=10, expected_revision=bad_token
                ):
                    pass


@pytest.mark.asyncio
async def test_assessment_write_rejects_already_decided_report():
    """Verify assessment_write raises ConflictError when report is already
    decided and allow_decided=False.
    """
    mock_run = MagicMock(spec=CheckRun)
    mock_run.status = CheckRunStatus.done

    mock_report = MagicMock(spec=ReadinessReport)
    mock_report.assessment_revision = 2
    mock_report.decision = "returned"  # Already decided

    mock_session = AsyncMock()
    mock_session.no_autoflush = MagicMock()
    mock_session.scalar.return_value = mock_run

    with (
        patch("app.report.assessment.lock_assessment", new=AsyncMock(return_value=mock_report)),
        pytest.raises(ConflictError, match="This report has already been decided"),
    ):
        async with assessment_write(
            mock_session,
            check_run_id=1,
            instructor_id=10,
            expected_revision=2,
            allow_decided=False,
        ):
            pass


@pytest.mark.asyncio
async def test_revise_judgment_stale_judgment_revision_raises_conflict_error():
    """Verify revise_judgment raises ConflictError when expected_judgment_revision
    does not match before.judgment_revision.
    """
    result = CheckResult(
        id=101,
        check_run_id=1,
        criterion_id=5,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.passed,
        score=Decimal("100"),
        judgment_revision=2,  # Current is 2
        detail={
            "resolution": {
                "type": "mark_pass",
                "reason": "Previous valid instructor reasoning",
                "by_instructor_id": 7,
                "at": "2026-10-25T10:00:00+00:00",
            }
        },
    )
    criterion = Criterion(
        id=5, rubric_id=2, type=CriterionType.semantic, text="Crit text", weight=1, position=0
    )
    mock_run = MagicMock(spec=CheckRun)
    mock_run.status = CheckRunStatus.done
    report = ReadinessReport(check_run_id=1, assessment_revision=3, decision=None)

    # Caller submits revision claiming expected_judgment_revision = 1 (stale: 1 != 2)
    stale_body = JudgmentRevisionIn(
        expected_revision=3,
        expected_judgment_revision=1,  # STALE: 1 != 2
        resolution="mark_fail",
        reason="Fresh new justification for failing the criterion.",
    )

    mock_session = AsyncMock()
    mock_session.no_autoflush = MagicMock()
    mock_session.scalar.return_value = mock_run

    with (
        patch(
            "app.report.judgments._bound_result", new=AsyncMock(return_value=(result, criterion))
        ),
        patch("app.report.assessment.lock_assessment", new=AsyncMock(return_value=report)),
    ):
        with pytest.raises(ConflictError) as exc_info:
            await revise_judgment(
                mock_session, run_id=1, criterion_id=5, actor_id=7, body=stale_body
            )
        assert "This recorded judgment changed. Review the current judgment before saving." in str(
            exc_info.value
        )


@pytest.mark.asyncio
async def test_revise_judgment_identical_grade_and_reason_raises_conflict_error():
    """Verify revise_judgment rejects identical resolution + reason with ConflictError."""
    result = CheckResult(
        id=101,
        check_run_id=1,
        criterion_id=5,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.passed,
        score=Decimal("100"),
        judgment_revision=1,
        detail={
            "resolution": {
                "type": "mark_pass",
                "reason": "Existing recorded justification text",
                "by_instructor_id": 7,
                "at": "2026-10-25T10:00:00+00:00",
            }
        },
    )
    criterion = Criterion(
        id=5, rubric_id=2, type=CriterionType.semantic, text="Crit text", weight=1, position=0
    )
    mock_run = MagicMock(spec=CheckRun)
    mock_run.status = CheckRunStatus.done
    report = ReadinessReport(check_run_id=1, assessment_revision=2, decision=None)

    # Caller tries to submit identical resolution "mark_pass" and identical reason
    identical_body = JudgmentRevisionIn(
        expected_revision=2,
        expected_judgment_revision=1,
        resolution="mark_pass",
        reason="  Existing recorded justification text  ",  # same after strip
    )

    async def mock_scalar(query, *args, **kwargs):
        # If querying for CheckRun:
        if "check_run" in str(query).lower():
            return mock_run
        # If querying for clock_timestamp:
        return datetime.now(UTC)

    mock_session = AsyncMock()
    mock_session.no_autoflush = MagicMock()
    mock_session.scalar = AsyncMock(side_effect=mock_scalar)

    with (
        patch(
            "app.report.judgments._bound_result", new=AsyncMock(return_value=(result, criterion))
        ),
        patch("app.report.assessment.lock_assessment", new=AsyncMock(return_value=report)),
    ):
        with pytest.raises(ConflictError) as exc_info:
            await revise_judgment(
                mock_session, run_id=1, criterion_id=5, actor_id=7, body=identical_body
            )
        assert "Change the judgment or enter a new reason before saving." in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_judgment_history_stale_concurrency_raises_conflict_error():
    """Verify get_judgment_history raises ConflictError when expected_revision
    or expected_judgment_revision is stale.
    """
    result = CheckResult(
        id=101,
        check_run_id=1,
        criterion_id=5,
        kind=CheckKind.semantic,
        outcome=ResultOutcome.passed,
        score=Decimal("100"),
        judgment_revision=2,
        detail={
            "resolution": {
                "type": "mark_pass",
                "reason": "Recorded reason",
                "by_instructor_id": 7,
                "at": "2026-10-25T10:00:00+00:00",
            }
        },
    )
    criterion = Criterion(
        id=5, rubric_id=2, type=CriterionType.semantic, text="Crit text", weight=1, position=0
    )
    report = ReadinessReport(check_run_id=1, assessment_revision=4, decision=None)

    mock_session = AsyncMock()
    with (
        patch(
            "app.report.judgments._owned_parent", new=AsyncMock(return_value=(MagicMock(), report))
        ),
        patch(
            "app.report.judgments._bound_result", new=AsyncMock(return_value=(result, criterion))
        ),
    ):
        # 1. Stale assessment revision (3 != 4)
        with pytest.raises(ConflictError, match="This judgment or assessment changed"):
            await get_judgment_history(
                mock_session,
                run_id=1,
                criterion_id=5,
                actor_id=7,
                expected_revision=3,
                expected_judgment_revision=2,
            )

        # 2. Stale judgment revision (1 != 2)
        with pytest.raises(ConflictError, match="This judgment or assessment changed"):
            await get_judgment_history(
                mock_session,
                run_id=1,
                criterion_id=5,
                actor_id=7,
                expected_revision=4,
                expected_judgment_revision=1,
            )


# ==============================================================================
# 3. SNAPSHOT INTEGRITY & EDGE CASES
# ==============================================================================


def test_judgment_snapshot_requires_timezone_aware_datetime():
    """Verify JudgmentSnapshot strictly rejects timezone-naive timestamps."""
    naive_dt = datetime(2026, 10, 26, 12, 0, 0)  # No tzinfo
    with pytest.raises(ValidationError) as exc_info:
        JudgmentSnapshot(
            judgment_revision=1,
            resolution_type="mark_pass",
            outcome="passed",
            score=100.0,
            level=None,
            reason="Valid 10+ character reason",
            actor_id=1,
            recorded_at=naive_dt,
            provenance="recorded",
        )
    assert any("identify its time zone" in str(e["msg"]) for e in exc_info.value.errors())


def test_judgment_snapshot_rejects_non_finite_scores():
    """Verify JudgmentSnapshot rejects NaN, Inf, -Inf in scores."""
    for bad_score in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError) as exc_info:
            JudgmentSnapshot(
                judgment_revision=1,
                resolution_type="mark_pass",
                outcome="passed",
                score=bad_score,
                level=None,
                reason="Valid 10+ character reason",
                actor_id=1,
                recorded_at=datetime.now(UTC),
                provenance="recorded",
            )
        assert any("finite number" in str(e["msg"]) for e in exc_info.value.errors())


def test_judgment_snapshot_provenance_recorded_contract():
    """Verify provenance='recorded' strictly requires revision > 0, actor_id, and recorded_at."""
    # 1. judgment_revision == 0
    with pytest.raises(ValidationError):
        JudgmentSnapshot(
            judgment_revision=0,
            resolution_type="mark_pass",
            outcome="passed",
            score=100.0,
            level=None,
            reason="Valid 10+ character reason",
            actor_id=1,
            recorded_at=datetime.now(UTC),
            provenance="recorded",
        )

    # 2. actor_id is None
    with pytest.raises(ValidationError):
        JudgmentSnapshot(
            judgment_revision=1,
            resolution_type="mark_pass",
            outcome="passed",
            score=100.0,
            level=None,
            reason="Valid 10+ character reason",
            actor_id=None,
            recorded_at=datetime.now(UTC),
            provenance="recorded",
        )

    # 3. recorded_at is None
    with pytest.raises(ValidationError):
        JudgmentSnapshot(
            judgment_revision=1,
            resolution_type="mark_pass",
            outcome="passed",
            score=100.0,
            level=None,
            reason="Valid 10+ character reason",
            actor_id=1,
            recorded_at=None,
            provenance="recorded",
        )

    # 4. mark_level without level
    with pytest.raises(ValidationError):
        JudgmentSnapshot(
            judgment_revision=1,
            resolution_type="mark_level",
            outcome="passed",
            score=50.0,
            level=None,
            reason="Valid 10+ character reason",
            actor_id=1,
            recorded_at=datetime.now(UTC),
            provenance="recorded",
        )


def test_manual_grade_consistent_matrix():
    """Verify manual_grade_consistent matches exact rules for every resolution type."""
    # needs_document: only valid with outcome=not_applicable and score=None
    assert manual_grade_consistent("needs_document", ResultOutcome.not_applicable, None) is True
    assert manual_grade_consistent("needs_document", ResultOutcome.passed, None) is False
    assert manual_grade_consistent("needs_document", ResultOutcome.not_applicable, 0.0) is False

    # mark_pass: only valid with outcome=passed and score=100
    assert manual_grade_consistent("mark_pass", ResultOutcome.passed, 100.0) is True
    assert manual_grade_consistent("mark_pass", ResultOutcome.passed, 50.0) is False
    assert manual_grade_consistent("mark_pass", ResultOutcome.failed, 100.0) is False

    # mark_fail: only valid with outcome=failed and score=0
    assert manual_grade_consistent("mark_fail", ResultOutcome.failed, 0.0) is True
    assert manual_grade_consistent("mark_fail", ResultOutcome.failed, 10.0) is False
    assert manual_grade_consistent("mark_fail", ResultOutcome.passed, 0.0) is False

    # mark_level: valid with outcome=passed and score between 0 and 100
    assert manual_grade_consistent("mark_level", ResultOutcome.passed, 33.33) is True
    assert manual_grade_consistent("mark_level", ResultOutcome.passed, 0.0) is True
    assert manual_grade_consistent("mark_level", ResultOutcome.passed, 100.0) is True
    assert manual_grade_consistent("mark_level", ResultOutcome.failed, 0.0) is False
    assert manual_grade_consistent("mark_level", ResultOutcome.passed, 100.01) is False
    assert manual_grade_consistent("mark_level", ResultOutcome.passed, -0.01) is False


def test_judgment_grade_identity_equivalence():
    """Verify judgment_grade_identity produces identical tuples for identical
    grades and distinct for different.
    """
    snap1 = JudgmentSnapshot(
        judgment_revision=1,
        resolution_type="mark_pass",
        outcome="passed",
        score=100.0,
        level=None,
        reason="Reason text 123",
        actor_id=1,
        recorded_at=datetime.now(UTC),
        provenance="recorded",
    )
    snap2 = JudgmentSnapshot(
        judgment_revision=2,
        resolution_type="mark_pass",
        outcome="passed",
        score=100.0,
        level=None,
        reason="Different reason entirely",
        actor_id=2,
        recorded_at=datetime.now(UTC) + timedelta(days=1),
        provenance="recorded",
    )
    # Different revisions, actors, timestamps, reasons — but identical grade identity!
    assert judgment_grade_identity(snap1) == judgment_grade_identity(snap2)

    snap3 = JudgmentSnapshot(
        judgment_revision=1,
        resolution_type="mark_fail",
        outcome="failed",
        score=0.0,
        level=None,
        reason="Reason text 123",
        actor_id=1,
        recorded_at=datetime.now(UTC),
        provenance="recorded",
    )
    assert judgment_grade_identity(snap1) != judgment_grade_identity(snap3)
