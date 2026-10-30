"""Rubric-level agreement with instructor labels: the arithmetic, the D-016
gate, and the refusal to score labels that did not come from an instructor."""

import csv
import math

import pytest

from app.checks.rubric_agreement import (
    AutomaticGrade,
    InstructorLabel,
    ProvenanceError,
    check_provenance,
    cohens_kappa_nominal,
    report_as_markdown,
    score,
    weighted_kappa,
)
from scripts import rubric_label_score

SCALE = [1, 2, 3, 4]
NAMES = ["Beginner", "Acceptable", "Proficient", "Exemplary"]


def _grade(i, level, *, decided=True, criterion="Tone", manuscript="M1", flagged=False):
    return AutomaticGrade(
        f"r{i}", manuscript, criterion, decided, level if decided else None, flagged
    )


def _label(i, level, *, who="Prof. Reyes", source="instructor", revealed=False, cannot=False):
    return InstructorLabel(f"r{i}", level, cannot, who, source, revealed)


def _score(grades, labels, min_n=35):
    return score(grades, labels, scale=SCALE, threshold=0.90, min_n=min_n)


def test_exact_and_within_one_with_wilson_intervals():
    grades = [_grade(0, 3), _grade(1, 3), _grade(2, 4), _grade(3, 1)]
    labels = [_label(0, 3), _label(1, 2), _label(2, 4), _label(3, 3)]
    report = _score(grades, labels)
    o = report.overall
    assert (o.n_decided, o.n_exact, o.n_within_one) == (4, 2, 3)
    assert o.exact == 0.5
    # Wilson 95% for 2/4, checked against the closed form
    assert o.exact_ci.low == pytest.approx(0.1500, abs=1e-4)
    assert o.exact_ci.high == pytest.approx(0.8500, abs=1e-4)
    assert o.mean_signed_difference == pytest.approx((0 + 1 + 0 - 2) / 4)


def test_escalations_leave_agreement_but_count_in_rate_and_end_to_end():
    grades = [_grade(0, 3), _grade(1, 3), _grade(2, None, decided=False)]
    labels = [_label(0, 3), _label(1, 3), _label(2, 1)]
    report = _score(grades, labels)
    assert report.overall.exact == 1.0
    assert report.overall.n_escalated == 1
    assert report.escalation_rate == pytest.approx(1 / 3)
    assert report.overall.end_to_end_exact == pytest.approx(2 / 3)


def test_cannot_judge_and_ungraded_rows_are_not_scored():
    grades = [_grade(0, 3), _grade(1, 3), _grade(2, 3)]
    labels = [
        _label(0, 3),
        _label(1, None, cannot=True),
        InstructorLabel("r2", None, False, "", ""),
    ]
    report = _score(grades, labels)
    assert report.overall.n_decided == 1
    assert report.n_cannot_judge == 1
    assert report.unlabelled_row_ids == ["r2"]


def test_kappa_matches_a_hand_computed_example():
    # 3x3 example: po = 0.7, pe = 0.34 -> kappa = 0.36/0.66
    pairs = [(1, 1)] * 2 + [(2, 2)] * 3 + [(3, 3)] * 2 + [(1, 2), (2, 3), (3, 1)]
    expected_pe = sum(
        (sum(a == c for a, _ in pairs) / 10) * (sum(b == c for _, b in pairs) / 10)
        for c in (1, 2, 3)
    )
    assert cohens_kappa_nominal(pairs) == pytest.approx((0.7 - expected_pe) / (1 - expected_pe))


def test_kappa_is_undefined_not_zero_when_both_raters_use_one_level():
    assert cohens_kappa_nominal([(3, 3)] * 10) is None
    assert weighted_kappa([(3, 3)] * 10, scale=SCALE) is None


def test_weighted_kappa_penalises_far_misses_more():
    near = [(1, 1), (2, 2), (3, 3), (4, 4), (3, 2), (2, 3)]
    far = [(1, 1), (2, 2), (3, 3), (4, 4), (4, 1), (1, 4)]
    assert weighted_kappa(near, scale=SCALE) > weighted_kappa(far, scale=SCALE)
    assert weighted_kappa([(1, 1), (2, 2), (3, 3), (4, 4)], scale=SCALE) == pytest.approx(1.0)


def test_gate_is_not_evaluable_below_minimum_n_even_when_perfect():
    grades = [_grade(i, 3) for i in range(20)]
    labels = [_label(i, 3) for i in range(20)]
    report = _score(grades, labels)
    assert report.overall.exact == 1.0
    assert report.gate.status == "insufficient"


def test_gate_uses_the_wilson_lower_bound_not_the_point_estimate():
    n = 40
    perfect = _score([_grade(i, 3) for i in range(n)], [_label(i, 3) for i in range(n)])
    assert perfect.gate.status == "pass"  # 40/40: lower bound about 0.912
    one_miss = _score(
        [_grade(i, 3) for i in range(n)], [_label(i, 3 if i else 2) for i in range(n)]
    )
    assert one_miss.overall.exact == pytest.approx(0.975)
    assert one_miss.gate.status == "fail"  # 39/40: lower bound about 0.871
    assert one_miss.gate.lower_bound < 0.90


def test_flagged_criteria_are_reported_with_and_without():
    grades = [_grade(0, 3, flagged=True), _grade(1, 3), _grade(2, 3)]
    labels = [_label(0, 1), _label(1, 3), _label(2, 3)]
    report = _score(grades, labels)
    assert report.overall.exact == pytest.approx(2 / 3)
    assert report.unflagged.exact == 1.0
    assert report.unflagged.n_decided == 2


def test_blind_subset_only_counts_rows_graded_before_the_reveal():
    grades = [_grade(0, 3), _grade(1, 3)]
    labels = [_label(0, 3, revealed=True), _label(1, 2, revealed=False)]
    report = _score(grades, labels)
    assert report.blind.n_decided == 1
    assert report.blind.exact == 0.0


def test_breakdowns_by_criterion_and_manuscript():
    grades = [
        _grade(0, 3, criterion="Tone", manuscript="A"),
        _grade(1, 3, criterion="Tone", manuscript="B"),
        _grade(2, 2, criterion="Purpose", manuscript="A"),
    ]
    labels = [_label(0, 3), _label(1, 4), _label(2, 2)]
    report = _score(grades, labels)
    by_c = {s.name: s for s in report.by_criterion}
    by_m = {s.name: s for s in report.by_manuscript}
    assert (by_c["Tone"].n_exact, by_c["Tone"].n_decided) == (1, 2)
    assert by_c["Purpose"].exact == 1.0
    assert (by_m["A"].n_exact, by_m["B"].n_exact) == (2, 0)
    assert [d.grade.row_id for d in report.disagreements] == ["r1"]


@pytest.mark.parametrize(
    "who,source",
    [
        ("Gemini", "instructor"),
        ("Prof. Reyes", "ai"),
        ("Prof. Reyes", "model-generated"),
        ("ChatGPT", "instructor"),
        ("", "instructor"),
        ("Prof. Reyes", ""),
        ("VERIDICAL auto", "instructor"),
    ],
)
def test_sheets_without_instructor_provenance_are_refused(who, source):
    with pytest.raises(ProvenanceError):
        check_provenance([_label(0, 3), _label(1, 3, who=who, source=source)])


def test_a_machine_source_on_an_ungraded_row_still_refuses_the_sheet():
    with pytest.raises(ProvenanceError):
        check_provenance([_label(0, 3), InstructorLabel("r1", None, False, "", "llm")])


def test_names_that_merely_contain_marker_letters_are_accepted():
    assert len(check_provenance([_label(0, 3, who="Kaira Aiko Bautista")])) == 1


def test_labels_off_the_scale_or_for_unknown_rows_are_rejected():
    with pytest.raises(ValueError):
        _score([_grade(0, 3)], [_label(0, 7)])
    with pytest.raises(ValueError):
        _score([_grade(0, 3)], [_label(9, 3)])


def test_markdown_states_the_gate_and_the_disagreements():
    report = _score([_grade(0, 3), _grade(1, 4)], [_label(0, 3), _label(1, 2)])
    text = report_as_markdown(report, level_names=dict(enumerate(NAMES, start=1)))
    assert "NOT EVALUABLE" in text
    assert "automatic **Exemplary**, instructor **Acceptable**" in text


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_cli_scores_a_sheet_and_refuses_a_machine_one(tmp_path, capsys):
    grades = tmp_path / "grades.csv"
    _write(
        grades,
        [
            {
                "row_id": "a",
                "manuscript": "M",
                "criterion": "Tone",
                "ai_outcome": "decided",
                "ai_level": "Proficient",
                "graded_against_misparsed_levels": "no",
            },
            {
                "row_id": "b",
                "manuscript": "M",
                "criterion": "Purpose",
                "ai_outcome": "escalated",
                "ai_level": "",
                "graded_against_misparsed_levels": "yes",
            },
        ],
    )
    labels = tmp_path / "labels.csv"
    base = {
        "instructor_rationale": "",
        "cannot_judge": "",
        "ai_revealed_before_label": "no",
        "labelled_by": "Prof. Reyes",
        "label_source": "instructor",
    }
    _write(
        labels,
        [
            {"row_id": "a", "instructor_level": "proficient", **base},
            {"row_id": "b", "instructor_level": "2", **base},
        ],
    )
    out = tmp_path / "report.md"
    code = rubric_label_score.main(
        [
            "--labels",
            str(labels),
            "--grades",
            str(grades),
            "--levels",
            ",".join(NAMES),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "Exact level agreement: 1/1" in text
    assert "Escalated to the instructor (no automatic grade): 1 of 2" in text

    _write(
        labels, [{"row_id": "a", "instructor_level": "Proficient", **base, "labelled_by": "gemini"}]
    )
    code = rubric_label_score.main(
        ["--labels", str(labels), "--grades", str(grades), "--levels", ",".join(NAMES)]
    )
    assert code == 2
    assert "REFUSED" in capsys.readouterr().err


def test_wilson_bounds_quoted_in_the_gate_comment_are_right():
    from app.checks.accuracy_stats import wilson_interval

    assert math.isclose(wilson_interval(40, 40).low, 0.912, abs_tol=0.001)
    assert math.isclose(wilson_interval(39, 40).low, 0.871, abs_tol=0.001)
