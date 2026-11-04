"""F6 table-local applicability and numerical recognition controls."""

import pytest
from docx import Document

from app.checks.forensics.extract import extract_inferential_stats
from app.checks.forensics.service import _compute_forensics_findings
from app.config import Settings
from app.ingest.docx import extract_document
from app.ingest.schemas import TextBlock


def document(tmp_path, parts):
    doc = Document()
    for part in parts:
        if isinstance(part, str):
            doc.add_paragraph(part)
        else:
            table = doc.add_table(rows=0, cols=len(part[0]))
            for row in part:
                for cell, value in zip(table.add_row().cells, row, strict=True):
                    cell.text = value
    path = tmp_path / "numerical.docx"
    doc.save(path)
    return extract_document(str(path), Settings(_env_file=None))


@pytest.mark.parametrize("alias", ["chi2", "Chi2", "CHI2", "χ²", "X2"])
def test_chi_alias_preserves_exact_source_range_and_decision(alias):
    expression = f"{alias}(df = 2) = 4.00, p = .001"
    text = f"A result: {expression}."
    block = TextBlock(text=text, paragraph=3, max_font_size=11, bold_ratio=0)
    stats = extract_inferential_stats([block])
    assert len(stats) == 1 and stats[0].test_type == "Chi2"
    stat = stats[0]
    assert text[stat.source_start : stat.source_end] == stat.raw_text == expression
    assert stat.p_value_precision == 3 and stat.anchor == "¶3"


def test_repeated_alias_with_thousands_and_explicit_df_retains_ownership():
    expression = "chi2(df = 2, N = 1,000) = 4.00, p = .135"
    text = f"Prefix {expression}; repeated {expression}. End."
    block = TextBlock(text=text, paragraph=3, max_font_size=11, bold_ratio=0)
    stats = extract_inferential_stats([block])
    assert len(stats) == 2
    assert stats[0].source_start != stats[1].source_start
    assert all(text[stat.source_start : stat.source_end] == expression for stat in stats)


def test_continuous_declaration_does_not_contaminate_following_integer_table(tmp_path):
    rows = [["Measure", "n", "Mean"], ["Response", "8", "3.29"]]
    extraction = document(
        tmp_path,
        [
            "Measurements are continuous lengths in centimeters, not integer ratings.",
            rows,
            "Each participant supplies exactly one integer rating from 1 to 5.",
            rows,
        ],
    )
    result = _compute_forensics_findings(extraction)
    flags = [flag for flag in result.flag_drafts if flag.detail["kind"] == "grim_inconsistent"]
    assert len(flags) == 1 and flags[0].page_anchor == f"¶{extraction.tables[1].paragraph}"
    scope = result.image_coverage["table_mean_applicability"]
    assert scope[0]["reason"] == "continuous" and scope[1]["reason"] is None
    assert scope[0]["sources"][0]["text"] in extraction.blocks[0].text


def test_declaration_is_not_borrowed_across_another_table(tmp_path):
    rows = [["Measure", "n", "Mean"], ["Response", "8", "3.29"]]
    extraction = document(tmp_path, ["Measurements are continuous lengths.", rows, rows])
    result = _compute_forensics_findings(extraction)
    assert result.image_coverage["table_mean_applicability"][1]["sources"] == []


@pytest.mark.parametrize("mean,expected", [("2.37", True), ("2.43", False)])
def test_own_caption_keeps_adjacent_integer_declaration_bound(tmp_path, mean, expected):
    declaration = "Each respondent contributes exactly one integer response."
    extraction = document(
        tmp_path,
        [
            declaration,
            "Table 8. Integer response summary",
            [["Response", "n", "Mean"], ["Rating", "7", mean]],
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert bool(result.flag_drafts) is expected
    assert result.image_coverage["numerical_assessment_counts"]["integer_mean_checked"] == 1
    receipt = result.image_coverage["table_mean_applicability"][0]
    assert receipt["state"] == "eligible"
    assert receipt["sources"] == [
        {"kind": "caption", "text": "Table 8. Integer response summary"},
        {"kind": "preceding_block", "paragraph": 0, "text": declaration},
    ]


def test_own_caption_keeps_complete_distribution_declaration_bound(tmp_path):
    extraction = document(
        tmp_path,
        [
            "Every respondent belongs to exactly one of the categories. All categories are shown.",
            "Table 8. Category distribution",
            [["Category", "Percentage (%)"], ["A", "22"], ["B", "34"], ["C", "38"]],
        ],
    )
    result = _compute_forensics_findings(extraction)
    [flag] = result.flag_drafts
    assert flag.detail["kind"] == "percentage_sum_off" and flag.detail["total"] == 94
    assert result.image_coverage["percentage_assessments"][0]["state"] == "assessed"


@pytest.mark.parametrize(
    "kind", ["formula", "duplicate", "ambiguous", "heading", "unbound", "omitted-before"]
)
def test_caption_metadata_requires_one_available_source_owner(tmp_path, kind):
    from tests.test_native_math_source import _math

    doc = Document()
    caption = "Table 6. This table reports one integer response per respondent."
    if kind != "unbound":
        if kind == "heading":
            doc.add_heading(caption, level=1)
        else:
            paragraph = doc.add_paragraph(caption)
            if kind in {"formula", "omitted-before"}:
                _math(paragraph)
    table = doc.add_table(rows=2, cols=3)
    for cells, values in zip(
        table.rows, [["Response", "n", "Mean"], ["Rating", "13", "4.37"]], strict=True
    ):
        for cell, value in zip(cells.cells, values, strict=True):
            cell.text = value
    if kind in {"duplicate", "omitted-before"}:
        doc.add_paragraph(caption)
    path = tmp_path / "caption-ownership.docx"
    doc.save(path)
    extraction = extract_document(str(path), Settings(_env_file=None))
    if kind == "unbound":
        extraction.tables[0].caption = caption
    if kind == "ambiguous":
        extraction.blocks.append(extraction.blocks[0].model_copy(update={"text": "Other text"}))
    result = _compute_forensics_findings(extraction)
    assert result.flag_drafts == []
    assert result.image_coverage["table_mean_applicability"][0]["state"] == "unassessed"


def test_generated_image_caption_cannot_declare_response_granularity(tmp_path):
    from tests.test_ingest_vision import SpyLLM, _pdf_with_images, _run

    path = _pdf_with_images(tmp_path, n_results_images=1)
    extraction, _ = _run(
        path,
        SpyLLM(
            [
                {
                    "kind": "table",
                    "confidence": "high",
                    "rows": [["Group", "n", "M"], ["A", "13", "4.37"]],
                    "caption": "This table reports one integer response per respondent.",
                }
            ]
        ),
    )
    result = _compute_forensics_findings(extraction)
    assert result.flag_drafts == []
    assert result.image_coverage["table_mean_applicability"][0]["state"] == "unassessed"


@pytest.mark.parametrize("barrier", ["table", "heading", "math", "ambiguous", "oversize"])
def test_caption_transparency_does_not_cross_source_barriers(tmp_path, barrier):
    from tests.test_native_math_source import _math

    doc = Document()
    doc.add_paragraph("Each respondent contributes exactly one integer response.")
    if barrier == "table":
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "Another table"
    elif barrier == "heading":
        doc.add_heading("Separate findings", level=1)
    elif barrier == "math":
        _math(doc.add_paragraph())
    doc.add_paragraph(
        "Table 8. " + "Context " * 300 if barrier == "oversize" else "Table 8. Summary"
    )
    table = doc.add_table(rows=2, cols=3)
    for cells, values in zip(
        table.rows, [["Response", "n", "Mean"], ["Rating", "7", "2.37"]], strict=True
    ):
        for cell, value in zip(cells.cells, values, strict=True):
            cell.text = value
    path = tmp_path / "barrier.docx"
    doc.save(path)
    extraction = extract_document(str(path), Settings(_env_file=None))
    if barrier == "ambiguous":
        extraction.blocks.append(extraction.blocks[-1].model_copy())
    result = _compute_forensics_findings(extraction)
    assert result.flag_drafts == []
    assert (
        result.image_coverage["table_mean_applicability"][len(extraction.tables) - 1]["state"]
        == "unassessed"
    )


@pytest.mark.parametrize("header", ["Percentage (%)", "Percent (%)", "%"])
def test_complete_distribution_far_from_100_is_checked_with_source_context(tmp_path, header):
    declaration = (
        "Every respondent belongs to exactly one of the categories below. "
        "All categories are shown. Percentages use the same denominator."
    )
    extraction = document(
        tmp_path,
        [
            declaration,
            [
                ["Category", header],
                ["A", "18"],
                ["B", "27"],
                ["C", "43"],
            ],
        ],
    )
    result = _compute_forensics_findings(extraction)
    flags = [flag for flag in result.flag_drafts if flag.detail["kind"] == "percentage_sum_off"]
    assert len(flags) == 1 and flags[0].detail["total"] == 88
    assert flags[0].detail["distribution_context"]["sources"][0]["text"] == declaration
    assert result.image_coverage["percentage_assessments"][0]["state"] == "assessed"


@pytest.mark.parametrize(
    "declaration",
    [
        "Respondents can select multiple answers. Categories overlap.",
        "Only selected categories are shown. Other categories are omitted.",
        "Independent success rates are reported for different groups.",
        "Select all that apply. Percentages reflect multiple responses.",
    ],
)
def test_near_whole_coincidence_cannot_override_distribution_veto(tmp_path, declaration):
    extraction = document(
        tmp_path,
        [
            declaration,
            [
                ["Category distribution", "Percentage (%)"],
                ["A", "45"],
                ["B", "52"],
            ],
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert not result.flag_drafts
    assert result.image_coverage["percentage_assessments"][0]["state"] == "excluded"


def test_uncued_near_whole_sum_is_unassessed(tmp_path):
    extraction = document(
        tmp_path,
        [
            [
                ["Category", "%"],
                ["A", "45"],
                ["B", "52"],
            ]
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert not result.flag_drafts and result.outcome.value == "escalated"
    assert result.image_coverage["percentage_assessments"][0]["state"] == "unassessed"


def test_incomplete_statistical_cells_cannot_produce_a_subtotal_warning(tmp_path):
    extraction = document(
        tmp_path,
        [
            [
                ["Category distribution", "%"],
                ["A", "45"],
                ["B", "unknown"],
                ["C", "30"],
            ]
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert not result.flag_drafts
    assert result.image_coverage["percentage_assessments"][0]["reason"] == "incomplete_table_cells"


def test_competing_mean_headers_are_not_silently_selected(tmp_path):
    extraction = document(
        tmp_path,
        [
            [
                ["Item", "n", "Mean", "Average"],
                ["Response", "8", "3.29", "3.25"],
            ]
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert result.n_descriptive == 0 and not result.flag_drafts
    assert result.outcome.value == "escalated"
    assert (
        result.image_coverage["descriptive_table_coverage"][0]["reason"]
        == "ambiguous_statistical_columns"
    )


def test_number_of_items_excludes_single_integer_assumption(tmp_path):
    extraction = document(
        tmp_path,
        [
            [
                ["Scale", "n", "Mean", "Number of items"],
                ["Composite", "8", "3.29", "3"],
            ]
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert not result.flag_drafts and result.skipped_composite_rows == 1


@pytest.mark.parametrize(
    "declaration",
    [
        "Lengths were measured in centimeters.",
        "The source does not state response granularity.",
        "The table does not summarize a single integer response per participant. "
        "It reports elapsed seconds.",
    ],
)
def test_unknown_granularity_is_unassessed_without_integer_warning(tmp_path, declaration):
    extraction = document(
        tmp_path,
        [
            declaration,
            [
                ["Measure", "n", "Mean"],
                ["Response", "8", "3.29"],
            ],
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert not result.flag_drafts and result.outcome.value == "escalated"
    assert result.image_coverage["table_mean_applicability"][0]["state"] == "unassessed"


@pytest.mark.parametrize("count", ["8.5", "0", "-2"])
def test_invalid_sample_size_is_never_truncated_into_a_mean_warning(tmp_path, count):
    extraction = document(
        tmp_path,
        [
            "Each participant supplies exactly one integer rating from 1 to 5.",
            [["Measure", "n", "Mean"], ["Response", count, "3.29"]],
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert not result.flag_drafts and result.outcome.value == "escalated"
    assert result.image_coverage["descriptive_table_coverage"][0]["reason"] == "invalid_sample_size"


@pytest.mark.parametrize("count", ["1", "1.0", "1.00"])
def test_equivalent_integer_item_declarations_have_identical_applicability(tmp_path, count):
    extraction = document(
        tmp_path,
        [
            [
                ["Item", "n", "Mean", "Number of items", "Response unit"],
                ["Response", "8", "3.29", count, "integer"],
            ]
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert result.image_coverage["table_mean_applicability"][0]["state"] == "eligible"
    assert any(flag.detail["kind"] == "grim_inconsistent" for flag in result.flag_drafts)


@pytest.mark.parametrize("count", ["1000000000", "100000"])
def test_large_sd_candidate_work_remains_unassessed_without_warning(tmp_path, count):
    extraction = document(
        tmp_path,
        [
            "Each participant supplies exactly one integer rating from 1 to 5.",
            [["Measure", "n", "Mean", "SD"], ["Response", count, "3", "1"]],
        ],
    )
    result = _compute_forensics_findings(extraction)
    assert not result.flag_drafts and result.outcome.value == "escalated"
    assert result.image_coverage["grimmer_unassessed"][0]["reason"] in {
        "observation_work_limit",
        "candidate_mean_work_limit",
    }
