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
    n = settings.tier_promotion_min_n - 1
    stats = [ShadowClassStats("readability", "tier0", n=n, agreement=1.0, n_agree=n)]
    assert evaluate_promotions(stats, dated="2026-07-25", settings=settings) == []


def test_class_below_agreement_bar_never_promotes_even_with_enough_samples():
    settings = get_settings()
    n = settings.tier_promotion_min_n
    n_agree = int(n * (settings.tier_promotion_agreement - 0.01))
    stats = [ShadowClassStats("readability", "tier0", n=n, agreement=n_agree / n, n_agree=n_agree)]
    assert evaluate_promotions(stats, dated="2026-07-25", settings=settings) == []


def test_class_clearing_both_bars_promotes():
    """Root-caused, not a flaky retry (2026-08-12): this used to read `n =
    settings.tier_promotion_min_n` and assume perfect agreement AT the
    minimum n clears the 90% Wilson lower bound. That's only true at
    `config.py`'s intentional in-code default of 35 (low = n/(n+z^2) >=
    0.90 needs n >= ~34.6) — the test broke on any run where `.env`
    overrode it down to 20, per `.env.example`'s value at the time this
    comment was first written. **Correction (BUG-030, 2026-08-12, same
    day): `.env.example` was never an intentional post-V-053 decision —
    it was simply stale, unedited since the original V-008 commit, and
    has since been fixed to 35 to match `config.py`.** At n=20 even 100%
    agreement only clears a 0.839 lower bound, correctly BELOW the bar
    (the gate's own conservative-estimate design working exactly as
    intended, per this module's docstring) — that part of the original
    analysis was right; only the claim that 20 was a deliberate choice
    was backwards. Still uses an explicit n=35 override here regardless
    of `.env.example`'s now-correct value, so this test asserts the
    actual promotion path independent of whatever `tier_promotion_min_n`
    happens to be configured to in the environment it runs in — the two
    neighboring tests above don't have this problem since "below the bar
    never promotes" holds at any min_n value.
    """
    settings = get_settings().model_copy(update={"tier_promotion_min_n": 35})
    n = 35
    stats = [ShadowClassStats("readability", "tier0", n=n, agreement=1.0, n_agree=n)]
    promotions = evaluate_promotions(stats, dated="2026-07-25", settings=settings)
    assert len(promotions) == 1
    assert promotions[0].class_name == "readability"
    assert promotions[0].agreement_lower_bound >= settings.tier_promotion_agreement


def test_observed_rate_at_the_bar_is_NOT_enough_when_the_interval_is_wide():
    """V-053, the whole point of the change. 90% observed over 20 samples is
    consistent with a true rate near 70% — the old point-estimate gate handed
    out permanent autonomy on exactly that evidence."""
    settings = get_settings().model_copy(update={"tier_promotion_min_n": 20})
    stats = [ShadowClassStats("readability", "tier0", n=20, agreement=0.90, n_agree=18)]
    assert evaluate_promotions(stats, dated="2026-07-26", settings=settings) == []


def test_missing_success_count_refuses_to_promote_rather_than_guessing():
    """No `n_agree` means no interval. Falling back to the observed rate
    would silently restore the weaker rule this replaced."""
    settings = get_settings()
    stats = [
        ShadowClassStats(
            "readability", "tier0", n=settings.tier_promotion_min_n, agreement=1.0, n_agree=None
        )
    ]
    assert evaluate_promotions(stats, dated="2026-07-26", settings=settings) == []


# --- V-053: the report must not let a small sample look like a baseline ------


def _pred(item_id, golden, predicted, escalated=False):
    from app.checks.golden import GoldenItem, GoldenPrediction

    item = GoldenItem(
        id=item_id,
        criterion_text="Chapter 1 states the research problem",
        criterion_type="semantic",
        excerpt="...",
        instructor_grade=golden,
        reason="",
        source="test",
    )
    return GoldenPrediction(
        item=item,
        escalated=escalated,
        predicted=None if escalated else predicted,
        agreement=1.0,
        votes=["pass", "pass"],
    )


def test_report_marks_a_small_sample_as_indicative_not_a_baseline():
    """The V-025 run reported 'agreement 1.0' off ONE item. The same data
    must now announce its own inadequacy BEFORE the number."""
    from app.checks.golden import report_as_markdown

    markdown = report_as_markdown(
        score_golden_set([_pred("g001", "pass", "pass")]),
        title="t",
        provisional_note="_n_",
    )
    assert "INDICATIVE ONLY" in markdown
    assert "not a baseline" in markdown
    # And the interval must be present and honest: 1/1 spans almost everything.
    assert "20.7%" in markdown
    assert markdown.index("INDICATIVE ONLY") < markdown.index("Selective accuracy")


def test_report_carries_kappa_coverage_and_confusion_not_just_accuracy():
    from app.checks.golden import report_as_markdown

    predictions = [
        _pred("g1", "pass", "pass"),
        _pred("g2", "fail", "fail"),
        _pred("g3", "pass", "fail"),
        _pred("g4", "fail", None, escalated=True),
    ]
    markdown = report_as_markdown(score_golden_set(predictions), title="t", provisional_note="_n_")
    for required in ("Cohen's", "Gwet's AC1", "MCC", "Confusion", "Coverage", "Prevalence"):
        assert required in markdown, f"report must show {required}"
    # Abstention mode named explicitly — accuracy over a covered subset is
    # meaningless without it.
    assert "EXCLUDE" in markdown


def test_degenerate_set_reports_na_rather_than_zero_agreement():
    """One label only => chance-corrected coefficients are undefined. Zero
    would read as 'no better than chance', which is a different claim."""
    from app.checks.golden import report_as_markdown

    predictions = [_pred("g1", "pass", "pass"), _pred("g2", "pass", "pass")]
    report = score_golden_set(predictions)
    assert report.stats.is_degenerate
    markdown = report_as_markdown(report, title="t", provisional_note="_n_")
    assert "undefined" in markdown
