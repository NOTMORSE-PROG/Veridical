"""GRIM/GRIMMER/SPRITE checks (F6.2/F6.3, V-032).

GRIM (mean-consistency) and SPRITE (distribution plausibility) wrap
`pysprite` directly — both are validated, actively maintained MIT-licensed
Python libraries (D-003: reuse, never reimplement).

**GRIMMER (SD-consistency) has no maintained Python implementation as of
this ticket's pickup (2026-08-06)** — only R's `rsprite2::GRIMMER_test`
exists (PyPI/GitHub searched; confirmed absent). Put to the owner
directly rather than assumed silently: skip it, shell out to R, or port
the algorithm. Owner's explicit call: "u decider... i want free, the
best... 100 percent accuracy, u can even create an algo and math" — port
it. What follows is a line-for-line translation of `rsprite2`'s own R
source (github.com/LukasWallrich/rsprite2, MIT-licensed:
`R/core-functions.R`'s `GRIMMER_test`/`GRIM_test`/`.equalish`/
`round_down`/`round_up`), not an original derivation — every constant
(`rSprite.dust = 1e-10`) and every formula step matches that file.
Validated against that SAME package's own test suite
(`tests/testthat/test-core-and-plot-functions.R`, `"GRIMMER works as
expected"`) as ground truth: every known-answer case in this module's own
tests is a real assertion copied from that suite, not authored here.
This is the one place in the whole forensics stack (D-003) that writes
math instead of wrapping a library — done this way specifically because
no library exists to wrap, and the port is checked against the reference
implementation's own test vectors rather than trusted on inspection alone.
"""

import math
from dataclasses import dataclass, field

import pysprite

_DUST = 1e-10  # rsprite2's `rSprite.dust` — floating-point tolerance


def _equalish(x: float, y: float, tol: float = _DUST) -> bool:
    return (x <= y + tol) and (x >= y - tol)


def _round_down(number: float, decimals: int, tolerance: float = _DUST) -> float:
    p = 10**decimals
    is_halfway = abs((number * p - math.floor(number * p)) - 0.5) < tolerance
    return math.floor(number * p) / p if is_halfway else round(number, decimals)


def _round_up(number: float, decimals: int, tolerance: float = _DUST) -> float:
    p = 10**decimals
    is_halfway = abs((number * p - math.floor(number * p)) - 0.5) < tolerance
    return math.ceil(number * p) / p if is_halfway else round(number, decimals)


def grim_check(n: int, mean: float, prec: int = 2, n_items: int = 1) -> bool:
    """Wraps `pysprite.grim` directly (D-003) — the ticket's own "GRIM on
    means" check."""
    return bool(pysprite.grim(n, mean, prec=prec, n_items=n_items))


def _grim_possible_means(mean: float, n_obs: int, m_prec: int, n_items: int = 1) -> list[float]:
    """Port of `rsprite2::GRIM_test`'s possible-sums calculation — GRIMMER
    needs the actual candidate means, not just a pass/fail boolean, so
    this is used internally rather than reusing `grim_check`'s bool-only
    `pysprite` wrapper."""
    effective_n = n_obs * n_items
    granule_mean = 0.5 * 10 ** (-m_prec)
    sum_lower_bound = (mean - granule_mean - _DUST) * effective_n
    sum_upper_bound = (mean + granule_mean + _DUST) * effective_n
    final_lower = math.ceil(sum_lower_bound)
    final_upper = math.floor(sum_upper_bound)
    if final_lower > final_upper:
        return []
    return [s / effective_n for s in range(final_lower, final_upper + 1)]


@dataclass(frozen=True)
class GrimmerResult:
    passed: bool
    values: list[float] = field(default_factory=list)
    # True when GRIMMER passed but too many SDs are consistent to
    # enumerate (rsprite2's own "any SD in range [...] is compatible"
    # case) — the caller must not treat an empty `values` here the same
    # way it would treat empty-because-genuinely-inconsistent.
    unenumerated: bool = False


def grimmer_check(
    mean: float,
    sd: float,
    n_obs: int,
    *,
    m_prec: int,
    sd_prec: int,
    n_items: int = 1,
) -> GrimmerResult:
    """Port of `rsprite2::GRIMMER_test` (module docstring — validated
    against that package's own known-answer test suite, not invented)."""
    if n_obs < 2:
        return GrimmerResult(passed=False)  # SD undefined for a single observation

    possible_means = _grim_possible_means(mean, n_obs, m_prec, n_items)
    if not possible_means:
        return GrimmerResult(passed=False)  # GRIM itself already fails -> GRIMMER fails

    effective_n = n_obs * n_items
    granule_sd = 5 / (10 ** (sd_prec + 1))
    l_sigma = 0.0 if sd < granule_sd else sd - granule_sd
    u_sigma = sd + granule_sd

    if _equalish(sd, 0):
        on_lattice = abs(mean * n_items - round(mean * n_items)) < _DUST
        return GrimmerResult(passed=on_lattice, values=[0.0] if on_lattice else [])

    test_passed = False
    consistent_sds: list[float] = []

    for realmean in possible_means:
        realsum = realmean * effective_n
        lower_bound_ss = ((n_obs - 1) * l_sigma**2 + n_obs * realmean**2) * n_items**2
        upper_bound_ss = ((n_obs - 1) * u_sigma**2 + n_obs * realmean**2) * n_items**2

        if math.ceil(lower_bound_ss) > math.floor(upper_bound_ss):
            continue

        window = math.floor(upper_bound_ss) - math.ceil(lower_bound_ss)
        if window >= 1:
            # At least one integer sum of EACH parity exists in range —
            # every SD in the possible range is GRIMMER-consistent. R's
            # own implementation short-circuits the WHOLE function here,
            # not just this iteration.
            return GrimmerResult(passed=True, values=[], unenumerated=True)

        possible_ss = range(math.ceil(lower_bound_ss), math.floor(upper_bound_ss) + 1)
        possible_ss = [x for x in possible_ss if x % 2 == round(realsum) % 2]
        if not possible_ss:
            continue

        for x in possible_ss:
            variance = (x / n_items**2 - n_obs * realmean**2) / (n_obs - 1)
            variance = max(variance, 0.0)
            predicted_sd = math.sqrt(variance)
            if _equalish(_round_down(predicted_sd, sd_prec), sd) or _equalish(
                _round_up(predicted_sd, sd_prec), sd
            ):
                test_passed = True
                consistent_sds.append(predicted_sd)

    output_values = sorted({round(v, 10) for v in consistent_sds})
    return GrimmerResult(passed=test_passed, values=output_values)


@dataclass(frozen=True)
class SpriteResult:
    possible: bool
    distribution: list[float] | None = None
    achieved_sd: float | None = None


def sprite_check(
    n: int,
    mean: float,
    sd: float,
    *,
    mean_prec: int,
    sd_prec: int,
    min_val: int,
    max_val: int,
    n_items: int = 1,
    init_method: str = "maxvar",
    max_iter: int = 100_000,
) -> SpriteResult:
    """Wraps `pysprite.Sprite` directly (D-003) — finds ONE candidate
    integer-item distribution consistent with n/mean/SD/scale, or reports
    none exists (SPRITE's own "possibility", not a claim about what the
    manuscript's real underlying data was). SPRITE's search is a
    stochastic local search, not exhaustive — `possible=False` means "not
    found within this budget," not a mathematical proof of impossibility
    the way GRIM/GRIMMER's closed-form checks are; `init_method`/
    `max_iter` are exposed (not hardcoded) so a caller can widen the
    search rather than accept a false negative silently."""
    try:
        sprite = pysprite.Sprite(
            n=n,
            mu=mean,
            sd=sd,
            mu_prec=mean_prec,
            sd_prec=sd_prec,
            min_val=min_val,
            max_val=max_val,
            n_items=n_items,
        )
        outcome, distribution, achieved_sd = sprite.find_possible_distribution(
            init_method=init_method, max_iter=max_iter
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 — two real gaps found live: pysprite's own
        # constructor raises `ValueError` when the mean itself fails GRIM
        # (an "impossible" input, not a bug — a genuinely inconsistent
        # reported mean IS the finding), and `find_possible_distribution`
        # separately raises a bare `BaseException` (not even `Exception`,
        # confirmed by reproducing it) for some inputs it can't search
        # rather than returning a 'Failure' outcome. Neither is allowed to
        # crash this deterministic check over a manuscript's own (possibly
        # wrong) reported numbers. KeyboardInterrupt/SystemExit still
        # propagate — only pysprite's own exception misuse is swallowed.
        return SpriteResult(possible=False)
    if outcome.casefold() != "success":  # pysprite returns "Success"/"Failure" (capitalized)
        return SpriteResult(possible=False)
    return SpriteResult(possible=True, distribution=list(distribution), achieved_sd=achieved_sd)
