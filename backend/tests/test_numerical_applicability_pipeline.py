"""F6 declaration controls retain the original undeclared numerical input."""

from fractions import Fraction

import docx

from tests.test_v4_seeded_regression import (
    _clean_tables as _clean_tables,
)
from tests.test_v4_seeded_regression import (
    _seed_and_run,
    _seeded_docx,
    live,
)
from tests.test_v4_seeded_regression import (
    scratch_url as scratch_url,
)
from tests.test_v4_seeded_regression import (
    session_factory as session_factory,
)

pytestmark = live


def _declared_document(tmp_path, declaration):
    original = _seeded_docx(tmp_path)
    document = docx.Document(original)
    caption = docx.text.paragraph.Paragraph(
        document.tables[0]._element.getprevious(), document._body
    )
    caption.insert_paragraph_before(declaration)
    path = tmp_path / "declared.docx"
    document.save(path)
    return str(path)


def _integer_document(tmp_path):
    return _declared_document(tmp_path, "Each respondent contributes exactly one integer response.")


def _continuous_document(tmp_path):
    return _declared_document(tmp_path, "Measurements are continuous lengths, not integer ratings.")


def _composite_document(tmp_path):
    return _declared_document(tmp_path, "The mean averages 3 questionnaire items per respondent.")


async def test_declared_integer_seed_keeps_all_four_detections(
    session_factory, tmp_path, monkeypatch
):
    _, flags = await _seed_and_run(session_factory, tmp_path, monkeypatch, _integer_document)
    kinds = {flag.detail.get("kind") for flag in flags}
    assert {
        "unverifiable_not_found",
        "retracted_source",
        "grim_inconsistent",
        "p_value_decision_error",
    } <= kinds


async def test_declared_integer_seed_retains_exact_arithmetic(
    session_factory, tmp_path, monkeypatch
):
    _, flags = await _seed_and_run(session_factory, tmp_path, monkeypatch, _integer_document)
    [flag] = [flag for flag in flags if flag.detail.get("kind") == "grim_inconsistent"]
    assert flag.detail["n"] == 10 and flag.detail["mean"] == 3.33
    lo, hi = (Fraction("3.33") - Fraction(1, 200)) * 10, (Fraction("3.33") + Fraction(1, 200)) * 10
    assert (lo, hi) == (Fraction(133, 4), Fraction(667, 20))
    assert not any(lo <= total < hi for total in range(0, 51))
    assert "10" in flag.evidence_excerpt and "3.33" in flag.evidence_excerpt
    assert flag.page_anchor


async def test_undeclared_original_mean_remains_unassessed(session_factory, tmp_path, monkeypatch):
    results, flags = await _seed_and_run(session_factory, tmp_path, monkeypatch, _seeded_docx)
    assert not any(flag.detail.get("kind") == "grim_inconsistent" for flag in flags)
    forensic = next(result for result in results if result.kind.value == "statistical_forensics")
    assert forensic.detail["numerical_assessment_counts"]["integer_mean_checked"] == 0
    [receipt] = forensic.detail["table_mean_applicability"].values()
    assert receipt["state"] == "unassessed"
    assert receipt["reason"] == "integer_granularity_unassessed"
    assert forensic.outcome.value == "escalated"


async def test_continuous_mean_does_not_receive_an_integer_warning(
    session_factory, tmp_path, monkeypatch
):
    results, flags = await _seed_and_run(
        session_factory, tmp_path, monkeypatch, _continuous_document
    )
    assert not any(flag.detail.get("kind") == "grim_inconsistent" for flag in flags)
    forensic = next(result for result in results if result.kind.value == "statistical_forensics")
    [receipt] = forensic.detail["table_mean_applicability"].values()
    assert receipt["state"] == "excluded" and receipt["reason"] == "continuous"
    assert forensic.detail["numerical_assessment_counts"]["integer_mean_checked"] == 0


async def test_multi_item_mean_does_not_receive_a_single_item_warning(
    session_factory, tmp_path, monkeypatch
):
    results, flags = await _seed_and_run(
        session_factory, tmp_path, monkeypatch, _composite_document
    )
    assert not any(flag.detail.get("kind") == "grim_inconsistent" for flag in flags)
    forensic = next(result for result in results if result.kind.value == "statistical_forensics")
    [receipt] = forensic.detail["table_mean_applicability"].values()
    assert receipt["state"] == "excluded" and receipt["reason"] == "composite"
    assert forensic.detail["numerical_assessment_counts"]["integer_mean_checked"] == 0
    assert round(Fraction(100, 30), 2) == Fraction(333, 100)
