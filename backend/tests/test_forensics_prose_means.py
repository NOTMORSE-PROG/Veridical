"""Integer mean checks must own their count, design, precision and native source."""

import fitz
import pytest
from docx import Document

from app.checks.forensics.prose_mean import load_prose_mean_policy
from app.checks.forensics.service import _compute_forensics_findings
from app.config import Settings
from app.ingest.docx import extract_document
from app.ingest.pdf import extract_document as extract_pdf
from app.models.enums import ResultOutcome


def source(tmp_path, *paragraphs, style=None, unknown_style=False):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text, style=style)
    path = tmp_path / "prose-values.docx"
    doc.save(path)
    result = extract_document(str(path), Settings(_env_file=None))
    if unknown_style:
        result.blocks[0].paragraph_style = None
    return result


def passage(count="Seven", mean="4.10"):
    return (
        f"{count} respondents each provided one integer rating from 1 to 5 "
        f"for a single item. The reported mean was {mean}."
    )


def findings(result):
    return [flag for flag in result.flag_drafts if flag.detail["kind"] == "grim_inconsistent"]


@pytest.mark.parametrize(
    "count,mean,warning",
    [
        ("Seven", "4.10", True),
        ("20", "4.10", False),
        ("Thirty", "4.10", False),
        ("Twenty-three", "4.10", True),
        ("twenty three", "4.10", True),
        ("12", "4.25", False),
    ],
)
def test_explicit_single_item_design_reaches_existing_arithmetic(tmp_path, count, mean, warning):
    text = passage(count, mean)
    result = _compute_forensics_findings(source(tmp_path, text))
    ledger = result.image_coverage["prose_mean_assessment"]
    assert (ledger["assessed"], ledger["unassessed"], ledger["excluded"]) == (1, 0, 0)
    assert len(findings(result)) == int(warning)
    assert result.image_coverage["numerical_assessment_counts"]["integer_mean_checked"] == 1
    assert result.image_coverage["statistical_notation_unassessed"] is False
    assert result.n_descriptive == 2
    if warning:
        flag = findings(result)[0]
        assert flag.evidence_excerpt == text
        assert flag.page_anchor == "¶0"
        assert flag.detail["prose_source"]["paragraph"] == 0
        for proof in ledger["records"][0]["evidence"]:
            assert proof["text"] == text[proof["source_start"] : proof["source_end"]]
            assert proof["quotation_status"] is False


@pytest.mark.parametrize(
    "mean,warning", [("4.1", False), ("4.10", True), ("4.100", True), ("4.1000", True)]
)
def test_printed_mean_precision_is_not_lost(tmp_path, mean, warning):
    result = _compute_forensics_findings(source(tmp_path, passage(mean=mean)))
    record = result.image_coverage["prose_mean_assessment"]["records"][0]
    assert record["mean_precision"] == len(mean.split(".")[1])
    assert len(findings(result)) == int(warning)


@pytest.mark.parametrize("protection", ["quote_style", "inline", "unknown_style"])
def test_quoted_or_unverified_design_does_not_create_arithmetic_warning(tmp_path, protection):
    text = passage()
    result = _compute_forensics_findings(
        source(
            tmp_path,
            f'"{text}"' if protection == "inline" else text,
            style="Quote" if protection == "quote_style" else None,
            unknown_style=protection == "unknown_style",
        )
    )
    ledger = result.image_coverage["prose_mean_assessment"]
    assert findings(result) == []
    assert ledger["assessed"] == 0
    assert ledger["records"][0]["state"] == (
        "unassessed" if protection == "unknown_style" else "excluded"
    )


@pytest.mark.parametrize(
    "qualification,reason",
    [
        ("Measurements are continuous values.", "continuous"),
        ("The result is a composite average.", "composite"),
        ("The result is a weighted mean.", "weighted"),
        ("This is not one integer response per respondent.", "negated_integer_design"),
        ("The average is over four items.", "composite"),
    ],
)
def test_explicit_design_veto_is_retained(tmp_path, qualification, reason):
    result = _compute_forensics_findings(source(tmp_path, passage() + " " + qualification))
    record = result.image_coverage["prose_mean_assessment"]["records"][0]
    assert record["state"] == "excluded"
    assert record["reason"] == reason
    assert findings(result) == []


def test_unrelated_quotation_does_not_replace_the_quantitative_proofs(tmp_path):
    result = _compute_forensics_findings(
        source(tmp_path, 'The display label is "response". ' + passage())
    )
    assert len(findings(result)) == 1


def test_separate_native_paragraphs_are_not_combined(tmp_path):
    result = _compute_forensics_findings(
        source(
            tmp_path,
            "Seven respondents each provided one integer rating for a single item.",
            "The reported mean was 4.10.",
        )
    )
    ledger = result.image_coverage["prose_mean_assessment"]
    assert ledger["assessed"] == 0
    assert ledger["unassessed"] == 2
    assert findings(result) == []
    assert result.outcome == ResultOutcome.escalated


def test_multiple_populations_in_one_block_remain_unassessed(tmp_path):
    result = _compute_forensics_findings(source(tmp_path, passage() + " " + passage("Twenty")))
    record = result.image_coverage["prose_mean_assessment"]["records"][0]
    assert record["state"] == "unassessed"
    assert record["reason"] == "missing_or_multiple_sample_sizes_or_means"
    assert findings(result) == []


@pytest.mark.parametrize("replacement", ["4.10e2", "4.10%", "4,100", "4.10/2"])
def test_numeric_suffixes_cannot_turn_a_partial_token_into_a_mean(tmp_path, replacement):
    result = _compute_forensics_findings(source(tmp_path, passage(mean=replacement)))
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 0
    assert findings(result) == []


def test_table_reference_cannot_duplicate_or_borrow_a_prose_mean(tmp_path):
    result = _compute_forensics_findings(
        source(tmp_path, "The table reports these measurements. " + passage())
    )
    record = result.image_coverage["prose_mean_assessment"]["records"][0]
    assert record["reason"] == "table_reference_requires_binding"
    assert findings(result) == []


def test_an_unrelated_percentage_does_not_disappear_from_coverage(tmp_path):
    result = _compute_forensics_findings(source(tmp_path, passage() + " Online accounted for 60%."))
    assert len(findings(result)) == 1
    assert result.image_coverage["statistical_notation_unassessed"] is True
    assert any(
        "60%" in row["raw_text"]
        for row in result.image_coverage["statistical_notation_coverage"]["items"]
    )


def test_sd_remains_explicitly_unassessed_while_supported_mean_runs(tmp_path):
    result = _compute_forensics_findings(source(tmp_path, passage("Twenty") + " SD = 0.50."))
    ledger = result.image_coverage["prose_mean_assessment"]
    assert ledger["assessed"] == 1 and ledger["unassessed"] == 1
    assert (
        ledger["records"][0]["unassessed_sd"][0]["reason"]
        == "prose_standard_deviation_not_supported"
    )
    assert result.outcome == ResultOutcome.escalated


def test_source_block_limit_is_an_explicit_nonassessment(tmp_path):
    text = passage() + " Background." * load_prose_mean_policy()["max_source_chars"]
    result = _compute_forensics_findings(source(tmp_path, text))
    assert (
        result.image_coverage["prose_mean_assessment"]["records"][0]["reason"]
        == "source_block_limit"
    )
    assert findings(result) == []


def test_receipt_limit_never_implies_complete_assessment(tmp_path):
    limit = load_prose_mean_policy()["max_receipts"]
    result = _compute_forensics_findings(source(tmp_path, *(passage() for _ in range(limit + 1))))
    ledger = result.image_coverage["prose_mean_assessment"]
    assert len(ledger["records"]) == limit
    assert ledger["omitted_blocks"] == 1 and ledger["omitted_reason"] == "receipt_limit"
    assert result.outcome == ResultOutcome.escalated


def test_identifiers_are_not_sample_size_assignments(tmp_path):
    result = _compute_forensics_findings(source(tmp_path, "Control N07 is a label.", passage()))
    assert len(result.image_coverage["prose_mean_assessment"]["records"]) == 1
    assert len(findings(result)) == 1


def test_native_line_wraps_do_not_change_word_count_or_design(tmp_path):
    result = _compute_forensics_findings(
        source(tmp_path, passage("twenty three").replace(" ", "\n"))
    )
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 1
    assert len(findings(result)) == 1
    assert findings(result)[0].detail["n"] == 23


def native_pdf(tmp_path, *, indented=False):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Background text for the manuscript margin.")
    page.insert_text((50, 80), "Further background text belongs to the same body margin.")
    page.insert_textbox(fitz.Rect(110 if indented else 50, 120, 450, 340), passage(), fontsize=11)
    path = tmp_path / "paragraph.pdf"
    doc.save(path)
    doc.close()
    return extract_pdf(str(path), Settings(_env_file=None))


def test_native_pdf_geometry_supports_the_existing_quote_check(tmp_path):
    result = _compute_forensics_findings(native_pdf(tmp_path))
    assert len(findings(result)) == 1
    assert findings(result)[0].page_anchor == "p. 1"
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 1


def test_indented_pdf_quotation_stays_excluded(tmp_path):
    result = _compute_forensics_findings(native_pdf(tmp_path, indented=True))
    assert findings(result) == []
    assert result.image_coverage["prose_mean_assessment"]["records"][0]["state"] == "excluded"


def test_pdf_without_geometry_keeps_unknown_quotation_provenance(tmp_path):
    extracted = native_pdf(tmp_path)
    extracted.blocks[-1].bbox = None
    result = _compute_forensics_findings(extracted)
    assert findings(result) == []
    assert (
        result.image_coverage["prose_mean_assessment"]["records"][0]["reason"]
        == "quotation_or_location_unverified"
    )


@pytest.mark.parametrize("suffix", [" to 4.30", "–4.30", " ± 0.20", "..4.30", " + 0.10"])
def test_a_mean_range_or_expression_is_not_reduced_to_its_first_number(tmp_path, suffix):
    result = _compute_forensics_findings(source(tmp_path, passage(mean="4.10" + suffix)))
    assert findings(result) == []
    assert (
        result.image_coverage["prose_mean_assessment"]["records"][0]["reason"]
        == "unsupported_mean_expression"
    )


def test_decimal_information_lost_by_the_existing_math_interface_stays_unassessed(tmp_path):
    result = _compute_forensics_findings(source(tmp_path, passage("Ten", "4.1000000000000001")))
    assert findings(result) == []
    assert result.image_coverage["prose_mean_assessment"]["records"][0]["state"] == "unassessed"
    assert result.image_coverage["numerical_assessment_counts"]["integer_mean_checked"] == 0


def test_a_trimmed_mean_is_not_treated_as_the_unweighted_sample_mean(tmp_path):
    result = _compute_forensics_findings(source(tmp_path, passage() + " This was a trimmed mean."))
    assert findings(result) == []
    assert (
        result.image_coverage["prose_mean_assessment"]["records"][0]["reason"]
        == "non_arithmetic_mean"
    )


def test_a_sample_count_without_a_reported_mean_or_design_is_not_a_mean_candidate(tmp_path):
    result = _compute_forensics_findings(source(tmp_path, "The sample size was 30 participants."))
    assert result.image_coverage["prose_mean_assessment"]["records"] == []
    assert result.outcome == ResultOutcome.not_applicable


def test_grouped_thousands_count_is_complete_and_preserves_a_valid_mean(tmp_path):
    text = (
        "The sample size was 7,000. Each respondent provided one integer rating "
        "for a single item. The reported mean was 4.10."
    )
    result = _compute_forensics_findings(source(tmp_path, text))
    record = result.image_coverage["prose_mean_assessment"]["records"][0]
    assert record["state"] == "assessed" and record["n"] == 7000 and record["consistent"]
    assert findings(result) == []


@pytest.mark.parametrize(
    "count",
    [
        "7/2",
        "7%",
        "7–9",
        "7 to 20",
        "7,00",
        "7.5",
        "seven hundred",
        "7 plus 20",
        "7 approximately",
        "1 007",
        "7 000",
        "7, 000",
        "7\u2009000",
        "7\u00a0000",
        "7;20",
    ],
)
def test_partial_or_qualified_sample_assignments_never_reach_arithmetic(tmp_path, count):
    text = (
        f"The sample size was {count}. Each respondent provided one integer rating "
        "for a single item. The reported mean was 4.10."
    )
    result = _compute_forensics_findings(source(tmp_path, text))
    assert findings(result) == []
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 0
    assert result.image_coverage["prose_mean_assessment"]["unassessed"] == 1


@pytest.mark.parametrize("assignment", ["n=7", "The sample size was 7 respondents", "n=7,000"])
def test_complete_sample_assignment_clause_preserves_the_exact_count(tmp_path, assignment):
    text = (
        f"{assignment}. Each respondent provided one integer rating "
        "for a single item. The reported mean was 4.10."
    )
    result = _compute_forensics_findings(source(tmp_path, text))
    record = result.image_coverage["prose_mean_assessment"]["records"][0]
    assert record["state"] == "assessed"
    assert record["n"] == (7000 if "7,000" in assignment else 7)
    assert len(findings(result)) == (0 if "7,000" in assignment else 1)


@pytest.mark.parametrize(
    "count",
    ["Between seven and 23", "7 to 20", "About seven", "At least 7", "Seven of 23", "minus seven"],
)
def test_population_ranges_and_approximations_cannot_supply_an_exact_count(tmp_path, count):
    result = _compute_forensics_findings(source(tmp_path, passage(count)))
    assert findings(result) == []
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 0
    assert result.outcome == ResultOutcome.escalated


@pytest.mark.parametrize(
    "design",
    [
        "Seven respondents were recruited; not every respondent provided one integer "
        "rating for a single item.",
        "Seven respondents each provided one integer rating, but not for a single item.",
        "If seven respondents each provided one integer rating for a single item, "
        "this calculation would apply.",
        "Seven respondents each provided one integer rating for a single item. "
        "Some responses were excluded.",
    ],
)
def test_negation_conditional_or_subset_context_does_not_grant_design_eligibility(tmp_path, design):
    result = _compute_forensics_findings(source(tmp_path, design + " The reported mean was 4.10."))
    assert findings(result) == []
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 0
    assert result.image_coverage["prose_mean_assessment"]["unassessed"] == 1


@pytest.mark.parametrize(
    "count", ["One hundred and seven", "One hundred seven", "1 007", "One million seven"]
)
def test_unsupported_complete_count_clause_is_not_reduced_to_a_trailing_number(tmp_path, count):
    result = _compute_forensics_findings(source(tmp_path, passage(count, "4.11")))
    assert findings(result) == []
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 0
    assert result.image_coverage["prose_mean_assessment"]["unassessed"] == 1


@pytest.mark.parametrize(
    "prefix", ["The approximate sample size was", "Approximately n=", "Approx. n="]
)
def test_qualification_before_a_count_label_remains_unassessed(tmp_path, prefix):
    text = (
        f"{prefix} 7. Each respondent provided one integer rating for a single item. "
        "The reported mean was 4.10."
    )
    result = _compute_forensics_findings(source(tmp_path, text))
    assert findings(result) == []
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 0


def test_ratio_sample_assignment_does_not_supply_its_first_number(tmp_path):
    text = (
        "The sample size was 7:20; each respondent provided one integer rating "
        "for a single item. The reported mean was 4.10."
    )
    result = _compute_forensics_findings(source(tmp_path, text))
    assert findings(result) == []
    assert (
        result.image_coverage["prose_mean_assessment"]["records"][0]["reason"]
        == "unsupported_or_qualified_sample_size"
    )


def test_a_planned_sample_size_is_not_an_observed_denominator(tmp_path):
    text = (
        "The sample size was 7. This was planned rather than observed. "
        "Each respondent provided one integer rating for a single item. "
        "The reported mean was 4.10."
    )
    result = _compute_forensics_findings(source(tmp_path, text))
    assert findings(result) == []
    assert result.image_coverage["prose_mean_assessment"]["assessed"] == 0
