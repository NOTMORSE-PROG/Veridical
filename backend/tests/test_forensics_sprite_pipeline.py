"""F6.3 application assembly, explicit assumptions and real bounded worker controls."""

import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.checks.forensics.service import _compute_forensics_findings
from app.checks.forensics.sprite import evaluate_sprite, validate_sprite_witness
from app.checks.forensics.sprite_inputs import extract_sprite_rows
from app.config import Settings
from app.ingest.schemas import ExtractionResult, SectionTree, TableBlock, TextBlock
from app.report.statistical_scope import statistical_check_scope, statistical_scope_copy

HEADERS = [
    "Measure",
    "n",
    "Mean",
    "SD",
    "Minimum possible item score",
    "Maximum possible item score",
    "Averaged item count",
    "Item response unit",
    "SD convention",
]
VALUES = ["Synthetic item", "10", "3.00", "0.00", "1", "5", "1", "integer", "sample"]


def extraction(rows=None, *, tables=None):
    return ExtractionResult(
        page_count=1,
        anchor_kind="page",
        image_only=False,
        text_chars=0,
        section_tree=SectionTree(source="none"),
        blocks=[],
        images=[],
        tables=tables
        if tables is not None
        else [TableBlock(page=1, rows=rows or [HEADERS, VALUES])],
    )


def test_real_pipeline_calls_library_and_checks_the_candidate_witness():
    result = _compute_forensics_findings(extraction())
    receipt = result.reconstruction
    assert receipt["attempted_rows"] == receipt["compatible_rows"] == 1
    assert receipt["unassessed_rows"] == 0
    row = receipt["rows"][0]
    assert row["library_version"] == "1.2.1"
    assert row["candidate"]["histogram"] == [{"numerator": 3, "denominator": 1, "count": 10}]
    assert row["cells"]["min_val"] == {"column": 4, "header": HEADERS[4], "value": "1"}
    assert row["table_index"] == 0 and row["row_index"] == 1
    assert not result.flag_drafts


def test_real_nonzero_sample_sd_candidate_is_independently_checked():
    values = VALUES.copy()
    values[3] = "1.05"
    result = evaluate_sprite(extraction([HEADERS, values]))
    assert result["compatible_rows"] == 1
    candidate = result["rows"][0]["candidate"]
    assert candidate["n"] == 10 and candidate["mean"] == "3"
    assert candidate["sample_variance"] == "10/9"


@pytest.mark.parametrize("declaration", ["two_items", "continuous_unit", "population_sd"])
def test_explicit_non_single_item_assumptions_never_feed_single_item_grimmer(declaration):
    values = VALUES.copy()
    values[3] = "0.53"
    values[{"two_items": 6, "continuous_unit": 7, "population_sd": 8}[declaration]] = {
        "two_items": "2",
        "continuous_unit": "hours",
        "population_sd": "population",
    }[declaration]
    with patch(
        "app.checks.forensics.sprite._search", return_value={"state": "not_found_within_budget"}
    ):
        result = _compute_forensics_findings(extraction([HEADERS, values]))
    assert result.skipped_composite_rows == 1
    assert not result.flag_drafts
    assert result.outcome.value == "escalated"


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        (1, "2.5", "integer_declaration_required"),
        (2, "NaN", "invalid_numeric_declaration"),
        (3, "0.000", "unsupported_sd_precision"),
        (4, "9", "invalid_declared_bounds"),
        (6, "0", "invalid_declared_bounds"),
        (7, "hours", "integer_item_unit_not_declared"),
        (8, "population", "sample_sd_not_declared"),
        (1, "9" * 1000, "invalid_numeric_declaration"),
    ],
)
def test_unknown_or_unsafe_assumptions_never_start_a_search(column, value, reason):
    values = VALUES.copy()
    values[column] = value
    with patch("app.checks.forensics.sprite._search") as search:
        result = evaluate_sprite(extraction([HEADERS, values]))
    search.assert_not_called()
    assert result["rows"][0]["reason"] == reason
    assert result["unassessed_rows"] == 1


def test_observed_minimum_and_other_table_declarations_are_not_scale_metadata():
    headers = HEADERS.copy()
    headers[4:6] = ["Minimum", "Maximum"]
    tables = [
        TableBlock(page=1, rows=[headers, VALUES]),
        TableBlock(
            page=1,
            rows=[["Minimum possible item score", "Maximum possible item score"], ["1", "5"]],
        ),
    ]
    with patch("app.checks.forensics.sprite._search") as search:
        result = evaluate_sprite(extraction(tables=tables))
    search.assert_not_called()
    assert result["considered_rows"] == 1
    assert result["rows"][0]["reason"] == "missing_declarations:max_val,min_val"


@pytest.mark.parametrize("header", ["Ave", "x̄"])
def test_existing_safe_mean_vocabulary_remains_admissible(header):
    headers = HEADERS.copy()
    headers[2] = header
    assert next(extract_sprite_rows(extraction([headers, VALUES]))).values is not None


@pytest.mark.parametrize("header", ["Weighted mean", "wm"])
def test_recognized_but_unsupported_descriptive_convention_remains_unassessed(header):
    headers = HEADERS.copy()
    headers[2] = header
    with patch("app.checks.forensics.sprite._search") as search:
        result = evaluate_sprite(extraction([headers, VALUES]))
    search.assert_not_called()
    assert result["considered_rows"] == result["unassessed_rows"] == 1
    assert result["rows"][0]["reason"] == "missing_declarations:mean"


def test_multiple_known_mean_conventions_cannot_silently_choose_one():
    with patch("app.checks.forensics.sprite._search") as search:
        result = evaluate_sprite(extraction([[*HEADERS, "Weighted mean"], [*VALUES, "4.00"]]))
    search.assert_not_called()
    assert result["rows"][0]["reason"] == "ambiguous_columns"


def test_duplicate_labels_and_tables_keep_actual_row_identity():
    source = extraction(
        tables=[
            TableBlock(page=1, rows=[HEADERS, VALUES, VALUES]),
            TableBlock(page=1, rows=[HEADERS, VALUES]),
        ]
    )
    rows = list(extract_sprite_rows(source))
    assert [(row.table_index, row.row_index) for row in rows] == [(0, 1), (0, 2), (1, 1)]


@pytest.mark.parametrize("vision", [False, True])
def test_ambiguous_or_vision_metadata_never_starts_a_search(vision):
    table = TableBlock(
        page=1,
        source="vision" if vision else "native",
        rows=[HEADERS if vision else [*HEADERS, "n"], VALUES if vision else [*VALUES, "10"]],
    )
    with patch("app.checks.forensics.sprite._search") as search:
        result = evaluate_sprite(extraction(tables=[table]))
    search.assert_not_called()
    assert result["rows"][0]["reason"] == (
        "native_table_required" if vision else "ambiguous_columns"
    )


@pytest.mark.parametrize(
    "distribution",
    [
        None,
        [3] * 9,
        [True] * 10,
        [3.5] * 10,
        [float("nan")] * 10,
        [8] * 10,
        [2] * 10,
        [10**400] * 10,
        [-(10**400)] * 10,
    ],
)
def test_untrusted_success_requires_an_independent_witness(distribution):
    assert validate_sprite_witness(distribution, next(extract_sprite_rows(extraction()))) is None


def test_witness_uses_printed_decimal_target_instead_of_float_coercion():
    row = next(extract_sprite_rows(extraction()))
    cells = {**row.cells, "mean": {**row.cells["mean"], "value": "3.00000000000000000001"}}
    changed = replace(row, cells=cells, values=replace(row.values, mean_prec=20))
    assert validate_sprite_witness([3] * 10, changed) is None


@pytest.mark.parametrize("status", [[], {}, ["candidate_found"], None, True, 1])
def test_malformed_worker_status_is_unassessed(status):
    response = {"status": status, "seed": 0, "library_version": "1.2.1"}
    with patch(
        "app.checks.forensics.sprite.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response)),
    ):
        result = evaluate_sprite(extraction())
    assert result["compatible_rows"] == 0
    assert result["unassessed_rows"] == 1
    assert result["rows"][0]["state"] == "invalid_worker_response"


def test_boolean_worker_seed_cannot_impersonate_integer_zero():
    response = {
        "status": "candidate_found",
        "seed": False,
        "library_version": "1.2.1",
        "distribution": [3] * 10,
    }
    with patch(
        "app.checks.forensics.sprite.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response)),
    ):
        result = evaluate_sprite(extraction())
    assert result["compatible_rows"] == 0
    assert result["rows"][0]["state"] == "invalid_worker_response"


@pytest.mark.parametrize("count", [True, "3", -1, 1.5, None, [], {}])
def test_present_malformed_legacy_quantities_never_silently_become_completed(count):
    scope = statistical_check_scope(["passed"], [{"n_descriptive_stats": count}])
    assert scope.state == "incomplete"
    assert scope.reconstruction.state == "not_recorded"


@pytest.mark.parametrize("change", ["wrong_state", "missing_row", "empty_candidate"])
def test_contradictory_reconstruction_receipts_do_not_create_a_candidate_claim(change):
    row = next(extract_sprite_rows(extraction()))
    saved_row = {
        **row.receipt(),
        "state": "candidate_found",
        "candidate": validate_sprite_witness([3] * 10, row),
    }
    saved = {
        "version": "sprite_pipeline_v1",
        "considered_rows": 1,
        "attempted_rows": 1,
        "compatible_rows": 1,
        "unassessed_rows": 0,
        "omitted_receipt_rows": 0,
        "rows": [saved_row],
    }
    if change == "wrong_state":
        saved_row["state"] = "not_found_within_budget"
    elif change == "missing_row":
        saved["rows"] = []
    else:
        saved_row["candidate"] = {}
    scope = statistical_check_scope(["passed"], [{"sprite_reconstruction": saved}])
    assert scope.state == "incomplete" and scope.reconstruction.state == "not_recorded"


def test_legitimately_capped_receipts_retain_safe_aggregate_counts():
    row = next(extract_sprite_rows(extraction()))
    saved = {
        "version": "sprite_pipeline_v1",
        "considered_rows": 2,
        "attempted_rows": 2,
        "compatible_rows": 2,
        "unassessed_rows": 0,
        "omitted_receipt_rows": 1,
        "rows": [
            {
                **row.receipt(),
                "state": "candidate_found",
                "candidate": validate_sprite_witness([3] * 10, row),
            }
        ],
    }
    scope = statistical_check_scope(["passed"], [{"sprite_reconstruction": saved}])
    assert scope.state == "completed" and scope.reconstruction.compatible_rows == 2


@pytest.mark.parametrize(
    "mode", ["invalid_json", "wrong_seed", "invalid_candidate", "not_found", "timeout"]
)
def test_worker_failures_are_unassessed_and_do_not_remove_other_findings(mode):
    source = extraction()
    source.blocks = [
        TextBlock(text="t(28) = 2.45, p = .001", page=1, max_font_size=11, bold_ratio=0)
    ]
    body = {
        "status": "candidate_found",
        "seed": 0,
        "library_version": "1.2.1",
        "distribution": [8] * 10,
    }
    if mode == "wrong_seed":
        body["seed"] = 1
    if mode == "not_found":
        body["status"] = "not_found_within_budget"
    completed = SimpleNamespace(
        returncode=0, stdout="broken" if mode == "invalid_json" else json.dumps(body)
    )
    with patch(
        "app.checks.forensics.sprite.subprocess.run",
        side_effect=subprocess.TimeoutExpired("worker", 1) if mode == "timeout" else None,
        return_value=completed,
    ):
        result = _compute_forensics_findings(source)
    assert result.reconstruction["unassessed_rows"] == 1
    assert result.outcome.value == "escalated"
    assert len(result.flag_drafts) == 1
    assert result.flag_drafts[0].detail["kind"] != "sprite_inconsistent"
    assert "possible rounding error or typo" in result.flag_drafts[0].detail["reason"].casefold()


def test_real_killable_worker_timeout_and_size_limit():
    tiny = Settings(forensics_sprite_row_timeout_seconds=0.001)
    result = evaluate_sprite(extraction(), tiny)
    assert result["rows"][0]["state"] == "deadline_reached"
    with patch("app.checks.forensics.sprite._search") as search:
        limited = evaluate_sprite(extraction(), Settings(forensics_sprite_max_observations=5))
    search.assert_not_called()
    assert limited["rows"][0]["state"] == "input_size_limit"


def test_public_scope_only_projects_safe_reconstruction_counts():
    detail = {"n_descriptive_stats": 3, "sprite_reconstruction": evaluate_sprite(extraction())}
    scope = statistical_check_scope(["passed"], [detail])
    assert scope.reconstruction.state == "candidate_found"
    public = scope.model_dump()
    assert "histogram" not in json.dumps(public) and "cells" not in json.dumps(public)
    assert "not recovered or verified original data" in statistical_scope_copy(scope)[1]
    legacy = statistical_check_scope(["passed"], [{"n_descriptive_stats": 3}])
    assert legacy.state == "incomplete" and legacy.reconstruction.state == "not_recorded"


def test_invalid_rows_share_the_admission_budget_and_tail_counts_stay_honest():
    import app.checks.forensics.sprite_inputs as inputs

    invalid = [*VALUES]
    invalid[1] = "unknown"
    source = extraction([HEADERS, *([invalid] * 5000)])
    with (
        patch.object(inputs, "_number", wraps=inputs._number) as parse,
        patch("app.checks.forensics.sprite._search") as search,
    ):
        result = evaluate_sprite(
            source, Settings(forensics_sprite_max_input_rows=2, forensics_sprite_max_receipt_rows=3)
        )
    search.assert_not_called()
    assert parse.call_count == 12
    assert result["considered_rows"] == result["unassessed_rows"] == 5000
    assert result["attempted_rows"] == 0
    assert result["omitted_receipt_rows"] == 4997
    assert len(result["rows"]) == 3
    assert result["rows"][2]["reason"] == "input_admission_budget_reached"
    assert result["rows"][2]["values"] is None


def test_child_scientific_thread_limit_does_not_change_parent_environment(monkeypatch):
    import os

    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "12")
    response = {
        "status": "candidate_found",
        "seed": 0,
        "library_version": "test-control",
        "distribution": [3] * 10,
        "worker_startup_seconds": 0.2,
        "worker_search_seconds": 0.01,
    }
    with patch(
        "app.checks.forensics.sprite.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response)),
    ) as run:
        result = evaluate_sprite(
            extraction(), Settings(_env_file=None, forensics_sprite_math_threads=1)
        )
    assert os.environ["OPENBLAS_NUM_THREADS"] == "12"
    child = run.call_args.kwargs["env"]
    for key in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        assert child[key] == "1"
    assert result["compatible_rows"] == 1
    assert result["rows"][0]["worker_startup_seconds"] == 0.2
    assert result["rows"][0]["worker_search_seconds"] == 0.01
    assert result["limits"]["math_threads"] == 1


def _plain_table(page, rows):
    return TableBlock(page=page, rows=[["Respondents", "N", "Mean"], *rows], source="native")


def test_grim_still_runs_on_tables_after_the_sprite_input_budget_is_used_up():
    # 60 earlier descriptive rows (distinct n, all GRIM-possible) use up the
    # default input budget; the impossible mean sits in a later table.
    early = [
        _plain_table(3, [[f"Group {i}", str(20 + i), f"{(2 * (20 + i) + 1) / (20 + i):.2f}"]])
        for i in range(60)
    ]
    late = _plain_table(90, [["Students", "10", "3.33"]])
    result = _compute_forensics_findings(extraction(tables=[*early, late]))
    kinds = [(f.detail["kind"], f.page_anchor) for f in result.flag_drafts]
    assert ("grim_inconsistent", "p. 90") in kinds
    assert result.skipped_composite_rows == 0
    # The tail is reported as not assessed by SPRITE, never as a multi-item table.
    assert result.reconstruction["single_item_excluded_tables"] == []
    assert result.reconstruction["budget_unassessed_tables"][-1] == 60


@pytest.mark.parametrize("past_budget", [False, True])
def test_declared_or_ambiguous_multi_item_tables_stay_excluded_with_or_without_budget(
    past_budget,
):
    filler = [
        _plain_table(3, [[f"G{i}", str(20 + i), f"{(2 * (20 + i) + 1) / (20 + i):.2f}"]])
        for i in range(60 if past_budget else 0)
    ]
    declared = [*VALUES]
    declared[1], declared[2], declared[6] = "10", "3.33", "3"
    two_means = TableBlock(
        page=95,
        rows=[["Item", "N", "Mean", "Weighted Mean"], ["Q1", "10", "3.33", "3.33"]],
        source="native",
    )
    for table in (TableBlock(page=91, rows=[HEADERS, declared]), two_means):
        result = _compute_forensics_findings(extraction(tables=[*filler, table]))
        assert not any(f.detail["kind"] == "grim_inconsistent" for f in result.flag_drafts)


def test_a_declared_multi_item_table_inside_the_budget_is_still_excluded_from_grim():
    values = [*VALUES]
    values[1] = "10"
    values[2] = "3.33"
    values[6] = "5"
    result = _compute_forensics_findings(extraction([HEADERS, values]))
    assert not any(f.detail["kind"] == "grim_inconsistent" for f in result.flag_drafts)
    assert result.skipped_composite_rows == 1
