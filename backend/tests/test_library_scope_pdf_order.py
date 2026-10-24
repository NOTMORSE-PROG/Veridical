"""F7 scope must qualify the first comparison, with truthful no-lead fallbacks."""

import pytest

from app.report.export import build_report_pdf
from app.report.schemas import OriginalityScopeOut
from tests.test_report_export_details import _data, _text


def _report(count=1, *, unknown=False, reason=None):
    data = _data()
    data.report.results = []
    data.archive_size_n = count
    data.report.originality_scope = OriginalityScopeOut(
        compared_count=count,
        corpus="library",
        not_compared_reason=reason,
        library_passage_indexing=None
        if unknown
        else dict(version="v1", partial=True, unavailable=False, not_recorded=False),
    )
    return data


@pytest.mark.parametrize("groups", ["optional", "historical", "both"])
def test_scope_precedes_first_f7_body_but_does_not_qualify_f6(groups):
    data = _report()
    template = data.flags[0]
    data.flags = [
        template.model_copy(
            update={
                "id": 0,
                "check_kind": "statistical_forensics",
                "evidence_excerpt": "F6_RETAINED",
            }
        )
    ]
    for index, optional in enumerate(
        [True] if groups == "optional" else [False] if groups == "historical" else [False, True],
        start=1,
    ):
        data.flags.append(
            template.model_copy(
                update={
                    "id": index,
                    "check_kind": "originality_reuse",
                    "affects_readiness": not optional,
                    "evidence_excerpt": f"F7_BODY_{index}",
                }
            )
        )
    text = _text(build_report_pdf(data))
    assert text.count("Passage comparison limits") == 1
    assert text.count("compared against 1 Library record") == 1
    assert text.index("F6_RETAINED") < text.index("Passage comparison limits")
    assert text.index("Passage comparison limits") < text.index("F7_BODY_1")
    if groups in {"optional", "both"}:
        assert "Comparison lead 1 of" in text and "do not affect readiness" in text


@pytest.mark.parametrize("state", ["positive_no_leads", "empty", "not_assessed", "unknown"])
def test_scope_fallback_without_f7_records_remains_truthful(state):
    data = _report(
        None if state == "not_assessed" else 0 if state == "empty" else 1,
        unknown=state == "unknown",
        reason="no_text" if state == "not_assessed" else None,
    )
    data.flags = []
    text = _text(build_report_pdf(data))
    if state == "positive_no_leads":
        assert text.count("Passage comparison limits") == 1
    else:
        assert "Passage comparison limits" not in text
    if state == "unknown":
        assert text.count("Passage coverage for this comparison was not recorded.") == 1
    if state == "empty":
        assert "not evidence of originality" in text
    if state == "not_assessed":
        assert "no comparable content could be extracted" in text.lower()
