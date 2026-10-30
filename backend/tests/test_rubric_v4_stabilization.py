"""V-081: unchanged experimental scale/checklist controls, isolated from legacy tests."""

import pytest

from app.rubric.schemas import ParsedCriterion, ParsedLevel
from app.rubric.validate import (
    attempt_decomposition,
    coverage_ratio,
    folded_requirements,
    level_scale_issues,
    run_gate,
)
from tests.test_rubric_validate import (
    BLOCKS,
    LOW_COVERAGE_RESPONSE,
    RAW_TEXT,
    SETTINGS,
    ScriptedLLM,
    _block,
    _criterion,
    _SpySession,
)


def _levelled(text: str, names: list[str], points: list[float] | None = None) -> ParsedCriterion:
    points = points or [float(i) for i in range(1, len(names) + 1)]
    levels = [
        ParsedLevel(level=i, name=name, descriptor=f"rung {i}", points=value)
        for i, (name, value) in enumerate(zip(names, points, strict=True), start=1)
    ]
    return ParsedCriterion(text=text, type="semantic", weight=10.0, levels=levels)


_FOUR = ["Beginner", "Acceptable", "Proficient", "Exemplary"]


def test_a_criterion_missing_a_rung_of_the_shared_scale_is_named():
    """BUG-391: TIP's "Use of references" came back without Exemplary while
    the other eight criteria kept all four rungs, and the gate accepted it."""
    criteria = [_levelled(f"Criterion {i}", _FOUR) for i in range(8)]
    criteria.append(_levelled("Use of references and attribution", _FOUR[:3]))
    issues = level_scale_issues(criteria)
    assert len(issues) == 1
    assert "Use of references and attribution" in issues[0]
    assert not run_gate([], criteria, SETTINGS).ok


def test_a_criterion_on_a_genuinely_different_scale_is_left_alone():
    criteria = [_levelled(f"Criterion {i}", _FOUR) for i in range(4)]
    criteria.append(_levelled("Overall impression", ["Low", "Medium", "High"], [0, 5, 10]))
    assert level_scale_issues(criteria) == []


def test_no_shared_scale_means_nothing_to_compare_against():
    criteria = [
        _levelled("A", _FOUR),
        _levelled("B", _FOUR[:3]),
        _levelled("C", ["Poor", "Good"], [0, 1]),
    ]
    assert level_scale_issues(criteria) == []


def _checklist(n: int) -> str:
    return "\n".join(f"\u2022 Requirement number {i} applies to every page" for i in range(n))


def test_a_checklist_folded_into_a_few_criteria_is_sent_back():
    """2026-10-09: a draft prompt folded MTSU's 44-item checklist into 7."""
    criteria = [_criterion(f"Broad rule {i}") for i in range(7)]
    reason = folded_requirements(criteria, _checklist(44), SETTINGS)
    assert reason is not None and "44 separate items" in reason
    assert not run_gate([], criteria, SETTINGS, _checklist(44)).ok


def test_one_criterion_per_item_and_short_lists_pass():
    assert (
        folded_requirements([_criterion(f"Rule {i}") for i in range(40)], _checklist(44), SETTINGS)
        is None
    )
    assert folded_requirements([_criterion("Rule")], _checklist(5), SETTINGS) is None


def test_a_rubric_table_without_list_markers_is_not_counted():
    criteria = [_criterion(f"Row {i}") for i in range(9)]
    assert folded_requirements(criteria, "CRITERIA\nIntroduction\nPurpose\n" * 20, SETTINGS) is None


def test_level_descriptors_marked_with_glyphs_are_not_folded_requirements():
    """2026-10-09: the UST grading form marks each of 4 level descriptors per
    row with a symbol-font box; 5 levelled criteria cover those 20 items."""
    source = "\n".join(" Descriptor of one level" for _ in range(18))
    criteria = [_levelled(f"Criterion {i}", _FOUR) for i in range(5)]
    assert folded_requirements(criteria, source, SETTINGS) is None


# --- backend review re-review, 2026-10-10 (each reproduced) --------------------


def test_a_different_point_scale_with_some_shared_names_is_left_alone():
    names = ["Poor", "Fair", "Good", "Excellent"]
    criteria = [_levelled(f"Criterion {i}", names) for i in range(4)]
    criteria.append(_levelled("Overall", ["Poor", "Good", "Excellent"], [0, 5, 10]))
    assert level_scale_issues(criteria) == []


async def test_after_failed_retries_the_best_attempt_is_kept_with_its_own_issues():
    """The last attempt can be the worst: a schema failure must not replace
    an earlier partial draft and its reasons."""
    llm = ScriptedLLM([LOW_COVERAGE_RESPONSE, {"criteria": []}, {"criteria": []}])
    outcome = await attempt_decomposition(_SpySession(), 1, BLOCKS, RAW_TEXT, llm, SETTINGS)
    assert outcome.needs_review
    assert outcome.criteria  # the partial draft, not an empty list
    assert any("too little" in issue for issue in outcome.issues)
    assert not any("validation" in issue.lower() for issue in outcome.issues)


def test_coverage_counts_the_level_descriptors_a_grid_keeps():
    criterion = ParsedCriterion(
        text="Thesis statement",
        type="semantic",
        weight=10.0,
        levels=[
            ParsedLevel(
                level=1, name="Limited", descriptor="vaguely developed statement", points=1
            ),
            ParsedLevel(level=2, name="Strong", descriptor="clearly argued statement", points=2),
        ],
    )
    blocks = [
        _block("Thesis statement"),
        _block("vaguely developed statement"),
        _block("clearly argued statement"),
    ]
    ratio = coverage_ratio(blocks, [criterion], min_line_chars=1, word_overlap_ratio=0.6)
    assert ratio == 1.0


@pytest.mark.parametrize(
    "names",
    [
        ["GRADE A", "GRADE B", "GRADE C"],
        ["Level I", "Level II", "Level IV"],
        ["Kulang pa", "Sapat", "Mahusay"],
        ["KULANG PA", "SAPAT NA", "MAHUSAY"],
    ],
)
def test_legitimate_wrapped_names_do_not_trigger_unsupported_word_repair(names):
    criteria = [_levelled("Introduction", names)]
    source = "\n".join(name.replace(" ", "\n") for name in names)
    assert run_gate([], criteria, SETTINGS, source).ok


async def test_retry_repairing_a_missing_rung_still_requires_source_confirmation():
    criteria = [_levelled(text, _FOUR) for text in ["Introduction", "Methods", "Results"]]
    partial = criteria + [_levelled("References", _FOUR[:3])]
    complete = criteria + [_levelled("References", _FOUR)]
    llm = ScriptedLLM(
        [
            {"criteria": [c.model_dump() for c in partial]},
            {"criteria": [c.model_dump() for c in complete]},
        ]
    )
    # Every level descriptor has to be the document's own words (BUG-566).
    rungs = "\n".join(f"rung {i}" for i in range(1, 5))
    outcome = await attempt_decomposition(_SpySession(), 1, [], rungs, llm, SETTINGS)
    assert outcome.attempts == 2
    assert outcome.needs_review
    assert any("earlier draft" in issue.lower() for issue in outcome.issues)
    assert len(outcome.criteria[-1].levels) == 4


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["1 - Beginner", "Acceptable: 2", "3 Proficient", "Exemplary 4"], _FOUR),
        (
            ["Level 1", "Level 2", "Level 3", "Level 4"],
            ["Level 1", "Level 2", "Level 3", "Level 4"],
        ),
    ],
)
async def test_point_cleanup_preserves_distinguishing_level_names(names, expected):
    from app.rubric.decompose import decompose_rubric

    criterion = _levelled("Introduction", names)
    result = await decompose_rubric("", ScriptedLLM([{"criteria": [criterion.model_dump()]}]))
    assert [level.name for level in result[0].levels] == expected
