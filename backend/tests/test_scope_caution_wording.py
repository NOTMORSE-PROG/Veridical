"""BUG-608: a grade quoted passages cannot show is described for what it is.

A tone or an error rate is about the whole paper, not "an itemized assessment
of the relevant complete collection", and the evidence check did not fail on
it: it could not apply. Rows saved under the old wording read the new way.
"""

from types import SimpleNamespace

import pytest

from app.checks.support import _capability_failure, scope_wording
from app.models.enums import CheckKind, ResultOutcome
from app.report.automatic_grade import automatic_grade_caution
from app.report.service import _automatic_grade, scope_capabilities

OLD_REASON = (
    "The proposed rating requires an itemized assessment of the relevant complete collection. "
    "The saved manuscript quotations do not provide these assessment records."
)
OLD_CAUTION = (
    "The rating also depends on an itemized assessment of the relevant complete collection, which "
    "quoted passages cannot show. Check that yourself before relying on this grade."
)

TONE = SimpleNamespace(
    text=(
        "Tone: Professionalism and appropriateness of the writing tone for an academic "
        "research paper."
    ),
    evidence="A reader evaluates the diction and style of the text.",
    levels=[
        {
            "name": "BEGINNER",
            "level": 1,
            "points": 1.0,
            "descriptor": "The tone is unprofessional.",
        },
        {"name": "ACCEPTABLE", "level": 2, "points": 2.0, "descriptor": "Not always professional."},
        {
            "name": "PROFICIENT",
            "level": 3,
            "points": 3.0,
            "descriptor": (
                "The tone is generally professional. For the most part, it is appropriate."
            ),
        },
        {
            "name": "EXEMPLARY",
            "level": 4,
            "points": 4.0,
            "descriptor": "The tone is consistently professional and appropriate.",
        },
    ],
)
REFERENCES = SimpleNamespace(
    text="Every reference is complete",
    evidence=None,
    levels=[
        {"name": "Weak", "level": 1, "points": 1.0, "descriptor": "Entries lack fields."},
        {"name": "Strong", "level": 2, "points": 2.0, "descriptor": "Each entry is complete."},
    ],
)


def test_a_whole_paper_grade_is_described_as_about_the_whole_paper():
    reason, caution = scope_wording(["whole_paper_assessment_required"])
    assert "whole paper" in caution and "tone" in caution
    assert "itemized" not in caution and "collection" not in caution
    assert "did not assign" in reason


def test_a_set_and_an_external_fact_keep_their_own_words():
    assert "every reference" in scope_wording(["collection_assessment_required"])[1]
    assert "peer-reviewed" in scope_wording(["external_verification_required"])[1]
    both = scope_wording(["collection_assessment_required", "external_verification_required"])[1]
    assert " and " in both and "Check these yourself" in both


def test_the_whole_paper_wording_covers_a_set_matched_alongside_it():
    # "For the most part" matches the set wording too; the grade is described once.
    caution = scope_wording(["collection_assessment_required", "whole_paper_assessment_required"])[
        1
    ]
    assert "whole paper" in caution and "every reference" not in caution


def test_the_guard_writes_the_new_wording():
    batch = SimpleNamespace(label="whole document", scope_patterns_file=None)
    failure = _capability_failure(batch, TONE, {"verdict": "EXEMPLARY"})
    assert "whole paper" in failure["caution"]
    failure = _capability_failure(batch, REFERENCES, {"verdict": "Strong"})
    assert "every reference" in failure["caution"]


def saved_row(verdict, outcome=ResultOutcome.passed, **extra):
    detail = {
        "basis": "llm",
        "verdict": verdict,
        "votes": [verdict, verdict],
        "support_caution": OLD_CAUTION,
        "support_check": {
            "status": "unsupported",
            "scope_guard": "independent_assessment_required",
            "missing_capabilities": ["collection_assessment_required"],
            "reason": OLD_REASON,
        },
        **extra,
    }
    return SimpleNamespace(outcome=outcome, kind=CheckKind.semantic), detail


def test_a_grade_saved_under_the_old_wording_reads_the_new_way():
    result, detail = saved_row("EXEMPLARY")
    scope = scope_capabilities(TONE, detail)
    assert scope == ["whole_paper_assessment_required"]
    fields = _automatic_grade(result, detail, None, scope)
    assert fields["support_caution_scope"] is True
    assert "whole paper" in fields["support_caution"]
    assert "itemized" not in fields["support_caution"]


def test_the_stored_capabilities_are_the_fallback():
    result, detail = saved_row("Strong")
    unknown = SimpleNamespace(text="A named thing", evidence=None, levels=REFERENCES.levels[:1])
    assert scope_capabilities(unknown, detail) == ["collection_assessment_required"]


def test_an_unconfirmed_evidence_check_is_left_as_it_was():
    result, detail = saved_row("EXEMPLARY")
    detail["support_check"] = {"status": "unsupported", "reason": "The quote shows the topic only."}
    detail["support_caution"] = "The quote shows the topic only."
    assert scope_capabilities(TONE, detail) is None
    fields = _automatic_grade(result, detail, None, None)
    assert fields["support_caution"] == "The quote shows the topic only."
    assert fields["support_caution_scope"] is False


@pytest.mark.parametrize(
    ("scope", "prefix"),
    [(True, False), (False, True)],
)
def test_only_a_failed_check_is_called_not_confirmed(scope, prefix):
    row = SimpleNamespace(
        decision_basis=None,
        support_caution="This grade is about the whole paper.",
        support_caution_scope=scope,
        agreeing_passes=2,
        grading_passes=2,
    )
    caution = automatic_grade_caution(row)
    assert caution.startswith("The quote check did not support") is prefix
    assert "This grade is about the whole paper." in caution
