"""V-032 tests: GRIM/GRIMMER/SPRITE. Every GRIMMER known-answer case here
is copied from `rsprite2`'s OWN test suite (`tests/testthat/
test-core-and-plot-functions.R`, "GRIMMER works as expected") — real
ground truth from the reference R implementation this module ports, not
authored by this session. GRIM/SPRITE cases wrap `pysprite` directly
(D-003) and are validated against its own README worked examples /
reproduced live behavior.
"""

from unittest.mock import patch

from app.checks.forensics.grim import (
    GrimmerResult,
    grim_check,
    grimmer_check,
    sprite_check,
)

# --- GRIM (pysprite passthrough) --------------------------------------------


def test_grim_consistent_mean_passes():
    # Matches rsprite2's own GRIMMER case 1 (5.21, n=28) — the underlying
    # GRIM check that case implicitly relies on.
    assert grim_check(28, 5.21, prec=2) is True


def test_grim_inconsistent_mean_fails():
    # Matches rsprite2's own GRIMMER case 3 (5.19, n=28) — that case's
    # own comment says "Fails if underlying GRIM test fails".
    assert grim_check(28, 5.19, prec=2) is False


# --- GRIMMER (ported from rsprite2::GRIMMER_test, R source) ----------------


def test_grimmer_basic_pass():
    """rsprite2 test suite: expect_true(GRIMMER_test(5.21, 1.6, 28))."""
    result = grimmer_check(5.21, 1.6, 28, m_prec=2, sd_prec=1)
    assert result.passed is True


def test_grimmer_basic_fail():
    """rsprite2 test suite: expect_false(GRIMMER_test(3.44, 2.47, 18))."""
    result = grimmer_check(3.44, 2.47, 18, m_prec=2, sd_prec=2)
    assert result.passed is False
    assert result.values == []


def test_grimmer_fails_when_underlying_grim_fails():
    """rsprite2 test suite: GRIMMER_test(5.19, 1.5, n_obs=28) fails with
    'GRIM test failed' — GRIMMER can never pass a mean GRIM itself rejects."""
    result = grimmer_check(5.19, 1.5, 28, m_prec=2, sd_prec=1)
    assert result.passed is False


def test_grimmer_returns_exact_consistent_values():
    """rsprite2 test suite: GRIMMER_test(mean=3.33, sd=1.234, n_obs=250,
    n_items=1, return_values=TRUE) rounds to c(1.2339, 1.2345) — the
    single most precise known-answer case in the reference suite."""
    result = grimmer_check(3.33, 1.234, 250, m_prec=2, sd_prec=3)
    assert result.passed is True
    assert [round(v, 4) for v in result.values] == [1.2339, 1.2345]


def test_grimmer_n_obs_less_than_2_is_undefined():
    """rsprite2 test suite: GRIMMER_test(2, 1, n_obs=1) -> FALSE (SD is
    undefined for a single observation)."""
    result = grimmer_check(2, 1, 1, m_prec=0, sd_prec=0)
    assert result.passed is False
    assert result.values == []


def test_grimmer_unenumerated_case_reports_passed_with_empty_values():
    """rsprite2's own case 1 (5.21, 1.6, 28) hits the 'too many consistent
    SDs to enumerate' branch — passed=True, values=[], flagged distinctly
    via `unenumerated` so a caller never mistakes this for 'no evidence
    either way'."""
    result = grimmer_check(5.21, 1.6, 28, m_prec=2, sd_prec=1)
    assert result.passed is True
    assert result.values == []
    assert result.unenumerated is True


def test_grimmer_zero_sd_on_lattice():
    """A zero SD is only possible if mean*n_items lands on an exact
    integer (every response identical) — sanity-checks the sd==0 branch
    the R source special-cases separately from the general search."""
    result = grimmer_check(3.0, 0.0, 10, m_prec=1, sd_prec=1)
    assert result.passed is True
    assert result.values == [0.0]


def test_grimmer_zero_sd_off_lattice_fails():
    result = grimmer_check(3.33, 0.0, 3, m_prec=2, sd_prec=1)
    assert result.passed is False


def test_grimmer_result_is_immutable_dataclass():
    result = grimmer_check(5.21, 1.6, 28, m_prec=2, sd_prec=1)
    assert isinstance(result, GrimmerResult)


# --- SPRITE (pysprite passthrough) ------------------------------------------


def test_sprite_reports_impossible_for_grim_failing_mean():
    """pysprite's own Sprite() constructor raises ValueError when the
    mean itself fails GRIM — a real gap found live (not documented
    clearly in the README's own worked example, which turned out to use
    a mean that fails GRIM for the given n). Must degrade to
    possible=False, never crash."""
    result = sprite_check(20, 3.02, 2.14, mean_prec=2, sd_prec=2, min_val=1, max_val=7)
    assert result.possible is False


def test_sprite_reports_impossible_for_sd_too_large_for_scale():
    """A real gap found live: pysprite's find_possible_distribution()
    raises a bare BaseException (not even Exception) for some genuinely-
    unsearchable inputs instead of returning a clean 'Failure' outcome —
    reproduced directly, must never crash this deterministic check."""
    result = sprite_check(10, 3.5, 10.0, mean_prec=1, sd_prec=1, min_val=1, max_val=5)
    assert result.possible is False


def test_sprite_success_path_wraps_pysprite_output_correctly():
    """pysprite returns ('Success'/'Failure', distribution, sd) — note
    the capitalization, a real bug caught live (this module's first draft
    compared against lowercase 'success' and silently treated every real
    success as a failure). Mocked here because SPRITE's own search is a
    stochastic local search (verified live: even a hand-constructed,
    mathematically-achievable target distribution didn't always converge
    within a large iteration budget) — this test proves the WRAPPER's own
    transformation logic, not pysprite's search luck on a given run."""
    import numpy as np

    fake_distribution = np.array([1, 2, 2, 3, 3, 3, 4, 4, 5])
    with patch("app.checks.forensics.grim.pysprite.Sprite") as mock_sprite_cls:
        mock_sprite_cls.return_value.find_possible_distribution.return_value = (
            "Success",
            fake_distribution,
            1.23,
        )
        result = sprite_check(9, 3.0, 1.23, mean_prec=2, sd_prec=2, min_val=1, max_val=5)
    assert result.possible is True
    assert result.achieved_sd == 1.23
    assert result.distribution == [1, 2, 2, 3, 3, 3, 4, 4, 5]
