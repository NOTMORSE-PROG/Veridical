"""F5 exact-DOI title controls: formatting uncertainty cannot verify identity."""

import json
from pathlib import Path

import httpx
import pytest

from app.checks.citations.metadata import _record_url, compare_metadata
from app.checks.citations.title_policy import compare_titles, load_title_policy
from app.checks.citations.verify import VerdictKind, _verdict_flag_draft, verify_citation
from app.config import Settings
from app.external import cache
from app.external.schemas import VerificationResult
from tests.test_doi_provider_identity import DOI, citation


@pytest.mark.parametrize(
    "left,right",
    [
        ("Nanometer thermometry", "Nanometre thermometry"),
        ("Centimeters and microliters", "Centimetres and microlitres"),
        ("Café measurements", "Cafe\u0301 measurements"),
        ("Nano\u00admetre thermometry", "Nanometre thermometry"),
        ("A measured result.", "A measured result"),
        ("Graduate students’ perspectives", "Graduate Students' Perspectives"),
    ],
)
def test_bounded_spelling_and_canonical_equivalence(left, right):
    assert compare_titles(left, right, load_title_policy()) == ("agrees", None)


@pytest.mark.parametrize(
    "left,right",
    [
        ("C++ evaluation", "C evaluation"),
        ("H+ transport", "H- transport"),
        ("(A+B)*C", "A+B*C"),
        ("Velocity m/s", "Velocity m s"),
        ("H′ measurements", "H measurements"),
        ("A:B evaluation", "A B evaluation"),
        ("CO₂ measurements", "CO2 measurements"),
        ("x² results", "x2 results"),
        ("1.2 results", "12 results"),
        ("Study 2", "Study 3"),
        ("No effect observed", "Effect observed"),
        ("Alpha beta", "Beta alpha"),
        ("5 mW power", "5 MW power"),
        ("10 pA current", "10 PA current"),
        ("Meters observed", "Metre observed"),
        ("Nanometre count", "Nanometre counts"),
        ("Nanmeter measurements", "Nanometre measurements"),
        ("Student's perspectives", "Students' perspectives"),
        ("Students′ perspectives", "Students' perspectives"),
    ],
)
def test_meaningful_differences_are_preserved(left, right):
    assert compare_titles(left, right, load_title_policy()) == ("differs", None)


@pytest.mark.parametrize(
    "left,right,reason",
    [
        (
            "Nanometer scale thermometry",
            "Nanometre-scale thermometry",
            "title_hyphenation_unassessed",
        ),
        ("A-B", "A B", "title_hyphenation_unassessed"),
        ("VARIABLE values", "Variable values", "title_case_unassessed"),
        (
            "Philippine graduate students’ perspectives on CMO No. 15 Series 2019 "
            "as basis for a conceptual model",
            "Philippine Graduate Students' Perspectives On CMO No. 15 Series 2019 "
            "As Basis For A Conceptual Model",
            "title_case_unassessed",
        ),
        ("A parameter", "a parameter", "title_case_unassessed"),
        ("", "", "title_metadata_unavailable"),
        ("...", "...", "title_metadata_unavailable"),
        (None, "A source", "title_metadata_unavailable"),
        ("5mW power", "5MW power", "title_case_unassessed"),
        ("10pA current", "10PA current", "title_case_unassessed"),
        ("mW2 output", "MW2 output", "title_case_unassessed"),
        ("pH7 measurements", "ph7 measurements", "title_case_unassessed"),
        ("E=mc2", "E=mC2", "title_case_unassessed"),
        ("mW² output", "MW² output", "title_case_unassessed"),
        ("α parameter", "Α parameter", "title_case_unassessed"),
        ("γ parameter", "Γ parameter", "title_case_unassessed"),
        ("αβ parameters", "ΑΒ parameters", "title_case_unassessed"),
        ("αβ parameters", "αΒ parameters", "title_case_unassessed"),
        ("Maße in physics", "Masse in physics", "title_case_unassessed"),
        ("ſine measurements", "sine measurements", "title_case_unassessed"),
    ],
)
def test_ambiguity_is_unassessed_never_agreement(left, right, reason):
    assert compare_titles(left, right, load_title_policy()) == ("unassessed", reason)


def test_original_wrong_clean_control_retains_all_genuine_differences():
    c = citation("A genuine study on capstone quality", 2026)
    c.authors = ["Reyes, J. P."]
    r = VerificationResult(
        found=True,
        provider="crossref",
        title="Nanometre-scale thermometry in a living cell",
        raw={
            "DOI": DOI,
            "published": {"date-parts": [[2013]]},
            "author": [{"family": "Kucsko"}],
        },
    )
    comparison = compare_metadata(c, r)
    assert comparison.differing_fields == ("title", "year", "first author")
    assert comparison.evidence()["registered"]["first_author"] == "Kucsko"
    with pytest.raises(TypeError):
        comparison.registered["title"] = "rewritten"


@pytest.mark.parametrize("join", [" and ", " & "])
def test_joint_corporate_metadata_uses_complete_registered_author_sequence(join):
    expression = join.join(["World Health Organization", "Food and Agriculture Organization"])
    c = citation("Joint guidance", 2024)
    c.authors = [expression]
    registered = VerificationResult(
        found=True,
        provider="crossref",
        title="Joint guidance",
        raw={
            "DOI": DOI,
            "published": {"date-parts": [[2024]]},
            "author": [
                {"family": "World Health Organization"},
                {"family": "Food and Agriculture Organization"},
            ],
        },
    )
    result = compare_metadata(c, registered)
    assert result.differing_fields == ()
    assert result.author_status == "source_supported_sequence"
    assert result.cited["first_author"] == expression


def test_uncertain_corporate_boundary_is_not_reported_as_wrong_first_author():
    c = citation("Joint guidance", 2024)
    c.authors = ["Institute of Research and Development Agency"]
    registered = VerificationResult(
        found=True,
        provider="crossref",
        title="Joint guidance",
        raw={
            "DOI": DOI,
            "published": {"date-parts": [[2024]]},
            "author": [{"family": "Institute of Research"}],
        },
    )
    result = compare_metadata(c, registered)
    assert result.differing_fields == ()
    assert result.author_status == "unassessed"
    assert result.evidence()["author_unassessed_reason"] == "author_boundaries_unassessed"


def policy_data():
    return json.loads(
        (
            Path(__file__).parents[1] / "app/checks/citations/data/metadata_title_policy.json"
        ).read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "change",
    [
        {"equivalent_words": [["meter", "metre"], ["METRE", "other"]]},
        {"equivalent_words": [["meter", "meter"]]},
        {"equivalent_words": [["meter", "m etre"]]},
        {"equivalent_words": [["meter", "m2"]]},
        {"ignored_characters": ["+"]},
        {"terminal_characters": ["x"]},
        {"terminal_characters": ["-"]},
        {"case_sensitive_patterns": [".*"]},
        {"unexpected": True},
        {"equivalent_punctuation": [["'", "’"], ["’", "′"]]},
        {"equivalent_punctuation": [["'", "'"]]},
        {"equivalent_punctuation": [["'", "word"]]},
        {"equivalent_punctuation": [["'", "."]]},
    ],
)
def test_invalid_policy_rejected(tmp_path, change):
    data = policy_data() | change
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_title_policy(path)


@pytest.mark.parametrize(
    "template",
    [
        "https://doi.org/{}",
        "https://doi.org/{0}",
        "https://doi.org/{doi.missing}",
        "https://doi.org/{other}",
        "javascript:{doi}",
    ],
)
def test_unusable_optional_link_template_cannot_abort_comparison(template):
    assert _record_url(template, DOI) is None


@pytest.mark.asyncio
async def test_invalid_local_policy_does_not_spend_provider_calls(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text("{}", encoding="utf-8")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("no network"))
    ) as client:
        verdict = await verify_citation(
            None, client, citation(), settings=Settings(citation_metadata_title_policy_file=path)
        )
    assert verdict.kind == VerdictKind.unassessed
    assert verdict.unassessed_reason == "metadata_comparison_policy_unavailable"


@pytest.mark.asyncio
async def test_exact_verdict_keeps_policy_and_comparison_snapshot(monkeypatch, tmp_path):
    async def empty(*args, **kwargs):
        return None

    monkeypatch.setattr(cache, "get_cached", empty)
    monkeypatch.setattr(cache, "store_result", empty)
    monkeypatch.setattr(cache, "is_instructor_confirmed", empty)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy_data()), encoding="utf-8")
    settings = Settings(citation_metadata_title_policy_file=path)
    r = {
        "DOI": DOI,
        "title": ["Different registered title"],
        "published": {"date-parts": [[2018]]},
        "author": [{"family": "Smith"}],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"message": r}))
    ) as client:
        verdict = await verify_citation(None, client, citation(), settings=settings)
    before = verdict.metadata_comparison.evidence()
    path.write_text("{}", encoding="utf-8")
    draft = _verdict_flag_draft(citation(), verdict)
    assert draft.detail["metadata_comparison"] == before
    assert draft.detail["differing_fields"] == ["title"]
    assert len(before["policy_sha256"]) == 64
    assert before["cited"]["title"] == "A source"


@pytest.mark.parametrize(
    ("registered", "cited"),
    [
        (
            "Phytochemical screening of rhizome of <i>Drynaria quercifolia</i>",
            "Phytochemical screening of rhizome of Drynaria quercifolia",
        ),
        ("Uptake of CO<sub>2</sub> in soils", "Uptake of CO2 in soils"),
        ("<scp>Sensing</scp> comfort &amp; health", "Sensing comfort & health"),
        (
            'Growth of <mml:math><mml:mi>x</mml:mi></mml:math> in <i class="a">vitro</i>',
            "Growth of x in vitro",
        ),
    ],
)
def test_registry_title_markup_is_not_a_title_difference(registered, cited):
    c = citation(cited, 2020)
    r = VerificationResult(
        found=True,
        provider="crossref",
        title=registered,
        raw={"DOI": DOI, "published": {"date-parts": [[2020]]}},
    )
    comparison = compare_metadata(c, r)
    assert comparison.title_status == "agrees"
    assert "title" not in comparison.differing_fields


def test_registry_markup_removal_keeps_real_differences_and_plain_angle_text():
    from app.checks.citations.metadata import registry_plain_title

    assert registry_plain_title("Results for a <5 year horizon") == (
        "Results for a <5 year horizon"
    )
    c = citation("A different title", 2020)
    r = VerificationResult(
        found=True,
        provider="crossref",
        title="Phytochemical screening of <i>Drynaria quercifolia</i>",
        raw={"DOI": DOI, "published": {"date-parts": [[2020]]}},
    )
    assert compare_metadata(c, r).differing_fields == ("title",)
