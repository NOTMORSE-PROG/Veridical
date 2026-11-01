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
DOI_URL = "https://doi.org/10.21203/rs.3.x"


def openalex(
    title,
    *,
    retracted=True,
    record_type="article",
    url=DOI_URL,
    attempted=("crossref", "openalex"),
):
    return VerificationResult(
        found=True,
        provider="openalex",
        title=title,
        url=url,
        raw={"is_retracted": retracted, "type": record_type, "display_name": title},
        attempted_providers=list(attempted),
    )


def draft(result, *, identified=True, settings=None):
    verdict = CitationVerdict(
        VerdictKind.existence_confirmed,
        result,
        search_identity=SimpleNamespace() if identified else None,
    )
    return _verdict_flag_draft(CITATION, verdict, settings or Settings(_env_file=None))


def test_a_work_only_openalex_marks_retracted_is_a_low_uncounted_lead():
    lead = draft(openalex("Kiosk queues in clinics"))
    assert lead.severity == FlagSeverity.low
    assert lead.detail["kind"] == POSSIBLE_RETRACTION
    assert lead.detail["affects_readiness"] is False
    assert lead.detail["reason"] == (
        "OpenAlex lists this work ('Kiosk queues in clinics') as possibly retracted. "
        "Crossref's title search did not find it, so this could not be confirmed there. "
        f"Please check the publisher's page: {DOI_URL}."
    )
    assert not any(word in lead.detail["reason"].lower() for word in ACCUSING)
    assert exclusion_reason("citation_integrity", lead.detail) == "retraction_lead_not_counted"


@pytest.mark.parametrize(
    "title",
    [
        # Publishers retitle the retracted work itself this way: a work, not a notice.
        "Retracted: Kiosk queues in clinics",
        "RETRACTED: Kiosk queues in clinics",
        "Notice of Retraction: Kiosk queues in clinics",
        "Withdrawn: Kiosk queues in clinics",
    ],
)
def test_a_retitled_retracted_work_is_never_called_a_notice(title):
    lead = draft(openalex(title))
    assert lead.detail["kind"] == POSSIBLE_RETRACTION
    assert "not the research paper" not in lead.detail["reason"]


@pytest.mark.parametrize(
    "title,record_type",
    [
        ("Kiosk queues in clinics", "retraction"),
        ("Retraction Note: Kiosk queues in clinics", "article"),
        ("Retraction notice to 'Kiosk queues in clinics'", "article"),
        ("Statement of Retraction: Kiosk queues in clinics", "article"),
        ("Retraction: Kiosk queues in clinics", "article"),
        ("Retraction of: Kiosk queues in clinics", "article"),
    ],
)
def test_a_retraction_notice_is_described_as_a_notice(title, record_type):
    lead = draft(openalex(title, record_type=record_type))
    assert lead.detail["kind"] == RETRACTION_NOTICE
    assert lead.detail["reason"].startswith("This entry matches a retraction notice")
    assert "possibly retracted" not in lead.detail["reason"]
    assert exclusion_reason("citation_integrity", lead.detail) == "retraction_lead_not_counted"


def test_a_record_openalex_types_as_a_notice_needs_no_retracted_flag():
    lead = draft(openalex("Kiosk queues in clinics", retracted=False, record_type="retraction"))
    assert lead.detail["kind"] == RETRACTION_NOTICE


@pytest.mark.parametrize(
    "result",
    [
        # A title alone never raises a lead, whatever it says.
        openalex("Withdrawal intention of students in e-learning systems", retracted=False),
        openalex("Retraction-based recommender for e-commerce", retracted=False),
        openalex("Retraction Note: Kiosk queues in clinics", retracted=False),
        openalex("Gum retraction in orthodontic patients", retracted=False),
        # An ordinary OpenAlex work.
        openalex("Kiosk queues in clinics", retracted=False),
        # Another registry's record never yields this lead.
        VerificationResult(
            found=True, provider="crossref", title="Kiosk queues", raw={"is_retracted": True}
        ),
    ],
)
def test_no_lead_without_openalex_s_own_retraction_signal(result):
    assert draft(result) is None


def test_crossref_that_was_not_searched_is_not_said_to_have_missed_it():
    lead = draft(openalex("Kiosk queues in clinics", attempted=("openalex",)))
    assert "Crossref was not searched for it" in lead.detail["reason"]
    assert "did not find" not in lead.detail["reason"]


@pytest.mark.parametrize(
    "url,link",
    [
        (DOI_URL, f"the publisher's page: {DOI_URL}"),
        ("https://openalex.org/W123", "the OpenAlex record: https://openalex.org/W123"),
        (None, POLICY.link_unknown),
    ],
)
def test_the_link_is_named_for_what_it_is(url, link):
    lead = draft(openalex("Kiosk queues in clinics", url=url))
    assert lead.detail["reason"].endswith(f"Please check {link}.")


def test_only_a_search_match_that_identified_the_reference_can_raise_it():
    assert draft(openalex("Kiosk queues in clinics"), identified=False) is None


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


def _custom(tmp_path, **changes):
    packaged = json.loads(
        (
            Path(__file__).parents[1] / "app/checks/citations/data/openalex_retraction_lead.json"
        ).read_text(encoding="utf-8")
    )
    custom = tmp_path / "lead.json"
    custom.write_text(json.dumps({**packaged, **changes}), encoding="utf-8")
    return Settings(_env_file=None, citation_openalex_retraction_lead_file=custom)


def test_wording_notice_patterns_and_severity_are_data(tmp_path):
    settings = _custom(
        tmp_path,
        notice_title_patterns=["^\\s*erratum\\b"],
        retracted_work_reason="Check {title} at {link}.",
        severity="med",
    )
    lead = draft(openalex("Erratum to a kiosk study"), settings=settings)
    assert lead.detail["kind"] == RETRACTION_NOTICE
    lead = draft(openalex("Kiosk queues"), settings=settings)
    assert lead.detail["reason"] == f"Check Kiosk queues at the publisher's page: {DOI_URL}."
    assert lead.severity == FlagSeverity.med


@pytest.mark.parametrize(
    "changes",
    [
        {"notice_reason": "Matches {notice_title}."},
        {"link_doi": "{address}"},
        {"notice_title_patterns": ["("]},
        {"notice_reason": "Matches {title.foo}."},
        {"retracted_work_reason": "Check {title[0]} at {link}."},
    ],
)
def test_a_policy_file_that_cannot_fill_its_wording_raises_no_lead(tmp_path, changes):
    load_retraction_lead_policy.cache_clear()
    assert draft(openalex("Kiosk queues"), settings=_custom(tmp_path, **changes)) is None


def test_an_unreadable_policy_file_raises_no_lead(tmp_path):
    settings = Settings(
        _env_file=None, citation_openalex_retraction_lead_file=tmp_path / "missing.json"
    )
    assert draft(openalex("Kiosk queues"), settings=settings) is None


def test_the_lead_keeps_its_saved_reference_links():
    lead = draft(openalex("Kiosk queues in clinics"))
    reference = SimpleNamespace(
        raw_text=lead.evidence_excerpt, doi=None, url="https://example.org/kiosk", order_index=4
    )
    saved = with_reference_locator(lead, reference)
    assert saved.detail["citation_source_locator"]["finding_kind"] == POSSIBLE_RETRACTION
