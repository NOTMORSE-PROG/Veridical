"""BUG-640: OpenAlex's retraction signal is a Low lead to verify, never counted."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.checks.citations.retraction_lead import (
    POSSIBLE_RETRACTION,
    RETRACTION_NOTICE,
    load_retraction_lead_policy,
)
from app.checks.citations.source_locator import with_reference_locator
from app.checks.citations.verify import CitationVerdict, VerdictKind, _verdict_flag_draft
from app.config import Settings
from app.external.schemas import VerificationResult
from app.findings.policy import exclusion_reason
from app.models.enums import FlagSeverity

POLICY = load_retraction_lead_policy()
CITATION = SimpleNamespace(
    raw_text="Cruz, M. (2023). Kiosk queues in clinics. Research Square.",
    order_index=4,
    doi=None,
    url=None,
)
ACCUSING = ("fake", "fabricat", "fraud", "misconduct", "invalid")


def openalex(title, *, retracted, record_type="article", url="https://doi.org/10.21203/rs.3.x"):
    return VerificationResult(
        found=True,
        provider="openalex",
        title=title,
        url=url,
        raw={"is_retracted": retracted, "type": record_type, "display_name": title},
    )


def draft(result, *, identified=True, settings=None):
    verdict = CitationVerdict(
        VerdictKind.existence_confirmed,
        result,
        search_identity=SimpleNamespace() if identified else None,
    )
    return _verdict_flag_draft(CITATION, verdict, settings or Settings(_env_file=None))


def test_a_work_only_openalex_marks_retracted_is_a_low_uncounted_lead():
    lead = draft(openalex("Kiosk queues in clinics", retracted=True))
    assert lead.severity == FlagSeverity.low
    assert lead.detail["kind"] == POSSIBLE_RETRACTION
    assert lead.detail["affects_readiness"] is False
    reason = lead.detail["reason"]
    assert reason.startswith("OpenAlex marks this work as possibly retracted ('Kiosk queues")
    assert "Possible retraction, please verify" in reason
    assert "https://doi.org/10.21203/rs.3.x" in reason
    assert not any(word in reason.lower() for word in ACCUSING)
    assert exclusion_reason("citation_integrity", lead.detail) == "retraction_lead_not_counted"


@pytest.mark.parametrize(
    "title,record_type",
    [
        ("Kiosk queues in clinics", "retraction"),
        ("Retracted: Kiosk queues in clinics", "article"),
        ("Retraction Note: Kiosk queues in clinics", "article"),
        ("Statement of Retraction: Kiosk queues in clinics", "article"),
        ("Notice of Retraction Kiosk queues in clinics", "article"),
        ("WITHDRAWAL: Kiosk queues in clinics", "article"),
    ],
)
def test_a_retraction_notice_is_described_as_a_notice_not_a_retracted_work(title, record_type):
    lead = draft(openalex(title, retracted=True, record_type=record_type))
    assert lead.detail["kind"] == RETRACTION_NOTICE
    reason = lead.detail["reason"]
    assert reason.startswith("This entry matches a retraction notice in OpenAlex")
    assert "possibly retracted" not in reason
    assert exclusion_reason("citation_integrity", lead.detail) == "retraction_lead_not_counted"


def test_a_cited_notice_is_named_even_when_openalex_does_not_flag_it():
    lead = draft(openalex("Retraction notice: Kiosk queues", retracted=False))
    assert lead.detail["kind"] == RETRACTION_NOTICE


@pytest.mark.parametrize(
    "result",
    [
        # An ordinary OpenAlex work: confirmed, nothing to say.
        openalex("Kiosk queues in clinics", retracted=False),
        # Another registry's record never yields this lead.
        VerificationResult(
            found=True, provider="crossref", title="Kiosk queues", raw={"is_retracted": True}
        ),
        # "Retraction" inside a title is not a notice's opening.
        openalex("Gum retraction in orthodontic patients", retracted=False),
    ],
)
def test_no_lead_without_an_openalex_retraction_signal(result):
    assert draft(result) is None


def test_only_a_search_match_that_identified_the_reference_can_raise_it():
    assert draft(openalex("Kiosk queues in clinics", retracted=True), identified=False) is None


def test_a_record_without_a_web_address_still_says_how_to_check():
    lead = draft(openalex("Kiosk queues in clinics", retracted=True, url=None))
    assert POLICY.unknown_url in lead.detail["reason"]
    assert lead.detail["registry_record_url"] is None


def test_a_crossref_retraction_keeps_its_counted_finding():
    retracted = VerificationResult(
        found=True, provider="crossref", title="Kiosk queues", retracted=True
    )
    finding = _verdict_flag_draft(
        CITATION, CitationVerdict(VerdictKind.retracted, retracted), Settings(_env_file=None)
    )
    assert finding.detail["kind"] == "retracted_source"
    assert finding.severity == FlagSeverity.high
    assert exclusion_reason("citation_integrity", finding.detail) is None


def test_wording_notice_patterns_and_severity_are_data(tmp_path):
    packaged = json.loads(
        (
            Path(__file__).parents[1] / "app/checks/citations/data/openalex_retraction_lead.json"
        ).read_text(encoding="utf-8")
    )
    custom = tmp_path / "lead.json"
    custom.write_text(
        json.dumps(
            {
                **packaged,
                "notice_title_patterns": ["^\\s*erratum\\b"],
                "retracted_work_reason": "Check {title} at {url}.",
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(_env_file=None, citation_openalex_retraction_lead_file=custom)
    lead = draft(openalex("Erratum to a kiosk study", retracted=False), settings=settings)
    assert lead.detail["kind"] == RETRACTION_NOTICE
    lead = draft(openalex("Kiosk queues", retracted=True), settings=settings)
    assert lead.detail["reason"] == "Check Kiosk queues at https://doi.org/10.21203/rs.3.x."


def test_an_unreadable_policy_file_raises_no_lead(tmp_path):
    settings = Settings(
        _env_file=None, citation_openalex_retraction_lead_file=tmp_path / "missing.json"
    )
    assert draft(openalex("Kiosk queues", retracted=True), settings=settings) is None


def test_the_lead_keeps_its_saved_reference_links():
    lead = draft(openalex("Kiosk queues in clinics", retracted=True))
    reference = SimpleNamespace(
        raw_text=lead.evidence_excerpt, doi=None, url="https://example.org/kiosk", order_index=4
    )
    saved = with_reference_locator(lead, reference)
    assert saved.detail["citation_source_locator"]["finding_kind"] == POSSIBLE_RETRACTION
