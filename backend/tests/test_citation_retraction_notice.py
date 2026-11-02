"""BUG-683: a retraction notice cited by DOI is named a notice, never a counted retraction."""

from types import SimpleNamespace

import pytest

from app.checks.citations.retraction_lead import RETRACTION_NOTICE
from app.checks.citations.verify import (
    CitationVerdict,
    VerdictKind,
    _verdict_flag_draft,
    _verdict_from_result,
)
from app.config import Settings
from app.external.crossref import _parse_work, retraction_notice_targets
from app.external.schemas import VerificationResult
from app.findings.policy import exclusion_reason
from app.models.enums import FlagSeverity

CITATION = SimpleNamespace(
    raw_text="Journal of Examples. (2023). Retraction Note: Kiosk queues. "
    "https://doi.org/10.1000/notice.1",
    order_index=2,
)


def record(doi, title, *, update_to=(), updated_by=()):
    def rel(target, source="publisher"):
        return {"type": "retraction", "DOI": target, "source": source, "label": "Retraction"}

    return {
        "DOI": doi,
        "title": [title],
        "update-to": [rel(t) for t in update_to],
        "updated-by": [rel(t, "retraction-watch") for t in updated_by],
    }


NOTICE = record("10.1000/notice.1", "Retraction Note: Kiosk queues", update_to=["10.1000/work.1"])
# A notice titled like a retracted work: the title must not decide.
NOTICE_TITLED_RETRACTED = record(
    "10.1000/notice.2", "RETRACTED: Luo et al. Kiosk queues", update_to=["10.1000/work.2"]
)
# Works: retracted in place (self-pointing update), or through updated-by,
# including titles that read like notices.
WORK_IN_PLACE = record("10.1000/work.3", "RETRACTED: Kiosk queues", update_to=["10.1000/work.3"])
WORK_NOTICE_OF_RETRACTION = record(
    "10.1000/work.4", "Notice of Retraction: Kiosk queues", updated_by=["10.1000/work.4"]
)
WORK_TITLE_ONLY = record("10.1000/work.5", "RETRACTED: Kiosk queues")


@pytest.mark.parametrize(
    "msg,targets",
    [
        (NOTICE, ("10.1000/work.1",)),
        (NOTICE_TITLED_RETRACTED, ("10.1000/work.2",)),
        (WORK_IN_PLACE, ()),
        (WORK_NOTICE_OF_RETRACTION, ()),
        (WORK_TITLE_ONLY, ()),
        ({"DOI": "10.1000/x", "update-to": [{"type": "correction", "DOI": "10.1000/y"}]}, ()),
        (None, ()),
    ],
)
def test_a_notice_is_told_apart_by_its_relations_not_its_title(msg, targets):
    assert retraction_notice_targets(msg) == targets


@pytest.mark.parametrize("msg", [NOTICE, NOTICE_TITLED_RETRACTED])
def test_the_parser_never_marks_a_notice_retracted(msg):
    parsed = _parse_work(msg)
    assert parsed.retracted is False and parsed.retraction_detail is None


@pytest.mark.parametrize("msg", [WORK_IN_PLACE, WORK_NOTICE_OF_RETRACTION, WORK_TITLE_ONLY])
def test_a_retracted_work_is_still_retracted(msg):
    assert _parse_work(msg).retracted is True
    assert _verdict_from_result(_parse_work(msg)).kind == VerdictKind.retracted


def test_a_notice_cached_as_retracted_before_the_rule_is_named_a_notice():
    cached = VerificationResult(
        found=True, provider="crossref", title=NOTICE["title"][0], retracted=True, raw=NOTICE
    )
    assert _verdict_from_result(cached).kind == VerdictKind.retraction_notice


def draft(msg, settings=None):
    verdict = _verdict_from_result(_parse_work(msg))
    return _verdict_flag_draft(CITATION, verdict, settings or Settings(_env_file=None))


def test_a_cited_notice_is_a_low_uncounted_notice_lead():
    lead = draft(NOTICE)
    assert lead.severity == FlagSeverity.low
    assert lead.detail["kind"] == RETRACTION_NOTICE
    assert lead.detail["retracted_dois"] == ["10.1000/work.1"]
    assert lead.detail["reason"] == (
        "This entry's DOI leads to a retraction notice ('Retraction Note: Kiosk queues'), "
        "not to the research paper it retracts (https://doi.org/10.1000/work.1). "
        "Please check which one the entry means to cite."
    )
    assert exclusion_reason("citation_integrity", lead.detail) == "retraction_lead_not_counted"


def test_a_cited_retracted_work_keeps_its_counted_high_finding():
    finding = draft(WORK_IN_PLACE)
    assert finding.detail["kind"] == "retracted_source"
    assert finding.severity == FlagSeverity.high
    assert exclusion_reason("citation_integrity", finding.detail) is None


def test_without_its_wording_a_notice_raises_nothing_rather_than_a_retraction(tmp_path):
    settings = Settings(
        _env_file=None, citation_openalex_retraction_lead_file=tmp_path / "missing.json"
    )
    verdict = CitationVerdict(VerdictKind.retraction_notice, _parse_work(NOTICE))
    assert _verdict_flag_draft(CITATION, verdict, settings) is None
