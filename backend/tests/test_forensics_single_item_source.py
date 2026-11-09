"""Single-item declarations require explicit, unquoted native evidence."""

import pytest
from docx import Document

from app.checks.forensics.service import _compute_forensics_findings
from app.config import Settings
from app.ingest.docx import extract_document

DECLARATIONS = [
    "The table reports a single item with one integer rating per respondent "
    "on a scale from 1 to 5.",
    "The row reports one item with exactly one integer response per participant "
    "on a scale from 0 to 8.",
    "Each respondent supplies exactly one integer rating from 1 to 5.",
]


def extraction(tmp_path, declaration, *, style=None, unknown_style=False):
    document = Document()
    document.add_paragraph(declaration, style=style)
    table = document.add_table(rows=0, cols=3)
    for values in [["Item", "n", "Mean"], ["Response", "7", "4.10"]]:
        for cell, value in zip(table.add_row().cells, values, strict=True):
            cell.text = value
    path = tmp_path / "integer-item.docx"
    document.save(path)
    result = extract_document(str(path), Settings(_env_file=None))
    if unknown_style:
        result.blocks[0].paragraph_style = None
    return result


def grim_findings(result):
    return [flag for flag in result.flag_drafts if flag.detail["kind"] == "grim_inconsistent"]


@pytest.mark.parametrize("declaration", DECLARATIONS)
def test_explicit_single_item_wording_reaches_supported_arithmetic(tmp_path, declaration):
    result = _compute_forensics_findings(extraction(tmp_path, declaration))
    assert len(grim_findings(result)) == 1
    assert result.image_coverage["numerical_assessment_counts"]["integer_mean_checked"] == 1
    assert result.image_coverage["table_mean_applicability"][0]["state"] == "eligible"


@pytest.mark.parametrize("declaration", DECLARATIONS)
@pytest.mark.parametrize("protection", ["quote_style", "inline", "unknown_style"])
def test_protected_or_unknown_declaration_does_not_grant_integer_eligibility(
    tmp_path, declaration, protection
):
    source = extraction(
        tmp_path,
        f'"{declaration}"' if protection == "inline" else declaration,
        style="Quote" if protection == "quote_style" else None,
        unknown_style=protection == "unknown_style",
    )
    result = _compute_forensics_findings(source)
    assert grim_findings(result) == []
    assert result.image_coverage["table_mean_applicability"][0]["state"] == "unassessed"


def test_unrelated_quotation_does_not_hide_unquoted_declaration(tmp_path):
    source = extraction(tmp_path, 'The label is "response". ' + DECLARATIONS[0])
    assert len(grim_findings(_compute_forensics_findings(source))) == 1


@pytest.mark.parametrize(
    "qualification,reason",
    [
        ("Measurements are continuous.", "continuous"),
        ("A weighted mean is reported.", "weighted"),
        ("A composite mean is reported.", "composite"),
        ("These are non-integer responses.", "integer_granularity_unassessed"),
    ],
)
def test_explicit_wording_retains_exclusions_and_negation(tmp_path, qualification, reason):
    source = extraction(tmp_path, DECLARATIONS[0] + " " + qualification)
    result = _compute_forensics_findings(source)
    assert grim_findings(result) == []
    assert result.image_coverage["table_mean_applicability"][0]["reason"] == reason


def test_rating_without_explicit_integer_cardinality_remains_unassessed(tmp_path):
    result = _compute_forensics_findings(extraction(tmp_path, "The table reports ratings."))
    assert grim_findings(result) == []
    assert result.image_coverage["table_mean_applicability"][0]["state"] == "unassessed"
