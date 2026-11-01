"""BUG-664: a DOI that leads to a different work is told apart from drift."""

import json
from types import SimpleNamespace

import pytest

from app.checks.citations.different_work import (
    leads_to_different_work,
    load_different_work_policy,
    title_word_overlap,
)
from app.checks.citations.metadata import compare_metadata
from app.checks.citations.verify import CitationVerdict, VerdictKind, _verdict_flag_draft
from app.config import Settings
from app.ingest.references import parse_reference
from app.models.enums import FlagSeverity

ENTRY = (
    "Yurov, V. M., Eremin, E. N., & Guchenko, S. A. (2022). Microhardness and wear resistance "
    "of a high-entropy coating FeCrNiTiZrAl. Journal of Physics: Conference Series, 2182(1), "
    "012083. https://doi.org/10.1088/1742-6596/2182/1/012083"
)


def record(title, family, year):
    return SimpleNamespace(
        provider="crossref",
        title=title,
        raw={
            "title": [title],
            "author": [{"family": family, "given": "A."}] if family else [],
            "published": {"date-parts": [[year]]},
        },
    )


def draft_for(entry, registered, settings=None):
    citation = parse_reference(entry, order_index=4)
    comparison = compare_metadata(citation, registered, settings=Settings(_env_file=None))
    verdict = CitationVerdict(
        VerdictKind.metadata_mismatch, registered, metadata_comparison=comparison
    )
    return _verdict_flag_draft(citation, verdict, settings or Settings(_env_file=None))


def test_a_doi_leading_to_another_paper_is_a_medium_possible_typo():
    other = record(
        "A validation study on the physiological parameters recorded by a wearable band",
        "Lu",
        2024,
    )
    draft = draft_for(ENTRY, other)
    assert draft.severity == FlagSeverity.med
    assert draft.detail["kind"] == "possible_metadata_mismatch"
    assert draft.detail["leads_to_different_work"] is True
    reason = draft.detail["reason"]
    assert reason.startswith(
        "The DOI in this entry appears to lead to a different work ('A validation"
    )
    assert "Lu, 2024" in reason and "Possible typo in the DOI" in reason
    for word in ("wrong", "fake", "fabricat", "invalid"):
        assert word not in reason.lower()


@pytest.mark.parametrize(
    "registered",
    [
        # Print year off by one.
        record(
            "Microhardness and wear resistance of a high-entropy coating FeCrNiTiZrAl",
            "Yurov",
            2021,
        ),
        # The registry keeps only the main title.
        record("Microhardness and wear resistance", "Yurov", 2022),
        # The registry title is the cited title reordered around a colon.
        record(
            "Wear resistance of a high-entropy coating FeCrNiTiZrAl: microhardness study",
            "Yurov",
            2022,
        ),
    ],
)
def test_drift_stays_a_low_mismatch(registered):
    draft = draft_for(ENTRY, registered)
    assert draft.severity == FlagSeverity.low
    assert "leads_to_different_work" not in draft.detail
    assert draft.detail["reason"].startswith("The DOI identifies a registered source")


def test_a_confirmed_first_author_keeps_a_different_title_low():
    renamed = record("Hardness of alloy films on steel substrates measured again", "Yurov", 2022)
    draft = draft_for(ENTRY, renamed)
    assert draft.severity == FlagSeverity.low


def test_short_titles_never_establish_a_different_work():
    policy = load_different_work_policy()
    assert title_word_overlap("The GRIM test", "Unrelated words here entirely", policy) is None


def test_overlap_ignores_words_without_subject():
    policy = load_different_work_policy()
    assert title_word_overlap(
        "The use of the method in the field", "The role of the state in the market", policy
    ) == pytest.approx(0.0)


def test_no_comparison_or_agreeing_title_is_never_a_different_work():
    policy = load_different_work_policy()
    assert leads_to_different_work(None, policy) is None
    agrees = SimpleNamespace(
        title_status="agrees", author_status="differs", cited={}, registered={}
    )
    assert leads_to_different_work(agrees, policy) is None


def test_the_threshold_and_wording_are_data(tmp_path):
    packaged = json.loads(
        (
            __import__("pathlib").Path(__file__).parents[1]
            / "app/checks/citations/data/metadata_different_work.json"
        ).read_text(encoding="utf-8")
    )
    custom = tmp_path / "different_work.json"
    custom.write_text(
        json.dumps({**packaged, "max_title_word_overlap": 0.01, "severity": "low"}),
        encoding="utf-8",
    )
    other = record(
        "A validation study on the physiological parameters recorded by a wearable band",
        "Lu",
        2024,
    )
    draft = draft_for(
        ENTRY, other, Settings(_env_file=None, citation_metadata_different_work_file=custom)
    )
    # Overlap 0 is still under 0.01: named, at the severity the file sets.
    assert draft.detail.get("leads_to_different_work") is True
    assert draft.severity == FlagSeverity.low


OTHER = "A validation study on the physiological parameters recorded by a wearable band"


@pytest.mark.parametrize(
    "family",
    [
        None,  # the registry records no author
        "Yurov-Lu",  # a hyphenated variant sharing the surname
        "Yúrov",  # an accent the reference left off
    ],
)
def test_an_author_that_does_not_clearly_differ_keeps_it_low(family):
    draft = draft_for(ENTRY, record(OTHER, family, 2024))
    assert draft.severity == FlagSeverity.low
    assert "leads_to_different_work" not in draft.detail


def test_registry_markup_is_left_out_of_the_overlap_and_the_wording():
    marked = record(
        "<i>Drynaria quercifolia</i> rhizome &amp; wound healing in <scp>Wistar</scp> rats",
        "Lu",
        2024,
    )
    draft = draft_for(ENTRY, marked)
    assert draft.severity == FlagSeverity.med
    assert (
        "('Drynaria quercifolia rhizome & wound healing in Wistar rats', Lu, 2024)"
        in (draft.detail["reason"])
    )
    policy = load_different_work_policy()
    # Tags are not words: a two-word title stays too short to decide.
    assert (
        title_word_overlap(ENTRY, "<mml:math><mml:mi>b</mml:mi></mml:math>-Glucan", policy) is None
    )


def comparison(cited_title, registered_title, status="differs"):
    return SimpleNamespace(
        title_status="differs",
        author_status=status,
        cited={"title": cited_title, "first_author": "Santos, M."},
        registered={"title": registered_title, "first_author": "Lu"},
    )


def test_half_the_title_shared_is_the_boundary_and_stays_low():
    policy = load_different_work_policy()
    assert policy.max_title_word_overlap == 0.5
    half = comparison("Alpha beta gamma delta", "Alpha beta epsilon zeta")
    assert leads_to_different_work(half, policy) is None
    quarter = comparison("Alpha beta gamma delta", "Alpha epsilon zeta theta")
    assert leads_to_different_work(quarter, policy) == pytest.approx(0.25)


@pytest.mark.parametrize("status", ["not_recorded", "unassessed", "source_supported_sequence"])
def test_only_listed_author_statuses_count_as_differing(status):
    policy = load_different_work_policy()
    unrelated = comparison("Alpha beta gamma delta", "Epsilon zeta theta iota", status)
    assert leads_to_different_work(unrelated, policy) is None


def test_an_unreadable_policy_file_keeps_the_low_mismatch(tmp_path):
    settings = Settings(
        _env_file=None, citation_metadata_different_work_file=tmp_path / "missing.json"
    )
    draft = draft_for(ENTRY, record(OTHER, "Lu", 2024), settings)
    assert draft.severity == FlagSeverity.low
    assert "leads_to_different_work" not in draft.detail
