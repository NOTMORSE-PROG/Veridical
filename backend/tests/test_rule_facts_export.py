"""F3.2/F8: saved rule observations survive ordinary, pending and long print paths."""

from copy import deepcopy
from types import SimpleNamespace

import fitz
import pytest

from app.models.enums import CheckKind, CriterionType, ResultOutcome
from app.report.export import build_report_pdf
from app.report.judgments import _original
from app.report.rule_facts import project_rule_facts, rule_facts_presentation
from app.report.schemas import ResolutionOut
from app.report.service import _to_criterion_result
from app.share.service import _to_public_report
from tests.test_report_export_perf import _sixty_criterion_report


def _facts(detail, outcome="passed"):
    return project_rule_facts(
        detail, criterion_type="structural", kind="structural", outcome=outcome
    )


def _data():
    data = _sixty_criterion_report()
    data.flags = []
    data.report.results = data.report.results[:1]
    row = data.report.results[0]
    row.type = row.kind = "structural"
    row.basis = None
    row.resolution = None
    row.evidence = []
    row.reason = row.reasoning = None
    return data


def _text(pdf):
    with fitz.open(stream=pdf, filetype="pdf") as document:
        return " ".join(" ".join(page.get_text().split()) for page in document)


@pytest.mark.parametrize("instructor_recorded", [False, True])
def test_legacy_heading_scope_qualifies_saved_band_or_earlier_record_without_regrading(
    instructor_recorded,
):
    data = _data()
    row = data.report.results[0]
    row.rule_facts = _facts(
        {"rule_id": "required_section_present", "target": "abstract", "matched_title": "Abstract"}
    )
    row.rule_scope_unconfirmed = True
    if instructor_recorded:
        row.resolution = ResolutionOut(
            type="mark_fail", reason="I checked the full requirement.", ai_majority_verdict=None
        )
        row.outcome, row.score = "failed", 0
    before = data.model_dump_json()
    text = _text(build_report_pdf(data))
    assert (
        "The saved record establishes a heading lookup; "
        "coverage of the complete requirement is not established." in text
    )
    if instructor_recorded:
        assert "Earlier rule record." in text
        assert "I checked the full requirement." in text
        assert "Complete-requirement coverage is unconfirmed for 1" not in text
    else:
        assert "Saved readiness band" in text
        assert "Complete-requirement coverage is unconfirmed for 1 recorded rule result." in text
    assert data.model_dump_json() == before


def test_private_original_and_public_fact_allowlists_match_without_mutating_saved_detail():
    detail = {
        "rule_id": "required_section_present",
        "target": "abstract",
        "matched_title": "ABSTRACT",
        "resolution": {
            "type": "mark_pass",
            "reason": "PRIVATE_INSTRUCTOR_REASON",
            "ai_majority_verdict": None,
        },
        "original_result": {"outcome": "passed", "score": 100},
        "provider": "PRIVATE_PROVIDER",
        "xml_path": "PRIVATE_PATH",
        "matched_ratio": 0.99,
    }
    saved = deepcopy(detail)
    result = SimpleNamespace(
        id=1,
        judgment_revision=1,
        kind=CheckKind.structural,
        outcome=ResultOutcome.passed,
        score=100,
        detail=detail,
    )
    criterion = SimpleNamespace(
        id=2, text="Actual criterion", type=CriterionType.structural, weight=1
    )
    private = _to_criterion_result(result, criterion, importance="med")
    original = _original(result, criterion)
    data = _data()
    data.report.results = [private]
    public = _to_public_report(data.report)
    assert private.rule_facts == original.rule_facts == public.results[0].rule_facts
    payload = public.model_dump_json()
    assert "PRIVATE_" not in payload
    assert public.results[0].resolved
    assert detail == saved


def test_all_structural_families_keep_the_same_observation_text_in_print():
    saved = [
        {"rule_id": "required_section_present", "target": "abstract", "matched_title": "ABSTRACT"},
        {
            "rule_id": "section_order",
            "checked_sections": ["Chapter <one> & study", "Chapter two", "Chapter two"],
        },
        {
            "rule_id": "page_limit",
            "actual_pages": 10,
            "expected": {"kind": "range", "low": 9.5, "high": 10.5},
        },
        {
            "rule_id": "reference_count_min_max",
            "actual_count": 0,
            "expected": {"kind": "exact", "low": 0.0},
        },
        {"rule_id": "table_formatting_presence", "total_tables": 2, "captioned": 1},
        {"rule_id": "margins_spacing", "checked_pages_or_sections": 2},
        {"rule_id": "citation_style_sniff", "matched_ratio": 0.9, "threshold": 0.5},
    ]
    data = _data()
    template = data.report.results[0]
    data.report.results = [
        template.model_copy(
            update={
                "criterion_id": i + 1,
                "check_result_id": i + 1,
                "text": f"Saved requirement {i + 1}",
                "rule_facts": _facts(detail),
            }
        )
        for i, detail in enumerate(saved)
    ]
    before = data.model_dump_json()
    text = _text(build_report_pdf(data))
    for row in data.report.results:
        paragraphs, headings = rule_facts_presentation(row.rule_facts)
        for phrase in paragraphs + headings:
            assert " ".join(phrase.split()) in text
    assert data.model_dump_json() == before
    assert text.count("Chapter two") == 2


def test_pending_and_instructor_resolved_facts_retain_their_distinct_relationships():
    data = _data()
    template = data.report.results[0]
    pending = template.model_copy(
        update={
            "criterion_id": 201,
            "check_result_id": 201,
            "text": "PENDING_GLOSSARY",
            "outcome": "escalated",
            "score": None,
            "reason": "The heading lookup did not locate this section.",
            "rule_facts": _facts(
                {"rule_id": "required_section_present", "target": "glossary"}, "escalated"
            ),
        }
    )
    resolved = template.model_copy(
        update={
            "criterion_id": 202,
            "check_result_id": 202,
            "text": "RESOLVED_ABSTRACT",
            "resolution": ResolutionOut(
                type="mark_fail",
                reason="I reviewed the full manuscript and recorded a different grade.",
                ai_majority_verdict=None,
            ),
            "rule_facts": _facts(
                {
                    "rule_id": "required_section_present",
                    "target": "abstract",
                    "matched_title": "ABSTRACT",
                }
            ),
        }
    )
    data.report.results = [pending, resolved]
    data.report.decision = None
    pdf = build_report_pdf(data)
    text = _text(pdf)
    detail_text = text.split("Needs Your Review", 1)[1]
    assert detail_text.count("PENDING_GLOSSARY") == 1
    assert 'The saved heading lookup searched for "glossary".' in text
    assert "Earlier recorded rule facts" in text
    assert "Criterion #202: RESOLVED_ABSTRACT" in text
    assert 'The saved heading lookup matched "ABSTRACT" for "abstract".' in text
    assert "I reviewed the full manuscript and recorded a different grade." in text
    assert "Not yet resolved" in text
    with fitz.open(stream=pdf, filetype="pdf") as document:
        for page in document:
            page_text = page.get_text()
            if "Earlier recorded rule facts" in page_text:
                assert "RESOLVED_ABSTRACT" in page_text or "Criterion #202 (continued)" in page_text


def test_long_order_facts_split_without_losing_or_reordering_saved_headings():
    data = _data()
    row = data.report.results[0]
    headings = [f"HEADING_{i:03d} " + "Saved section title. " * 8 for i in range(100)]
    row.rule_facts = _facts({"rule_id": "section_order", "checked_sections": headings})
    row.text = "LONG_ORDER_REQUIREMENT"
    text = _text(build_report_pdf(data))
    offsets = [text.index(f"HEADING_{i:03d}") for i in range(100)]
    assert offsets == sorted(offsets)
    assert all(text.count(f"HEADING_{i:03d}") == 1 for i in range(100))
    assert "Saved section title." in text
