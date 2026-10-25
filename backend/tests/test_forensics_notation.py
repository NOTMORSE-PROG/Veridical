"""F6/V-081 BUG-260: source-bound notation, precision and incomplete coverage."""

import pytest
from scipy import stats as scipy_stats

from app.checks.forensics import extract as extraction_module
from app.checks.forensics.extract import extract_inferential_stats
from app.checks.forensics.pcheck import recompute_p
from app.checks.forensics.service import _compute_forensics_findings
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.models.enums import ResultOutcome


def block(text, *, page=7, paragraph=None, furniture=False):
    return TextBlock(
        text=text,
        page=page,
        paragraph=paragraph,
        max_font_size=11,
        bold_ratio=0,
        is_furniture=furniture,
    )


def document(*texts):
    return ExtractionResult(
        page_count=16,
        anchor_kind="page",
        image_only=False,
        text_chars=sum(map(len, texts)),
        section_tree=SectionTree(source="none"),
        blocks=[block(text) for text in texts],
        images=[],
    )


@pytest.mark.parametrize("p_token", [".011", " .011", "0.011", " 0.011"])
@pytest.mark.parametrize("degrees", ["(190.82)", " (df = 190.82)"])
def test_equivalent_source_precision_and_correct_p_remain_unflagged(p_token, degrees):
    text = f"t{degrees} = 2.58, p={p_token}"
    stats = extract_inferential_stats([block(text)])
    assert len(stats) == 1
    stat = stats[0]
    assert (stat.df2, stat.test_value, stat.p_value) == (190.82, 2.58, 0.011)
    assert (stat.test_value_precision, stat.p_value_precision) == (2, 3)
    assert stat.raw_text == text and stat.anchor == "p. 7"
    checked = recompute_p(stat, one_tailed_in_text=False)
    assert checked is not None and checked.computed_p == pytest.approx(
        2 * scipy_stats.t.sf(2.58, 190.82), rel=1e-12
    )
    assert checked.error is checked.decision_error is False
    assert _compute_forensics_findings(document(text)).flag_drafts == []


@pytest.mark.parametrize("degrees", ["(190.82)", " (df = 190.82)"])
def test_inconsistent_p_keeps_existing_high_decision_error(degrees):
    text = f"t{degrees} = 2.58, p=.500"
    result = _compute_forensics_findings(document(text))
    flags = [flag for flag in result.flag_drafts if flag.detail["kind"] == "p_value_decision_error"]
    assert len(flags) == 1 and flags[0].severity.value == "high"
    assert flags[0].evidence_excerpt == text and flags[0].page_anchor == "p. 7"


@pytest.mark.parametrize(
    "value,comparison,p", [("-2.58", "=", ".011"), ("2.58", "<", ".05"), ("-2.58", "<", ".05")]
)
def test_negative_and_p_inequality_source_is_preserved(value, comparison, p):
    text = f"t (df = 190.82) = {value}, p {comparison} {p}"
    stats = extract_inferential_stats([block(text, page=None, paragraph=8)])
    assert len(stats) == 1 and stats[0].test_value == float(value)
    assert stats[0].p_comparison == comparison and stats[0].raw_text == text
    assert stats[0].anchor == "\u00b68"
    assert not recompute_p(stats[0], one_tailed_in_text=False).error


def test_unicode_thousands_and_original_source_offsets_are_retained():
    original = "\u03c7\u00b2(2, N = 1,000) = 5.67, p = .059"
    text = "Prefix " + original + ". End."
    stats = extract_inferential_stats([block(text)])
    assert len(stats) == 1 and stats[0].test_type == "Chi2"
    stat = stats[0]
    assert stat.raw_text == original
    assert stat.source_start == len("Prefix ")
    assert text[stat.source_start : stat.source_end] == original
    assert stat.source_block_index == 0 and stat.p_value_precision == 3


def test_repeated_identical_results_bind_distinct_original_ranges():
    statement = "t (df = 190.82) = 2.58, p = .011"
    text = statement + "; next " + statement
    stats = extract_inferential_stats([block(text)])
    assert len(stats) == 2
    assert [stat.source_start for stat in stats] == [0, len(statement + "; next ")]
    assert all(text[stat.source_start : stat.source_end] == statement for stat in stats)


NARRATIVE = "The paired test gave t = 8.677, df = 12 and p = 0.000001427."


def test_observed_unsupported_notation_is_not_not_applicable():
    result = _compute_forensics_findings(document(NARRATIVE))
    assert result.n_inferential == 0 and result.flag_drafts == []
    assert result.outcome == ResultOutcome.escalated
    coverage = result.image_coverage
    assert coverage["coverage_reason"] == "statistical_notation_unassessed"
    assert coverage["analysis_coverage"] == "unsupported"
    assert coverage["statistical_notation_unassessed"] is True
    assert coverage["statistical_notation_coverage"]["version"] == "v1"
    items = coverage["statistical_notation_coverage"]["items"]
    assert items and all(
        NARRATIVE[item["source_start"] : item["source_end"]] == item["raw_text"] for item in items
    )


@pytest.mark.parametrize("p,expected_flags", [("0.011", 0), (".500", 1)])
def test_mixed_supported_and_unsupported_keeps_findings_and_partial_coverage(p, expected_flags):
    supported = f"t(190.82) = 2.58, p={p}"
    result = _compute_forensics_findings(document(supported + ". " + NARRATIVE))
    assert result.n_inferential == 1 and len(result.flag_drafts) == expected_flags
    assert result.outcome == ResultOutcome.escalated
    assert result.image_coverage["analysis_coverage"] == "partial"
    assert result.image_coverage["statistical_notation_unassessed"] is True


@pytest.mark.parametrize(
    "text",
    [
        "This chapter describes the findings and limitations.",
        "We will use paired t-tests and a threshold of p < .05.",
        "The value of p is a significance threshold.",
    ],
)
def test_prose_and_methods_do_not_invent_an_observed_result(text):
    result = _compute_forensics_findings(document(text))
    assert result.n_inferential == 0 and result.outcome == ResultOutcome.not_applicable
    assert result.flag_drafts == []
    assert result.image_coverage["statistical_notation_unassessed"] is False


def test_incomplete_result_is_unassessed_without_invented_p_value():
    result = _compute_forensics_findings(document("t (df = 190.82) = 2.58"))
    assert result.n_inferential == 0 and result.flag_drafts == []
    assert result.outcome == ResultOutcome.escalated


def test_library_value_not_supported_by_original_source_is_not_assessed(monkeypatch):
    original = extraction_module.extract_stats

    def changed_value(*args, **kwargs):
        rows = original(*args, **kwargs)
        rows["Reported_P_Value"] = 0.500
        return rows

    monkeypatch.setattr(extraction_module, "extract_stats", changed_value)
    result = _compute_forensics_findings(document("t(190.82) = 2.58, p=0.011"))
    assert result.n_inferential == 0 and result.flag_drafts == []
    assert result.outcome == ResultOutcome.escalated


def test_test_letter_inside_a_word_is_not_a_statistical_result():
    result = _compute_forensics_findings(document("The code printed cat(190.82) = 2.58, p=.500"))
    assert result.n_inferential == 0 and result.flag_drafts == []
    assert result.outcome == ResultOutcome.not_applicable


@pytest.mark.parametrize(
    "text",
    [
        "t(190.82)=2.58,p= .011²",
        "t(190.82)=2.58²,p=.011",
        "t(190.82)=2.58,p=.011^2",
        "t(190.82)=2.58,p=.011%",
    ],
)
def test_numeric_adornments_are_unassessed_instead_of_joined_digits(text):
    result = _compute_forensics_findings(document(text))
    assert result.n_inferential == 0 and not result.flag_drafts
    assert result.outcome == ResultOutcome.escalated
    items = result.image_coverage["statistical_notation_coverage"]["items"]
    assert items and items[0]["raw_text"] == text


def test_unsupported_notation_does_not_change_a_readable_two_tailed_finding():
    text = "A two-tailed test gave t(28)=2.45,p=.010"
    supported = _compute_forensics_findings(document(text))
    mixed = _compute_forensics_findings(document(text + ". " + NARRATIVE))
    assert len(supported.flag_drafts) == 1
    assert mixed.flag_drafts == supported.flag_drafts
    assert mixed.outcome == ResultOutcome.escalated


@pytest.mark.parametrize("text", ["t(0)=2.58,p=.011", "F(0,45)=3.10,p=.05"])
def test_invalid_degrees_do_not_establish_assessed_coverage(text):
    result = _compute_forensics_findings(document(text))
    assert not result.flag_drafts and result.outcome == ResultOutcome.escalated
    assert result.image_coverage["statistical_notation_unassessed"] is True


def test_unavailable_recalculation_retains_a_neutral_limit(monkeypatch):
    from app.checks.forensics import pcheck

    monkeypatch.setattr(pcheck, "recompute_p", lambda *args, **kwargs: None)
    result = _compute_forensics_findings(document("t(190.82)=2.58,p=.011"))
    assert result.n_inferential == 1 and not result.flag_drafts
    assert result.outcome == ResultOutcome.escalated
    assert result.image_coverage["analysis_coverage"] == "unsupported"


def test_unchanged_prose_mapping_does_not_allocate_an_integer_per_character():
    import tracemalloc

    from app.checks.forensics.notation import normalize_notation

    normalize_notation("warm policy cache")
    text = "a" * 16384
    tracemalloc.start()
    normalized = normalize_notation(text)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < len(text) * 4
    assert normalized.text == text and normalized.original_range(100, 300) == (100, 300)
