"""BUG-683: a retraction notice cited by DOI is named a notice, never a counted retraction."""

from types import SimpleNamespace

import pytest

from app.checks.citations.retraction_lead import RETRACTION_NOTICE
from app.checks.citations.verify import (
    CitationVerdict,
    VerdictKind,
    _describable_notice,
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
def test_only_a_lookup_by_the_cited_doi_names_a_notice(msg):
    parsed = _parse_work(msg)
    assert _verdict_from_result(parsed, key_kind="doi").kind == VerdictKind.retraction_notice
    # A title match could equally be the retracted work: unchanged there.
    assert _verdict_from_result(parsed, key_kind="title").kind == VerdictKind.retracted
    assert parsed.retracted is True  # the loose-title-match guard still sees it


@pytest.mark.parametrize("msg", [WORK_IN_PLACE, WORK_NOTICE_OF_RETRACTION, WORK_TITLE_ONLY])
def test_a_retracted_work_is_still_retracted(msg):
    assert _parse_work(msg).retracted is True
    assert _verdict_from_result(_parse_work(msg), key_kind="doi").kind == VerdictKind.retracted


def test_a_notice_cached_as_retracted_before_the_rule_is_named_a_notice():
    cached = VerificationResult(
        found=True, provider="crossref", title=NOTICE["title"][0], retracted=True, raw=NOTICE
    )
    assert _verdict_from_result(cached, key_kind="doi").kind == VerdictKind.retraction_notice


def test_a_self_link_written_as_a_resolver_address_is_still_the_work():
    msg = record("10.1000/work.6", "RETRACTED: Kiosk", update_to=["https://doi.org/10.1000/WORK.6"])
    assert retraction_notice_targets(msg) == ()


def draft(msg, settings=None):
    verdict = _verdict_from_result(_parse_work(msg), key_kind="doi")
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
    assert exclusion_reason("citation_integrity", lead.detail) == "retraction_notice_not_counted"


def test_a_cited_retracted_work_keeps_its_counted_high_finding():
    finding = draft(WORK_IN_PLACE)
    assert finding.detail["kind"] == "retracted_source"
    assert finding.severity == FlagSeverity.high
    assert exclusion_reason("citation_integrity", finding.detail) is None


def test_a_notice_whose_wording_is_unreadable_is_disclosed_as_unassessed(tmp_path):
    settings = Settings(
        _env_file=None, citation_openalex_retraction_lead_file=tmp_path / "missing.json"
    )
    notice = CitationVerdict(VerdictKind.retraction_notice, _parse_work(NOTICE))
    disclosed = _describable_notice(notice, settings)
    assert disclosed.kind == VerdictKind.unassessed
    assert disclosed.unassessed_reason == "retraction_notice_policy_unavailable"
    assert _describable_notice(notice, Settings(_env_file=None)).kind == (
        VerdictKind.retraction_notice
    )
