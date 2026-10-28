"""Known-answer controls for offered-case accounting; no inference or network."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.checks.accuracy_stats import (
    ConfusionMatrix,
    PairedComparison,
    compute_agreement,
    mcnemar_exact,
    wilson_interval,
)
from app.checks.golden import GoldenItem, GoldenPrediction, report_as_markdown, score_golden_set
from app.config import get_settings
from app.models.enums import ResultOutcome
from scripts import ab_prompt_compare, golden_harness


def item(index=0, criterion="criterion"):
    return GoldenItem(str(index), criterion, "semantic", "source", "pass", "reason", "control")


def mixed_predictions():
    return [
        GoldenPrediction(item(i), False, "pass", 1, ["pass"])
        if i < 2
        else GoldenPrediction(item(i), True, None, 0, ["partial", "fail"])
        if i < 4
        else GoldenPrediction(item(i, "error-only"), False, None, None, [], error="api_down")
        for i in range(10)
    ]


def test_offered_denominator_includes_errors_without_calling_them_wrong():
    report = score_golden_set(mixed_predictions())
    assert report.agreement_rate == report.stats.accuracy == 1
    assert report.stats.n_offered == 10
    assert report.stats.n_errored == 6
    assert report.stats.coverage == 0.2
    assert report.stats.operational_completion == 0.4
    assert sum(c.n for c in report.by_criterion) == 10
    failed_lane = next(c for c in report.by_criterion if c.criterion_text == "error-only")
    assert failed_lane.n_errored == failed_lane.n == 6
    assert failed_lane.n_decided == 0
    md = report_as_markdown(report, title="control", provisional_note="Not accuracy evidence")
    assert "20.0%" in md and "40.0%" in md
    assert "6 errored" in md


@pytest.mark.parametrize("state", ["errors", "escalations", "empty"])
def test_no_decision_accounting_is_not_silently_dropped(state):
    predictions = mixed_predictions()[4:] if state == "errors" else mixed_predictions()[2:4]
    if state == "empty":
        predictions = []
    report = score_golden_set(predictions)
    assert report.stats is not None
    assert report.stats.accuracy is None
    assert report.stats.coverage == (None if state == "empty" else 0)
    assert report.stats.operational_completion == (
        None if state == "empty" else 0 if state == "errors" else 1
    )
    md = report_as_markdown(report, title="control", provisional_note="control")
    assert "Every item escalated" not in md


@pytest.mark.parametrize("side", ["human_labels", "judge_labels"])
@pytest.mark.parametrize("label", ["nonsense", "partial", "", None])
def test_unsupported_labels_cannot_be_correct_negative_predictions(side, label):
    values = {"human_labels": ["fail"], "judge_labels": ["fail"]}
    values[side] = [label]
    with pytest.raises(ValueError):
        compute_agreement(**values)


@pytest.mark.parametrize("name", ["n_abstained", "n_errored"])
@pytest.mark.parametrize("count", [-1, 0.5, True, "2"])
def test_population_counts_are_nonnegative_integers(name, count):
    with pytest.raises(ValueError):
        compute_agreement(human_labels=["pass"], judge_labels=["pass"], **{name: count})


@pytest.mark.parametrize(
    "change",
    [
        {"predicted": None},
        {"predicted": "unknown"},
        {"escalated": True},
        {"error": "api_down"},
        {"error": ""},
        {"escalated": 1},
    ],
)
def test_invalid_prediction_states_are_rejected(change):
    valid = GoldenPrediction(item(), False, "pass", 1, ["pass"])
    with pytest.raises(ValueError):
        score_golden_set([replace(valid, **change)])


def test_invalid_golden_label_is_rejected_at_construction():
    with pytest.raises(ValueError):
        replace(item(), instructor_grade="unknown")


def test_ab_uses_the_same_error_accounting_and_validates_missing_predictions():
    predictions = mixed_predictions()
    stats = ab_prompt_compare._arm_stats(
        [p.item for p in predictions], [(p.predicted, p.escalated, p.error) for p in predictions]
    )
    assert stats.coverage == 0.2
    assert stats.operational_completion == 0.4
    with pytest.raises(ValueError):
        ab_prompt_compare._arm_stats([item()], [(None, False, None)])


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [golden_harness, ab_prompt_compare])
@pytest.mark.parametrize("verdict", [None, "unknown", "partial", "pass", "fail"])
async def test_adapter_rejects_unknown_verdict_without_changing_partial_projection(
    monkeypatch, module, verdict
):
    voted = SimpleNamespace(outcome=ResultOutcome.passed, detail={"verdict": verdict})
    monkeypatch.setattr(module, "vote_batch", AsyncMock(return_value=[voted]))

    async def grade():
        if module is golden_harness:
            return (await module._grade_item(item(), 0, object(), get_settings())).predicted
        return (await module._grade(item(), 0, object(), get_settings(), "test"))[0]

    if verdict not in ("pass", "partial", "fail"):
        with pytest.raises(ValueError):
            await grade()
    else:
        assert await grade() == ("fail" if verdict == "fail" else "pass")


@pytest.mark.parametrize("count", [-1, True, 1.2])
def test_invalid_matrix_and_interval_counts_cannot_be_scored(count):
    with pytest.raises(ValueError):
        ConfusionMatrix(count, 0, 0, 0)
    with pytest.raises(ValueError):
        PairedComparison(0, count, 0, 0)
    with pytest.raises(ValueError):
        wilson_interval(0, count)


def test_no_paired_decisions_does_not_claim_identical_decisions():
    result = mcnemar_exact(PairedComparison(0, 0, 0, 0))
    assert result.p_value is None
    assert "no paired decisions" in result.note
    assert "identical decisions" not in result.note


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["empty", "errors", "mixed"])
async def test_ab_writes_population_accounting_for_empty_and_failed_arms(
    monkeypatch, tmp_path, state
):
    predictions = mixed_predictions() if state == "mixed" else mixed_predictions()[4:]
    if state == "empty":
        predictions = []
    monkeypatch.setattr(ab_prompt_compare, "_load_items", lambda: [p.item for p in predictions])
    monkeypatch.setattr(ab_prompt_compare, "REPORT_DIR", tmp_path)

    async def grade(_item, idx, *_args):
        p = predictions[idx]
        return p.predicted, p.escalated, p.error

    monkeypatch.setattr(ab_prompt_compare, "_grade", grade)
    await ab_prompt_compare.run(live=False, arm_a="control-a", arm_b="control-b")
    report = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "operational completion" in report
    if state == "empty":
        assert "coverage N/A" in report
    elif state == "errors":
        assert "coverage 0.0%" in report
        assert "6 offered, 0 decided, 0 escalated, 6 errored" in report
        assert "identical decisions on every item" not in report
    else:
        assert "coverage 20.0%" in report
        assert "operational completion 40.0%" in report


def test_ab_main_tolerates_windows_console_encoding(monkeypatch):
    import io
    from contextlib import redirect_stdout

    output = io.BytesIO()
    console = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(ab_prompt_compare.sys, "argv", ["ab_prompt_compare", "--fake"])

    async def run(**_kwargs):
        print("κ ⚠")

    monkeypatch.setattr(ab_prompt_compare, "run", run)
    with redirect_stdout(console):
        ab_prompt_compare.main()
        console.flush()
    assert output.getvalue()
