"""V-053: agreement statistics — KNOWN-ANSWER tests (TESTING §2).

Every expected value here is either hand-computable from the formula or a
published reference value, so the arithmetic can be checked at the defense
without trusting this code. That is the point of the module: the panel is
the adversary model.
"""

import math

import pytest

from app.checks.agreement import (
    ConfusionMatrix,
    cohens_kappa,
    compute_agreement,
    gwet_ac1,
    matthews_corrcoef,
    wilson_interval,
)

# Worked textbook table: 50 items, po = 0.70, pe = 0.50 -> kappa = 0.40.
#   human passed 25 (20 tp + 5 fn), judge passed 30 (20 tp + 10 fp)
TEXTBOOK = ConfusionMatrix(tp=20, fp=10, tn=15, fn=5)


def test_cohens_kappa_matches_hand_computation():
    # po = (20+15)/50 = 0.70
    # pe = (25/50)(30/50) + (25/50)(20/50) = 0.30 + 0.20 = 0.50
    # kappa = (0.70-0.50)/(1-0.50) = 0.40
    assert cohens_kappa(TEXTBOOK) == pytest.approx(0.40)


def test_gwet_ac1_matches_hand_computation():
    # pi = (0.50 + 0.60)/2 = 0.55 ; pe = 2(0.55)(0.45) = 0.495
    # AC1 = (0.70 - 0.495)/(1 - 0.495) = 0.40594...
    assert gwet_ac1(TEXTBOOK) == pytest.approx(0.205 / 0.505)


def test_mcc_matches_hand_computation():
    # (20*15 - 10*5)/sqrt(30*25*25*20) = 250/sqrt(375000)
    assert matthews_corrcoef(TEXTBOOK) == pytest.approx(250 / math.sqrt(375000))


def test_wilson_interval_reference_value_for_ten_of_ten():
    """Published reference: 10/10 at 95% gives roughly [0.722, 1.0]. The Wald
    interval would give [1.0, 1.0] — a zero-width claim of certainty from ten
    observations, which is the failure mode this replaces."""
    interval = wilson_interval(10, 10)
    assert interval.low == pytest.approx(0.7225, abs=5e-4)
    assert interval.high == pytest.approx(1.0)


def test_wilson_interval_exposes_the_one_of_one_anecdote():
    """The V-025 baseline really did report 'agreement 1.0' from a single
    graded item. The honest reading of 1/1 is 'somewhere between 21% and
    100%' — this test exists so that claim can never quietly return."""
    interval = wilson_interval(1, 1)
    assert interval.low == pytest.approx(0.2065, abs=5e-4)
    assert interval.high == pytest.approx(1.0)
    assert interval.low < 0.25


def test_no_observations_is_not_a_wide_interval():
    assert wilson_interval(0, 0) is None


def test_kappa_paradox_is_visible_and_ac1_resists_it():
    """THE reason both coefficients are reported.

    An always-'pass' judge on a set where the instructor passed 18 of 20:
    accuracy is a flattering 90%, but the judge carries no information — it
    never once said 'fail'. Kappa correctly collapses to ~0 while accuracy
    stays high, and AC1 stays closer to observed agreement. A single
    accuracy number would have called this a success.
    """
    stats = compute_agreement(
        human_labels=["pass"] * 18 + ["fail"] * 2,
        judge_labels=["pass"] * 20,
    )
    assert stats.accuracy == pytest.approx(0.90)
    # Degenerate judge margin: kappa/MCC are undefined, NOT zero and not
    # silently dropped (the paper's "degenerate criteria" rule).
    assert stats.is_degenerate
    assert stats.mcc is None
    assert stats.ac1 is not None and stats.ac1 > 0.8


def test_leniency_shows_which_direction_the_judge_errs():
    """A wrong 'fail' is a wrong accusation — the expensive direction for
    this product — so the report must distinguish harsh from lenient rather
    than only counting errors."""
    harsh = compute_agreement(
        human_labels=["pass", "pass", "pass", "fail"],
        judge_labels=["fail", "fail", "pass", "fail"],
    )
    assert harsh.matrix.leniency == pytest.approx(0.25 - 0.75)
    assert harsh.matrix.fn == 2  # instructor passed, judge failed

    lenient = compute_agreement(
        human_labels=["fail", "fail", "pass"],
        judge_labels=["pass", "pass", "pass"],
    )
    assert lenient.matrix.leniency > 0


def test_coverage_is_reported_so_abstaining_cannot_fake_accuracy():
    """Escalating everything hard and scoring 100% on what is left is not a
    good result. Coverage is what makes selective accuracy interpretable."""
    stats = compute_agreement(
        human_labels=["pass", "fail"],
        judge_labels=["pass", "fail"],
        n_abstained=8,
    )
    assert stats.accuracy == pytest.approx(1.0)
    assert stats.coverage == pytest.approx(0.2)
    assert stats.n_abstained == 8


def test_perfect_agreement_on_a_balanced_set_is_kappa_one():
    stats = compute_agreement(
        human_labels=["pass", "fail", "pass", "fail"],
        judge_labels=["pass", "fail", "pass", "fail"],
    )
    assert stats.kappa == pytest.approx(1.0)
    assert stats.mcc == pytest.approx(1.0)
    assert stats.accuracy_ci.low < 1.0  # even 4/4 is not certainty


def test_mismatched_label_lengths_is_an_error_not_a_silent_truncation():
    with pytest.raises(ValueError):
        compute_agreement(human_labels=["pass"], judge_labels=["pass", "fail"])
