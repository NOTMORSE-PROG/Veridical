"""F6.3: bounded reconstruction, with independently checked candidate witnesses."""

import hashlib
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from fractions import Fraction

from app.checks.forensics.keywords import load_keywords
from app.checks.forensics.sprite_inputs import (
    NOT_ADMITTED_REASONS,
    SpriteRow,
    extract_sprite_rows,
    load_sprite_policy,
    single_item_incompatible,
)
from app.config import Settings, get_settings
from app.ingest.schemas import ExtractionResult


def validate_sprite_witness(distribution: object, row: SpriteRow) -> dict | None:
    """Check the returned witness, not SPRITE's rounded success indication.

    Exact rational means/variances are compared to the printed rounding intervals;
    a candidate is one compatible reconstruction, never recovered original data.
    """
    values = row.values
    if values is None or not isinstance(distribution, list) or len(distribution) != values.n:
        return None
    candidate = []
    for number in distribution:
        if type(number) not in (int, float) or not values.min_val <= number <= values.max_val:
            return None
        try:
            if not math.isfinite(number):
                return None
        except OverflowError:
            return None
        value = Fraction(str(number)).limit_denominator(values.n_items)
        if float(value) != float(number) or (value * values.n_items).denominator != 1:
            return None
        if not values.min_val <= value <= values.max_val:
            return None
        candidate.append(value)
    mean = sum(candidate) / values.n
    variance = sum((value - mean) ** 2 for value in candidate) / (values.n - 1)
    printed_mean = Fraction(Decimal(row.cells["mean"]["value"].strip()))
    printed_sd = Fraction(Decimal(row.cells["sd"]["value"].strip()))
    mean_half = Fraction(1, 2 * 10**values.mean_prec)
    sd_half = Fraction(1, 2 * 10**values.sd_prec)
    if not printed_mean - mean_half <= mean <= printed_mean + mean_half:
        return None
    if not max(0, printed_sd - sd_half) ** 2 <= variance <= (printed_sd + sd_half) ** 2:
        return None
    histogram = [
        {"numerator": value.numerator, "denominator": value.denominator, "count": count}
        for value, count in sorted(Counter(candidate).items())
    ]
    return {
        "n": values.n,
        "mean": str(mean),
        "sample_variance": str(variance),
        "histogram": histogram,
    }


def _search(row: SpriteRow, settings: Settings, timeout: float) -> dict:
    task = {
        **asdict(row.values),
        "seed": settings.forensics_sprite_random_seed,
        "init_method": settings.forensics_sprite_init_method,
        "max_iter": settings.forensics_sprite_max_iterations,
    }
    started = time.monotonic()
    worker_env = os.environ.copy()
    for variable in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        worker_env[variable] = str(settings.forensics_sprite_math_threads)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.checks.forensics.sprite_worker"],
            input=json.dumps(task, allow_nan=False),
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=worker_env,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {
            "state": "deadline_reached",
            "worker_elapsed_seconds": time.monotonic() - started,
            "worker_timeout_seconds": timeout,
            "math_threads": settings.forensics_sprite_math_threads,
        }
    except OSError:
        return {"state": "worker_unavailable"}
    if result.returncode:
        return {"state": "library_error"}
    try:
        response = json.loads(result.stdout)
    except (ValueError, TypeError):
        return {"state": "invalid_worker_response"}
    if (
        not isinstance(response, dict)
        or type(response.get("seed")) is not int
        or response["seed"] != settings.forensics_sprite_random_seed
    ):
        return {"state": "invalid_worker_response"}
    provenance = {
        "library_version": response.get("library_version"),
        "seed": response["seed"],
        "init_method": settings.forensics_sprite_init_method,
        "max_iterations": settings.forensics_sprite_max_iterations,
        "math_threads": settings.forensics_sprite_math_threads,
        "worker_elapsed_seconds": time.monotonic() - started,
        "worker_startup_seconds": response.get("worker_startup_seconds"),
        "worker_search_seconds": response.get("worker_search_seconds"),
    }
    if response.get("status") != "candidate_found":
        state = response.get("status")
        return {
            "state": state
            if isinstance(state, str) and state in {"not_found_within_budget", "library_error"}
            else "invalid_worker_response",
            **provenance,
        }
    witness = validate_sprite_witness(response.get("distribution"), row)
    return {
        "state": "candidate_found" if witness is not None else "invalid_candidate",
        "candidate": witness,
        **provenance,
    }


def evaluate_sprite(extraction: ExtractionResult, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    deadline = time.monotonic() + settings.forensics_sprite_check_timeout_seconds
    policy = load_sprite_policy(settings.forensics_sprite_policy_file)
    rows = extract_sprite_rows(
        extraction,
        policy,
        max_rows=settings.forensics_sprite_max_input_rows,
        deadline=deadline,
        keywords=load_keywords(settings.forensics_keywords_file),
    )
    receipts = []
    single_item_excluded_tables = set()
    budget_unassessed_tables = set()
    considered = attempted = compatible = 0
    for row in rows:
        considered += 1
        if row.reason in NOT_ADMITTED_REASONS:
            budget_unassessed_tables.add(row.table_index)
        if single_item_incompatible(row, policy):
            single_item_excluded_tables.add(row.table_index)
        state = {"state": "unassessed", "reason": row.reason}
        if row.values is not None:
            if (
                row.values.n > settings.forensics_sprite_max_observations
                or (row.values.max_val - row.values.min_val) * row.values.n_items + 1
                > settings.forensics_sprite_max_scale_points
            ):
                state = {"state": "input_size_limit"}
            elif attempted >= settings.forensics_sprite_max_rows:
                state = {"state": "row_budget_reached"}
            elif time.monotonic() >= deadline:
                state = {"state": "check_deadline_reached"}
            else:
                attempted += 1
                state = _search(
                    row,
                    settings,
                    min(settings.forensics_sprite_row_timeout_seconds, deadline - time.monotonic()),
                )
        compatible += state["state"] == "candidate_found"
        if len(receipts) < settings.forensics_sprite_max_receipt_rows:
            receipts.append({**row.receipt(), **state})
    return {
        "version": "sprite_pipeline_v1",
        "library": "pysprite",
        "limits": {
            "math_threads": settings.forensics_sprite_math_threads,
            "row_timeout_seconds": settings.forensics_sprite_row_timeout_seconds,
            "check_timeout_seconds": settings.forensics_sprite_check_timeout_seconds,
            "max_iterations": settings.forensics_sprite_max_iterations,
        },
        "input_policy_version": policy["version"],
        "input_policy_sha256": hashlib.sha256(
            json.dumps(policy, sort_keys=True).encode()
        ).hexdigest(),
        "considered_rows": considered,
        "attempted_rows": attempted,
        "compatible_rows": compatible,
        "unassessed_rows": considered - compatible,
        "omitted_receipt_rows": considered - len(receipts),
        "single_item_excluded_tables": sorted(single_item_excluded_tables),
        # Tables SPRITE did not read because its row or time limit ran out
        # (not assessed, budget). GRIM still checks them.
        "budget_unassessed_tables": sorted(budget_unassessed_tables),
        "rows": receipts,
    }
