"""V-081: a portable report must retain the saved finding, not only its excerpt."""

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import fitz
import pytest

from app.flags.schemas import FlagOut
from app.flags.service import _to_flag_out
from app.models.enums import CheckKind
from app.report.export import _problem_labels, build_report_pdf
from app.report.export_data import flag_export_detail, unassessed_references
from app.report.schemas import EvidenceItem, ReportExportData
from tests.test_report_export_perf import _MAX_SECONDS, _sixty_criterion_report


def _data(**updates):
    payload = _sixty_criterion_report().model_dump()
    payload["flags"] = payload["flags"][:1]
    payload.update(updates)
    return ReportExportData.model_validate(payload)


def _text(pdf):
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return " ".join(" ".join(page.get_text().split()) for page in doc)


def test_optional_comparison_export_keeps_all_records_and_qualified_continuations():
    data = _data()
    template = data.flags[0]
    data.flags = [
        template.model_copy(
            update={
                "id": record_id,
                "check_kind": "originality_reuse",
                "affects_readiness": False,
                "severity": "high",
                "evidence_excerpt": f"RECORD_{record_id}_START "
                + ("Saved source passage. " * 400 if record_id == 41 else "Saved source passage. ")
                + f"RECORD_{record_id}_END",
            }
        )
        for record_id in reversed(range(39, 70))
    ]
    data.flag_details = {}
    text = _text(build_report_pdf(data))
    assert "Other integrity records: 0. Optional comparison leads: 31." in text
    assert "Optional comparison leads (31)" in text
    assert "High severity" not in text
    for ordinal, record_id in enumerate(range(39, 70), start=1):
        assert f"Comparison lead {ordinal} of 31. Record #{record_id}:" in text
        assert f"RECORD_{record_id}_START" in text
        assert f"RECORD_{record_id}_END" in text
    assert "Comparison lead 3 of 31. Record #41 (continued)" in text
    assert "Records can overlap or cover different scopes." in text


def test_other_non_scoring_check_does_not_become_a_comparison_lead():
    data = _data()
    data.flags[0].check_kind = "internal_agreement"
    data.flags[0].affects_readiness = False
    text = _text(build_report_pdf(data))
    assert "Other integrity records: 1. Optional comparison leads: 0." in text
    assert "Finding 1:" in text
    assert "Not scored" in text
    assert "Comparison lead: not scored" not in text
    assert "Comparison lead 1 of" not in text


def test_export_keeps_recorded_issue_reason_identifier_and_resolution():
    data = _data(
        flag_details={
            0: {
                "issue": "unverifiable_not_found",
                "reasoning": "The stored title was not found in the databases checked.",
                "source_identifier": "doi: 10.1234/example",
                "override_reason": "The instructor checked the publisher record.",
                "annotation": "PRIVATE_NOTE_MUST_NOT_APPEAR",
            }
        }
    )
    data.flags[0].overridden = True
    text = _text(build_report_pdf(data))
    assert "Source not found in the databases checked" in text
    assert "The stored title was not found in the databases checked." in text
    assert "doi: 10.1234/example" in text
    assert "The instructor checked the publisher record." in text
    assert "PRIVATE_NOTE_MUST_NOT_APPEAR" not in text


def test_export_retains_comparisons_and_unassessed_reference_notices():
    data = _data(
        flag_details={
            0: {
                "issue": "p_value_mismatch",
                "reasoning": "The reported and recalculated values differ.",
                "comparisons": [
                    {"label": "Reported p-value", "text": "p = .03"},
                    {"label": "Recalculated p-value", "text": "p = .14"},
                    {"label": "Objective", "text": "Measure completion time.", "anchor": "¶12"},
                    {"label": "Recorded result", "text": "Participants liked the interface."},
                    {"label": "This manuscript", "text": "OWN_PASSAGE", "anchor": "p. 5"},
                    {"label": "Library match 31", "text": "MATCHED_PASSAGE"},
                ],
            }
        },
        unassessed_references=[
            {
                "reference_order_index": 8,
                "status": "unassessed",
                "reason": "ambiguous_identifier_wrap",
                "lookup_attempted": False,
            },
            {"reference_order_index": 9, "status": "api_down", "lookup_attempted": True},
        ],
    )
    text = _text(build_report_pdf(data))
    for expected in (
        "Reported p-value: p = .03",
        "Recalculated p-value: p = .14",
        "Measure completion time.",
        "Participants liked the interface.",
        "OWN_PASSAGE",
        "MATCHED_PASSAGE",
        "Reference entry 9",
        "Reference entry 10",
        "No lookup was attempted",
        "external service",
    ):
        assert expected in text


def test_export_discloses_missing_historical_reason_instead_of_inventing_one():
    text = _text(build_report_pdf(_data()))
    assert "No separate explanation was saved for this finding." in text


def test_export_names_title_comparison_uncertainty_without_claiming_a_parse_failure():
    data = _data(
        unassessed_references=[
            {
                "reference_order_index": 1,
                "status": "unassessed",
                "reason": "title_hyphenation_unassessed",
                "lookup_attempted": True,
            }
        ]
    )
    text = _text(build_report_pdf(data))
    assert "Hyphenation differs between the reference title and registry title" in text
    assert "The title comparison remains unassessed" in text
    assert "Some reference fields were not reliably parsed" not in text


def test_docx_layout_note_is_once_but_missing_source_is_not_suppressed():
    data = _data(
        source_format="docx",
        page_images={
            0: {
                "image_png": None,
                "page": None,
                "reason": "REPEATED_LAYOUT_NOTICE",
                "reason_code": "docx_layout",
            }
        },
    )
    text = _text(build_report_pdf(data))
    assert text.count("was submitted as a DOCX") == 1
    assert "REPEATED_LAYOUT_NOTICE" not in text
    data.page_images[0].reason_code = None
    data.page_images[0].reason = "The manuscript source was purged."
    assert "The manuscript source was purged." in _text(build_report_pdf(data))


def test_long_evidence_and_reason_can_span_pages_without_loss():
    reason = "REASON_START " + "This recorded reason has supporting context. " * 400 + " REASON_END"
    excerpt = "SOURCE_START " + "This source excerpt is long. " * 400 + " SOURCE_END"
    data = _data(flag_details={0: {"issue": "agreement_partial", "reasoning": reason}})
    data.flags[0].evidence_excerpt = excerpt
    pdf = build_report_pdf(data)
    text = _text(pdf)
    for marker in ("REASON_START", "REASON_END", "SOURCE_START", "SOURCE_END"):
        assert marker in text
    assert "Finding 1 (continued)" in text


def test_pending_ai_basis_is_retained_escaped_and_splittable_without_claiming_a_verdict():
    data = _data()
    data.report.decision = None
    row = data.report.results[0]
    row.kind = "semantic"
    row.basis = "llm"
    row.outcome = "escalated"
    row.resolution = None
    row.reasoning = (
        "BASIS_START <source> & analysis " + "Saved explanatory context. " * 500 + " BASIS_END"
    )
    row.evidence = [
        EvidenceItem(
            quote="SOURCE_START <exact> & literal " + "Recorded text. " * 500 + " SOURCE_END",
            anchor="paragraph 57",
        )
    ]
    text = _text(build_report_pdf(data))
    for marker in (
        "BASIS_START <source> & analysis",
        "BASIS_END",
        "SOURCE_START <exact> & literal",
        "SOURCE_END",
        "Extracted paragraph 58",
    ):
        assert marker in text
    assert "A recorded location does not establish support for a grade." in text
    assert "No grade has been accepted for this criterion." in text
    assert "AI reached no verdict for this criterion." not in text


@pytest.mark.parametrize("requirement_phrases", [400, 1200])
def test_long_pending_requirement_finishes_without_repeating_it_in_continuation_headers(
    requirement_phrases,
):
    # A continuation header taller than its frame previously prevented useful
    # pagination progress. Isolate the renderer so a recurrence fails boundedly.
    script = dedent("""
        import json
        import sys
        import fitz
        from app.report.export import build_report_pdf
        from app.report.schemas import EvidenceItem
        from tests.test_report_export_details import _data

        count = int(sys.argv[1])
        data = _data()
        row = data.report.results[0]
        row.kind = 'semantic'
        row.basis = 'llm'
        row.outcome = 'escalated'
        row.resolution = None
        row.text = 'CRITERION_START <literal> & ' + 'Requirement text. ' * count + 'CRITERION_END'
        row.reasoning = 'REASON_START ' + 'Explanatory support. ' * 700 + 'REASON_END'
        row.evidence = [EvidenceItem(
            quote='EVIDENCE_START ' + 'Saved evidence. ' * 700 + 'EVIDENCE_END',
            anchor='paragraph 1',
        )]
        before = data.model_dump_json()
        with fitz.open(stream=build_report_pdf(data), filetype='pdf') as document:
            text = ' '.join(' '.join(page.get_text().split()) for page in document)
            assert all(marker in text for marker in (
                'CRITERION_START <literal> &', 'CRITERION_END',
                'REASON_START', 'REASON_END', 'EVIDENCE_START', 'EVIDENCE_END',
            ))
            assert text.count('Requirement text.') == count
            assert text.count('CRITERION_START') == 1
            assert f'Criterion #{row.criterion_id} (continued)' in text
        assert data.model_dump_json() == before
        print(json.dumps({'all_content_retained': True, 'input_unchanged': True}))
    """)
    completed = subprocess.run(
        [sys.executable, "-c", script, str(requirement_phrases)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
        timeout=_MAX_SECONDS,
    )
    assert json.loads(completed.stdout) == {
        "all_content_retained": True,
        "input_unchanged": True,
    }


@pytest.mark.parametrize("preface_length", [20, 30, 40, 50, 60])
def test_finding_heading_stays_with_the_first_evidence_lines(preface_length):
    data = _data()
    data.report.status = "needs_review"
    data.report.reason = "A saved report limitation requires source review. " * preface_length
    data.flags[0].evidence_excerpt = "FIRST_EVIDENCE_TOKEN The exact saved manuscript excerpt."
    with fitz.open(stream=build_report_pdf(data), filetype="pdf") as doc:
        heading_pages = [page.get_text() for page in doc if "Finding 1:" in page.get_text()]
        assert len(heading_pages) == 1
        assert "FIRST_EVIDENCE_TOKEN" in heading_pages[0]
        category_pages = [
            page.get_text() for page in doc if "CITATION INTEGRITY (" in page.get_text()
        ]
        if category_pages:
            assert "FIRST_EVIDENCE_TOKEN" in category_pages[0]


def test_export_retains_plain_language_coverage_causes_and_each_saved_skip_count():
    from app.report.schemas import IntegrityCheckStatusOut

    data = _data()
    data.report.integrity_check_status = [
        IntegrityCheckStatusOut(
            check_kind="internal_agreement",
            outcome="escalated",
            coverage_reason="statement_roles_unverified",
            n_skipped_quota=4,
            n_skipped_api_down=2,
            n_skipped_parse_failure=3,
            n_linkage_unassessed=5,
        )
    ]
    text = _text(build_report_pdf(data))
    for expected in (
        "could not reliably establish that they describe this study",
        "4 items skipped because AI capacity was unavailable",
        "2 items skipped because a service was unavailable",
        "3 items skipped because the model response could not be validated",
        "5 reference links remain unassessed",
    ):
        assert expected in text


def test_long_group_label_does_not_overlap_footer_or_running_header():
    data = _data()
    data.report.manuscript_group_label = "Very long instructor group name " * 8
    with fitz.open(stream=build_report_pdf(data), filetype="pdf") as doc:
        assert len(doc) > 1
        for page in doc:
            spans = [
                span
                for block in page.get_text("dict")["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"]
                if span["bbox"][1] > page.rect.height - 90
            ]
            page_spans = [s for s in spans if s["text"].startswith("Page ")]
            assert len(page_spans) == 1
            counter = fitz.Rect(page_spans[0]["bbox"])
            for span in spans:
                if span is page_spans[0]:
                    continue
                assert not counter.intersects(fitz.Rect(span["bbox"])), span["text"]
            for span in spans:
                assert 50 <= span["bbox"][0] <= span["bbox"][2] <= page.rect.width - 50
            if page.number:
                header = [
                    s
                    for block in page.get_text("dict")["blocks"]
                    for line in block.get("lines", [])
                    for s in line["spans"]
                    if s["bbox"][1] < 60
                ]
                assert len(header) == 2
                assert not fitz.Rect(header[0]["bbox"]).intersects(fitz.Rect(header[1]["bbox"]))


def _public_flag(**overrides):
    fields = {
        "id": 1,
        "check_result_id": 1,
        "check_run_id": 1,
        "manuscript_group_label": "Own manuscript",
        "check_kind": "citation_integrity",
        "criterion_text": None,
        "severity": "low",
        "confidence": None,
        "evidence_excerpt": "Original source",
        "page_anchor": "p. 4",
        "annotation": "PRIVATE_ANNOTATION",
        "overridden": False,
        "override_reason": None,
        "ai_verdict_summary": "uncited_reference",
        "ai_reasoning": "Stored public reason",
        "llm_mode": "real",
    }
    return FlagOut(**(fields | overrides))


def test_projection_keeps_saved_values_and_excludes_private_annotations():
    flag = _public_flag(
        p_value_comparison={"reported": "p < .01", "recalculated": "p = .23"},
        agreement_pair={
            "intent_text": "Exact candidate intent.",
            "intent_anchor": "¶12",
            "outcome_text": "Exact candidate outcome.",
            "outcome_anchor": None,
        },
        citation_source_key={"kind": "doi", "value": "10.1234/source"},
        override_reason="STALE_OVERRIDE",
    )
    projected = flag_export_detail(flag)
    assert projected.reasoning == flag.ai_reasoning
    assert projected.source_identifier == "doi: 10.1234/source"
    assert [c.text for c in projected.comparisons] == [
        "Exact candidate intent.",
        "Exact candidate outcome.",
        "p < .01",
        "p = .23",
    ]
    assert projected.comparisons[1].anchor is None
    assert projected.override_reason is None
    assert "PRIVATE_ANNOTATION" not in projected.model_dump_json()
    assert "STALE_OVERRIDE" not in projected.model_dump_json()


def test_reuse_projection_keeps_only_the_public_pair_and_bounded_context():
    projected = flag_export_detail(
        _public_flag(
            check_kind="originality_reuse",
            passage_pair={
                "own_excerpt": "EXACT_OWN_TEXT",
                "own_context_before": "OWN_BEFORE",
                "own_context_after": None,
                "matched_ref": 42,
                "matched_excerpt": "EXACT_MATCH_TEXT",
                "matched_context_before": None,
                "matched_context_after": "MATCH_AFTER",
                "context_words_each_side": 40,
                "similarity": 0.95,
                "level": "high_similarity",
                "matched_group_label": "FOREIGN_IDENTITY",
                "full_document": "FOREIGN_DOCUMENT",
            },
        )
    )
    assert [c.text for c in projected.comparisons] == [
        "EXACT_OWN_TEXT",
        "OWN_BEFORE",
        "EXACT_MATCH_TEXT",
        "MATCH_AFTER",
    ]
    assert projected.comparisons[2].label == "Library match 42"
    assert projected.comparisons[2].anchor is None
    assert "FOREIGN_IDENTITY" not in projected.model_dump_json()
    assert "FOREIGN_DOCUMENT" not in projected.model_dump_json()
    assert "0.95" not in projected.model_dump_json()


async def _stored_reuse_flag(detail):
    from types import SimpleNamespace

    flag = SimpleNamespace(
        id=0,
        detail=detail,
        evidence_excerpt="Stored system comparison summary.",
        page_anchor="whole document",
        annotation="PRIVATE_REUSE_NOTE",
        severity=SimpleNamespace(value="low"),
        confidence=None,
        overridden=False,
        override_reason=None,
        confirmed_citation_source=False,
    )
    result = SimpleNamespace(
        id=1,
        check_run_id=1,
        criterion_id=None,
        kind=CheckKind.originality_reuse,
        detail={"reason": "PARENT_REASON_MUST_NOT_REPLACE_FLAG_DETAIL"},
    )
    manuscript = SimpleNamespace(group_label="Own group")
    run = SimpleNamespace(llm_mode=SimpleNamespace(value="real"))
    return await _to_flag_out(None, flag, result, manuscript, run), flag.evidence_excerpt


@pytest.mark.parametrize("recorded_reason", [None, "LEGACY_PRIVATE_OR_ACCUSATORY_REASON"])
async def test_current_reuse_guidance_is_not_a_recorded_reason_or_source_quote(recorded_reason):
    detail = {
        "kind": "reuse_high_similarity",
        "matched_manuscript_id": 42,
        "evidence_unavailable": True,
    }
    if recorded_reason:
        detail["reason"] = recorded_reason
    flag, excerpt = await _stored_reuse_flag(detail)
    projected = flag_export_detail(flag, recorded_detail=detail, recorded_excerpt=excerpt)
    assert projected.reasoning is None
    assert projected.current_guidance == flag.ai_reasoning
    assert projected.evidence_is_summary is True
    assert projected.recorded_explanation_withheld is bool(recorded_reason)
    assert "LEGACY_PRIVATE" not in projected.model_dump_json()
    data = _data(flag_details={0: projected.model_dump()})
    data.report.results = []
    data.flags[0].check_kind = "originality_reuse"
    data.flags[0].evidence_excerpt = flag.evidence_excerpt
    data.flags[0].affects_readiness = False
    text = _text(build_report_pdf(data))
    assert "System comparison summary" in text
    assert "Manuscript evidence" not in text
    assert "Current comparison guidance:" in text
    assert ("No separate explanation was saved" in text) == (recorded_reason is None)
    assert "LEGACY_PRIVATE" not in text
    assert "PRIVATE_REUSE_NOTE" not in text


async def test_missing_legacy_pair_identity_does_not_abort_flag_or_export():
    detail = {
        "kind": "reuse_high_similarity_passage",
        "matched_text": "A stored matching excerpt without its record identity.",
    }
    flag, excerpt = await _stored_reuse_flag(detail)
    assert flag.passage_pair is None
    assert flag.evidence_unavailable is True
    projected = flag_export_detail(flag, recorded_detail=detail, recorded_excerpt=excerpt)
    data = _data(flag_details={0: projected.model_dump()})
    text = _text(build_report_pdf(data))
    assert "Supporting comparison evidence was not available" in text


async def test_legacy_summary_contract_is_preserved_but_export_discloses_absent_pair():
    detail = {"kind": "reuse_high_similarity", "matched_manuscript_id": 42}
    flag, excerpt = await _stored_reuse_flag(detail)
    assert flag.passage_pair is None
    assert flag.evidence_unavailable is False
    projected = flag_export_detail(flag, recorded_detail=detail, recorded_excerpt=excerpt)
    assert projected.evidence_unavailable is True
    text = _text(build_report_pdf(_data(flag_details={0: projected.model_dump()})))
    assert "Supporting comparison evidence was not available" in text


async def test_saved_reason_equal_to_current_policy_stays_a_recorded_reason():
    from app.findings.policy import reuse_reason

    detail = {"kind": "reuse_high_similarity_passage", "matched_manuscript_id": 42}
    detail["reason"] = reuse_reason(detail)
    flag, excerpt = await _stored_reuse_flag(detail)
    projected = flag_export_detail(flag, recorded_detail=detail, recorded_excerpt=excerpt)
    assert projected.reasoning == detail["reason"]
    assert projected.current_guidance is None
    assert projected.recorded_explanation_withheld is False


def test_reference_notices_come_from_saved_coverage_without_reinterpreting_metadata():
    rows = [
        {
            "reference_order_index": 0,
            "status": "existence_confirmed",
            "lookup_attempted": True,
            "metadata_complete": True,
        },
        {
            "reference_order_index": 1,
            "status": "unassessed",
            "lookup_attempted": False,
            "reason": "ambiguous_identifier_wrap",
        },
        {"reference_order_index": 2, "status": "api_down", "lookup_attempted": True},
        {
            "reference_order_index": 3,
            "status": "existence_confirmed",
            "lookup_attempted": True,
            "metadata_complete": False,
            "private": "PRIVATE_RECORD",
        },
        {"reference_order_index": -1, "status": "unassessed", "lookup_attempted": False},
        {"reference_order_index": True, "status": "unassessed", "lookup_attempted": False},
        {"reference_order_index": 6, "status": [], "lookup_attempted": False},
        {"reference_order_index": 7, "status": "unassessed", "lookup_attempted": "false"},
        None,
    ]
    notices = unassessed_references({"reference_metadata_coverage": rows})
    assert [n.reference_order_index for n in notices] == [1, 2, 3]
    assert notices[-1].status == "existence_confirmed"
    assert "PRIVATE_RECORD" not in "".join(n.model_dump_json() for n in notices)
    assert unassessed_references({}) == []


def test_export_domain_labels_match_every_existing_screen_label():
    import re
    from pathlib import Path

    screen = Path(__file__).resolve().parents[2] / "frontend/src/domain/problemLabel.ts"
    screen_labels = dict(re.findall(r'^\s*([a-z_]+): "([^"]+)",', screen.read_text("utf-8"), re.M))
    assert len(screen_labels) >= 20
    for key, label in screen_labels.items():
        assert _problem_labels()[key] == label


@pytest.mark.parametrize("llm_mode", ["real", "fake", "unknown"])
@pytest.mark.parametrize("decision", [None, "approved"])
def test_footer_variants_are_within_page_and_do_not_intersect(llm_mode, decision):
    data = _data()
    data.report.results = []
    data.report.llm_mode = llm_mode
    data.report.decision = decision
    data.report.manuscript_group_label = "Group & <literal identity> " * 20
    with fitz.open(stream=build_report_pdf(data), filetype="pdf") as doc:
        for page in doc:
            spans = [
                span
                for block in page.get_text("dict")["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"]
                if span["bbox"][1] > page.rect.height - 80
            ]
            for index, first in enumerate(spans):
                rect = fitz.Rect(first["bbox"])
                assert 50 <= rect.x0 < rect.x1 <= page.rect.width - 50
                assert rect.y1 < page.rect.height - 10
                for second in spans[index + 1 :]:
                    assert not rect.intersects(fitz.Rect(second["bbox"]))
            texts = " ".join(s["text"] for s in spans)
            assert ("DRAFT - NO DECISION RECORDED" in texts) == (decision is None)
            assert ("Test-mode AI responses." in texts) == (llm_mode == "fake")
            assert ("AI response mode could not be verified" in texts) == (llm_mode == "unknown")
            assert "predates AI-mode tracking" not in texts
