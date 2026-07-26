"""V-054: simulated annotator perspectives.

These guard the property that makes the confidence signal mean anything: the
voting passes must read the same text under GENUINELY different stances. If
the stances ever collapse into each other, the agreement score silently goes
back to being a repeated sample dressed up as an ensemble.
"""

import json

import pytest

from app.checks.annotators import (
    ROLE_PASS_1,
    ROLE_PASS_2,
    ROLE_TIE_BREAK,
    AnnotatorPerspectiveError,
    load_perspectives,
    perspective_for,
)


def _write(tmp_path, perspectives):
    path = tmp_path / "perspectives.json"
    path.write_text(json.dumps({"perspectives": perspectives}), encoding="utf-8")
    return path


def test_packaged_file_defines_exactly_the_three_voting_roles():
    perspectives = load_perspectives()
    assert {p.role for p in perspectives} == {ROLE_PASS_1, ROLE_PASS_2, ROLE_TIE_BREAK}


def test_packaged_stances_are_actually_different():
    """The whole point. Two identical stances would be one judgement sampled
    twice — which is precisely the temperature-0 problem this replaces."""
    stances = [p.stance for p in load_perspectives()]
    assert len(set(stances)) == len(stances)
    assert all(len(s) > 40 for s in stances), "a stance must actually direct the reading"


def test_every_role_resolves_to_a_stance():
    for role in (ROLE_PASS_1, ROLE_PASS_2, ROLE_TIE_BREAK):
        assert perspective_for(role).stance


def test_duplicate_stances_are_rejected_not_quietly_accepted(tmp_path):
    same = "Read the document and decide."
    path = _write(
        tmp_path,
        [
            {"id": "a", "label": "A", "role": ROLE_PASS_1, "stance": same},
            {"id": "b", "label": "B", "role": ROLE_PASS_2, "stance": same},
            {"id": "c", "label": "C", "role": ROLE_TIE_BREAK, "stance": "Adjudicate."},
        ],
    )
    with pytest.raises(AnnotatorPerspectiveError, match="identical stance"):
        load_perspectives(path)


def test_a_missing_voting_role_is_an_error(tmp_path):
    path = _write(
        tmp_path,
        [
            {"id": "a", "label": "A", "role": ROLE_PASS_1, "stance": "Show me the text."},
            {"id": "b", "label": "B", "role": ROLE_PASS_2, "stance": "Read for substance."},
        ],
    )
    with pytest.raises(AnnotatorPerspectiveError, match="tie_break"):
        load_perspectives(path)


def test_blank_stance_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        [
            {"id": "a", "label": "A", "role": ROLE_PASS_1, "stance": "   "},
            {"id": "b", "label": "B", "role": ROLE_PASS_2, "stance": "Read for substance."},
            {"id": "c", "label": "C", "role": ROLE_TIE_BREAK, "stance": "Adjudicate."},
        ],
    )
    with pytest.raises(AnnotatorPerspectiveError, match="blank stance"):
        load_perspectives(path)


def test_v2_prompt_actually_consumes_the_stance_and_locks_evidence_first():
    """The prompt must (a) have a slot for the stance, or the perspectives do
    nothing, and (b) order the JSON keys evidence -> reasoning -> verdict, or
    the 'reasoning' is post-hoc rationalisation of an already-chosen label."""
    from app.checks.semantic import prompt_file_for

    text = prompt_file_for("v2").read_text(encoding="utf-8")
    assert "{annotator_stance}" in text
    shape = text[text.index('{"verdicts"') :]
    assert shape.index("evidence_quotes") < shape.index("reasoning") < shape.index('"verdict"')


def test_no_prompt_tells_the_model_to_return_an_empty_evidence_list():
    """REGRESSION (V-054). The v2 prompt originally said "use an empty list"
    when no supporting quote exists — but `GradeVerdict.evidence_quotes` is
    `min_length=1`, so every time the model obeyed, validation failed and the
    pass came back as a non-answer. It looked like principled escalation and
    was actually a broken prompt. Caught by the A/B instrument, not by a unit
    test, which is why this one now exists.
    """
    from app.checks.semantic import PROMPT_TYPE, prompt_file_for

    prompts = (prompt_file_for("v1").parent).glob(f"{PROMPT_TYPE}_*.txt")
    for path in prompts:
        text = path.read_text(encoding="utf-8").lower()
        assert "empty list" not in text, (
            f"{path.name} instructs an empty evidence list, which the schema rejects"
        )
