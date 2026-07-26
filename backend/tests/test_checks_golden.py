"""V-025 unit tests: the golden-set scoring arithmetic (pure, no DB/LLM)
and the tier-promotion gate. The "harness sensitivity" AC (a deliberate
regression measurably drops the score) is proven here at the scoring
level — feeding the SAME aggregation function a better vs. a worse
prediction set and asserting the score actually moves, which is what
would happen live if a prompt regression degraded real predictions.
"""

from app.checks.golden import GoldenItem, GoldenPrediction, score_golden_set
from app.checks.promotion import ShadowClassStats, evaluate_promotions
from app.config import get_settings

_ITEMS = [
    GoldenItem(
        id=f"g{i}",
        criterion_text=f"C{i % 3}",
        criterion_type="semantic",
        excerpt="...",
        instructor_grade="pass" if i % 2 == 0 else "fail",
        reason="",
        source="synthetic-control",
    )
    for i in range(6)
]  # pass, fail, pass, fail, pass, fail


def _predictions(
    correct: list[bool], *, escalate: list[bool] | None = None
) -> list[GoldenPrediction]:
    escalate = escalate or [False] * len(_ITEMS)
    preds = []
    for item, is_correct, is_escalated in zip(_ITEMS, correct, escalate, strict=True):
        if is_escalated:
            preds.append(GoldenPrediction(item, True, None, 0.333, ["pass", "fail", "partial"]))
            continue
        opposite = "fail" if item.instructor_grade == "pass" else "pass"
        predicted = item.instructor_grade if is_correct else opposite
        preds.append(GoldenPrediction(item, False, predicted, 1.0, [predicted, predicted]))
    return preds


def test_perfect_agreement():
    report = score_golden_set(_predictions([True] * 6))
    assert report.agreement_rate == 1.0
    assert report.n_agree == 6
    assert report.disagreements == []


def test_escalated_items_excluded_from_agreement_not_counted_as_wrong():
    # 4 correct, 2 escalated (never "wrong").
    all_correct = [True, True, True, True, True, True]
    two_escalated = [False, False, True, True, False, False]
    report = score_golden_set(_predictions(all_correct, escalate=two_escalated))
    assert report.n_escalated == 2
    assert report.n_decided == 4
    assert report.agreement_rate == 1.0  # all 4 DECIDED items agree
    assert report.escalation_rate == 2 / 6


def test_all_escalated_reports_none_not_a_fabricated_score():
    report = score_golden_set(_predictions([True] * 6, escalate=[True] * 6))
    assert report.agreement_rate is None
    assert report.n_decided == 0


def test_sensitivity_a_regression_measurably_drops_the_score():
    """The harness's whole point: catch a prompt/rubric regression before
    it ships. Simulating a "regressed prompt" as a worse prediction set
    (half now wrong instead of all correct) must produce a LOWER,
    distinguishable agreement number — proving the arithmetic is
    sensitive, not just present."""
    baseline = score_golden_set(_predictions([True] * 6))
    regressed = score_golden_set(_predictions([True, False, True, False, True, False]))
    assert baseline.agreement_rate == 1.0
    assert regressed.agreement_rate == 0.5
    assert regressed.agreement_rate < baseline.agreement_rate
    assert len(regressed.disagreements) == 3
    assert len(baseline.disagreements) == 0


def test_errored_items_never_count_as_escalated_or_wrong():
    """api_down/quota_exhausted mid-run (TESTING.md §5) must never read
    as a confidence problem OR a wrong grade — it's a third, distinct
    state, excluded from both agreement and escalation-rate math."""
    preds = _predictions([True, True, True, True, True, True])
    errored = GoldenPrediction(_ITEMS[0], False, None, None, [], error="Gemini did not respond.")
    report = score_golden_set([errored, *preds[1:]])
    assert report.n_errored == 1
    assert report.n_total == 6
    # Only the 5 gradeable items count toward escalation/agreement math.
    assert report.n_decided == 5
    assert report.agreement_rate == 1.0
    assert report.errored == [errored]
    assert errored not in report.disagreements


def test_per_criterion_breakdown_groups_by_criterion_text():
    report = score_golden_set(_predictions([True] * 6))
    assert len(report.by_criterion) == 3  # C0, C1, C2
    for c in report.by_criterion:
        assert c.agreement_rate == 1.0
        assert c.n == 2


def test_disagreements_carry_enough_detail_to_review():
    report = score_golden_set(_predictions([False, True, True, True, True, True]))
    assert len(report.disagreements) == 1
    d = report.disagreements[0]
    assert d.item.id == "g0"
    assert d.predicted != d.item.instructor_grade


# --- tier promotion gate -----------------------------------------------------------


def test_class_below_min_n_never_promotes_even_at_perfect_agreement():
    settings = get_settings()
    stats = [
        ShadowClassStats("readability", "tier0", n=settings.tier_promotion_min_n - 1, agreement=1.0)
    ]
    assert evaluate_promotions(stats, dated="2026-07-25", settings=settings) == []


def test_class_below_agreement_bar_never_promotes_even_with_enough_samples():
    settings = get_settings()
    stats = [
        ShadowClassStats(
            "readability",
            "tier0",
            n=settings.tier_promotion_min_n,
            agreement=settings.tier_promotion_agreement - 0.01,
        )
    ]
    assert evaluate_promotions(stats, dated="2026-07-25", settings=settings) == []


def test_class_clearing_both_bars_promotes():
    settings = get_settings()
    stats = [
        ShadowClassStats(
            "readability",
            "tier0",
            n=settings.tier_promotion_min_n,
            agreement=settings.tier_promotion_agreement,
        )
    ]
    promotions = evaluate_promotions(stats, dated="2026-07-25", settings=settings)
    assert len(promotions) == 1
    assert promotions[0].class_name == "readability"
