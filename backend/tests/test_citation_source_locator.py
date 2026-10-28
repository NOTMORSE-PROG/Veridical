"""Saved locator producer/projection; no provider, storage, DB or PDF operations."""

from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from app.checks.citations.source_locator import (
    citation_source_locator,
    with_claim_reference_locator,
    with_reference_locator,
)
from app.config import Settings


@dataclass(frozen=True)
class Draft:
    evidence_excerpt: str
    detail: dict


def saved(
    *,
    raw="Author. A source. DOI 10.1234/example",
    doi="10.1234/example",
    url=None,
    index=0,
    kind="uncited_reference",
):
    citation = SimpleNamespace(raw_text=raw, doi=doi, url=url, order_index=index)
    draft = Draft(raw, {"kind": kind, "order_index": index})
    return citation, with_reference_locator(draft, citation)


def project(draft, **kwargs):
    return citation_source_locator(
        "citation_integrity", draft.evidence_excerpt, draft.detail, **kwargs
    )


def claim_saved(kind="claim_possibly_unsupported", *, doi="10.1234/example", index=2, pair=7):
    citation = SimpleNamespace(
        raw_text="Author. Source DOI 10.1234/example",
        doi=doi,
        url="https://example.org/source",
        order_index=index,
    )
    claim = "This manuscript claim needs source comparison."
    draft = Draft(
        claim, dict(kind=kind, reference_order_index=index, claim_support_pair_index=pair)
    )
    return citation, with_claim_reference_locator(draft, citation, claim, pair)


@pytest.mark.parametrize(
    "kind",
    [
        "claim_possibly_unsupported",
        "claim_support_cannot_determine",
        "claim_support_injection_suspected",
    ],
)
def test_claim_snapshot_keeps_own_reference_when_current_citation_changes(kind):
    citation, draft = claim_saved(kind)
    citation.doi, citation.raw_text, citation.order_index = "10.9999/changed", "Other work", 100
    result = project(draft)
    assert result["basis"] == "saved_reference" and result["order_index"] == 2
    assert result["links"][0] == {
        "kind": "doi_as_cited",
        "url": "https://doi.org/10.1234%2Fexample",
    }
    assert draft.evidence_excerpt == "This manuscript claim needs source comparison."
    assert "reference_text" not in result


@pytest.mark.parametrize(
    "change",
    [
        "claim",
        "reference",
        "claim_hash",
        "reference_hash",
        "kind",
        "ref_index",
        "pair_index",
        "schema",
        "extra",
        "bool_index",
        "missing",
    ],
)
def test_claim_snapshot_rebinding_or_corruption_withholds_link(change):
    _, draft = claim_saved()
    detail = {
        **draft.detail,
        "citation_source_locator": dict(draft.detail["citation_source_locator"]),
    }
    snapshot = detail["citation_source_locator"]
    raw = draft.evidence_excerpt
    if change == "claim":
        raw += " changed"
    elif change == "reference":
        snapshot["reference_text"] += " changed"
    elif change == "claim_hash":
        snapshot["claim_text_sha256"] = "0" * 64
    elif change == "reference_hash":
        snapshot["reference_text_sha256"] = "0" * 64
    elif change == "kind":
        detail["kind"] = "claim_support_cannot_determine"
    elif change == "ref_index":
        detail["reference_order_index"] += 1
    elif change == "pair_index":
        detail["claim_support_pair_index"] += 1
    elif change == "schema":
        snapshot["schema_version"] = "unknown"
    elif change == "extra":
        snapshot["extra"] = "untrusted"
    elif change == "bool_index":
        snapshot["reference_order_index"] = True
    elif change == "missing":
        del snapshot["claim_support_pair_index"]
    assert project(Draft(raw, detail)) is None


@pytest.mark.parametrize(
    "index,pair", [(True, 1), (1, False), (-1, 1), (1, -1), ("1", 1), (1, "1")]
)
def test_claim_producer_does_not_guess_invalid_pair_identity(index, pair):
    _, draft = claim_saved(index=index, pair=pair)
    assert "citation_source_locator" not in draft.detail


def test_old_claim_and_doi_in_reason_never_gain_new_source_provenance():
    _, draft = claim_saved()
    assert (
        project(
            Draft(
                draft.evidence_excerpt,
                {"kind": "claim_possibly_unsupported", "reason": "See DOI10.1234/example"},
            )
        )
        is None
    )


def test_claim_doi_validation_uses_the_reference_not_the_claim():
    citation, draft = claim_saved()
    citation.raw_text = "Author. Source DOI 10.1234/example tail"
    citation.url = None
    draft = with_claim_reference_locator(draft, citation, draft.evidence_excerpt, 7)
    assert project(draft) is None


def test_saved_reference_is_immutable_when_current_citation_changes():
    citation, draft = saved(url="https://example.org/reference")
    citation.raw_text = "Different reference"
    citation.doi = "10.5678/different"
    citation.url = "https://example.org/different"
    result = project(draft)
    assert result["basis"] == "saved_reference" and result["order_index"] == 0
    assert result["links"] == [
        {"kind": "doi_as_cited", "url": "https://doi.org/10.1234%2Fexample"},
        {"kind": "url_as_cited", "url": "https://example.org/reference"},
    ]


@pytest.mark.parametrize(
    "kind", ["orphan_in_text_citation", "claim_not_supported", "possible_metadata_mismatch"]
)
def test_non_reference_findings_do_not_gain_locator(kind):
    _, draft = saved(kind=kind)
    assert "citation_source_locator" not in draft.detail
    assert project(draft) is None


@pytest.mark.parametrize("index", [True, False, -1, "0", 0.5, None])
def test_producer_rejects_non_integer_or_negative_reference_identity(index):
    _, draft = saved(index=index)
    assert "citation_source_locator" not in draft.detail


@pytest.mark.parametrize(
    "change", ["excerpt", "hash", "index", "kind", "schema", "missing schema", "boolean index"]
)
def test_projection_rejects_rebound_or_malformed_snapshot(change):
    _, draft = saved()
    detail = {
        **draft.detail,
        "citation_source_locator": dict(draft.detail["citation_source_locator"]),
    }
    raw = draft.evidence_excerpt
    if change == "excerpt":
        raw += " changed"
    if change == "hash":
        detail["citation_source_locator"]["reference_text_sha256"] = "0" * 64
    if change == "index":
        detail["order_index"] = 1
    if change == "boolean index":
        detail["citation_source_locator"]["order_index"] = False
    if change == "kind":
        detail["kind"] = "orphan_in_text_citation"
    if change == "schema":
        detail["citation_source_locator"]["schema_version"] = "future"
    if change == "missing schema":
        del detail["citation_source_locator"]["schema_version"]
    assert project(Draft(raw, detail)) is None


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/plain,test",
        "file:///local",
        "https://u:p@example.org/path",
        "https://example.org/white space",
        "https://example.org/line\nbreak",
        "https://example.org\\wrong",
        "https://example.org:bad/",
        "https://example.org:0/",
    ],
)
def test_unsafe_printed_url_is_not_exposed(url):
    _, draft = saved(doi=None, url=url)
    assert project(draft) is None


@pytest.mark.parametrize(
    "template",
    [
        "https://example.org/{doi}",
        "https://doi.org/other/{doi}",
        "https://doi.org/{doi}?tracking=1",
        "http://doi.org/{doi}",
        "https://user:pass@doi.org/{doi}",
    ],
)
def test_doi_label_requires_configured_resolver_and_same_identifier(template):
    _, draft = saved()
    assert project(draft, settings=Settings(citation_doi_source_url_template=template)) is None


def test_split_doi_does_not_get_an_exact_navigation_link():
    _, draft = saved(raw="Author. DOI 10.1234/example tail")
    assert project(draft) is None


def test_valid_url_survives_unavailable_doi_and_non_citation_kind_is_rejected():
    _, draft = saved(doi=None, url="https://example.org/reference")
    assert project(draft)["links"] == [
        {"kind": "url_as_cited", "url": "https://example.org/reference"}
    ]
    assert (
        citation_source_locator("internal_agreement", draft.evidence_excerpt, draft.detail) is None
    )


def test_snapshot_requires_exact_full_reference_text_and_order():
    citation, draft = saved()
    source = Draft(
        draft.evidence_excerpt + "extra", {"kind": "uncited_reference", "order_index": 0}
    )
    assert with_reference_locator(source, citation) is source
    source = Draft(draft.evidence_excerpt, {"kind": "uncited_reference", "order_index": 2})
    assert with_reference_locator(source, citation) is source
    legacy = project(
        replace(
            draft,
            detail={
                "kind": "unverifiable_not_found",
                "key_kind": "doi",
                "key_value": "10.1234/example",
            },
        )
    )
    assert legacy == {
        "basis": "recorded_source_key",
        "order_index": None,
        "links": [{"kind": "doi_recorded_lookup", "url": "https://doi.org/10.1234%2Fexample"}],
    }


@pytest.mark.parametrize(
    "url", ["https://exa%mple.org/path", "https://%00.example/path", "https://[::1%25eth0]/path"]
)
def test_invalid_http_host_cannot_break_flag_dto_and_keeps_valid_doi(url):
    from app.flags.schemas import CitationSourceLocatorOut

    _, draft = saved(url=url)
    result = project(draft)
    dto = CitationSourceLocatorOut.model_validate(result)
    assert len(dto.links) == 1 and dto.links[0].kind == "doi_as_cited"


def lookup_detail(**changes):
    return {
        "kind": "unverifiable_not_found",
        "key_kind": "doi",
        "key_value": "10.1234/example",
        **changes,
    }


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        {"schema_version": "future"},
        {"schema_version": "citation-source-v1"},
        {"reference_text_sha256": "0" * 64},
    ],
)
def test_present_invalid_snapshot_never_falls_back_to_lookup_key(snapshot):
    assert (
        citation_source_locator(
            "citation_integrity", "Reference", lookup_detail(citation_source_locator=snapshot)
        )
        is None
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "uncited_reference"},
        {"kind": "orphan_in_text_citation"},
        {"kind": "claim_not_supported"},
        {"kind": "possible_metadata_mismatch"},
        {"key_kind": "title"},
        {"key_kind": "isbn"},
        {"key_value": None},
        {"key_value": 42},
        {"key_value": "10.1234/a\\b"},
        {"key_value": "10.1234/a\nb"},
        {"key_value": "10.1234/../a"},
        {"key_value": "10.1234/a b"},
    ],
)
def test_lookup_navigation_withholds_wrong_kind_or_unsafe_identifier(changes):
    assert (
        citation_source_locator("citation_integrity", "Reference", lookup_detail(**changes)) is None
    )


def test_lookup_is_not_relabelled_as_cited_and_keeps_balanced_punctuation():
    from app.flags.schemas import CitationSourceLocatorOut

    result = citation_source_locator(
        "citation_integrity",
        "An older excerpt without the identifier",
        lookup_detail(key_value="10.1234/example(2026)"),
    )
    dto = CitationSourceLocatorOut.model_validate(result)
    assert dto.basis == "recorded_source_key" and dto.order_index is None
    assert dto.links[0].kind == "doi_recorded_lookup"
    assert str(dto.links[0].url) == "https://doi.org/10.1234%2Fexample%282026%29"


@pytest.mark.parametrize(
    "basis,index,kind",
    [
        ("recorded_source_key", 0, "doi_recorded_lookup"),
        ("recorded_source_key", None, "doi_as_cited"),
        ("recorded_source_key", None, "url_as_cited"),
        ("saved_reference", None, "doi_as_cited"),
        ("saved_reference", 0, "doi_recorded_lookup"),
        ("saved_reference", True, "doi_as_cited"),
    ],
)
def test_output_contract_rejects_misleading_basis_combinations(basis, index, kind):
    from pydantic import ValidationError

    from app.flags.schemas import CitationSourceLocatorOut

    with pytest.raises(ValidationError):
        CitationSourceLocatorOut.model_validate(
            {
                "basis": basis,
                "order_index": index,
                "links": [{"kind": kind, "url": "https://doi.org/10.1234%2Fexample"}],
            }
        )


@pytest.mark.parametrize("owns_detail", [True, False])
async def test_flag_assembly_never_inherits_another_flags_parent_lookup(owns_detail):
    from app.flags.service import _to_flag_out

    detail = lookup_detail()
    flag = SimpleNamespace(
        id=1,
        detail=detail if owns_detail else None,
        evidence_excerpt="Reference",
        page_anchor="page 1",
        annotation=None,
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
        kind=SimpleNamespace(value="citation_integrity"),
        detail=detail,
    )
    dto = await _to_flag_out(
        None,
        flag,
        result,
        SimpleNamespace(group_label="Own manuscript"),
        SimpleNamespace(llm_mode=SimpleNamespace(value="fake")),
    )
    if owns_detail:
        assert dto.citation_source_locator.basis == "recorded_source_key"
        assert dto.citation_source_locator.order_index is None
    else:
        assert dto.citation_source_locator is None
