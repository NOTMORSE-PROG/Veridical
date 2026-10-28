"""F5/F8: immutable reference locators for deliberate external navigation."""

import hashlib
from dataclasses import replace
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError

from app.checks.citations.metadata import _record_url, same_doi
from app.config import get_settings
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns
from app.ingest.reference_urls import supported_reference_url
from app.ingest.references import possible_split_doi
from app.models.citation import Citation


class SavedReferenceLocator(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal["citation-source-v1"]
    order_index: int = Field(ge=0)
    finding_kind: Literal[
        "uncited_reference",
        "retracted_source",
        "corrected_source",
        "unverifiable_api_down",
        "unverifiable_not_found",
    ]
    reference_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    doi: str | None = None
    cited_url: str | None = None


class SavedClaimReferenceLocator(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal["citation-claim-source-v1"]
    finding_kind: Literal[
        "claim_possibly_unsupported",
        "claim_support_cannot_determine",
        "claim_support_injection_suspected",
    ]
    reference_order_index: int = Field(ge=0)
    claim_support_pair_index: int = Field(ge=0)
    claim_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reference_text: str = Field(min_length=1)
    reference_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    doi: str | None = None
    cited_url: str | None = None


def with_claim_reference_locator(draft, citation, claim, pair_index):
    """Snapshot the owning pair, without replacing its manuscript evidence."""
    reference_text = getattr(citation, "raw_text", None)
    if (
        not isinstance(claim, str)
        or not claim.strip()
        or draft.evidence_excerpt != claim
        or citation is None
        or not isinstance(reference_text, str)
        or not reference_text.strip()
        or type(citation.order_index) is not int
        or type(pair_index) is not int
        or type(draft.detail.get("reference_order_index")) is not int
        or type(draft.detail.get("claim_support_pair_index")) is not int
        or draft.detail["reference_order_index"] != citation.order_index
        or draft.detail["claim_support_pair_index"] != pair_index
    ):
        return draft
    try:
        saved = SavedClaimReferenceLocator(
            schema_version="citation-claim-source-v1",
            finding_kind=draft.detail.get("kind"),
            reference_order_index=citation.order_index,
            claim_support_pair_index=pair_index,
            claim_text_sha256=hashlib.sha256(claim.encode()).hexdigest(),
            reference_text=citation.raw_text,
            reference_text_sha256=hashlib.sha256(citation.raw_text.encode()).hexdigest(),
            doi=citation.doi,
            cited_url=citation.url,
        )
    except ValidationError:
        return draft
    return replace(draft, detail={**draft.detail, "citation_source_locator": saved.model_dump()})


def with_reference_locator(draft, citation):
    """The actual reference must own the entire saved evidence excerpt."""
    if (
        citation is None
        or type(citation.order_index) is not int
        or citation.order_index < 0
        or not isinstance(citation.raw_text, str)
        or citation.raw_text != draft.evidence_excerpt
        or draft.detail.get("kind") == "possible_metadata_mismatch"
    ):
        return draft
    recorded_index = draft.detail.get("order_index", citation.order_index)
    if type(recorded_index) is not int or recorded_index != citation.order_index:
        return draft
    try:
        snapshot = SavedReferenceLocator(
            schema_version="citation-source-v1",
            order_index=citation.order_index,
            finding_kind=draft.detail.get("kind"),
            reference_text_sha256=hashlib.sha256(citation.raw_text.encode()).hexdigest(),
            doi=citation.doi,
            cited_url=citation.url,
        )
    except ValidationError:
        return draft
    return replace(draft, detail={**draft.detail, "citation_source_locator": snapshot.model_dump()})


def _doi_url(doi, raw, settings):
    if (
        not isinstance(doi, str)
        or len(doi) > Citation.__table__.c.doi.type.length
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in doi)
        or "\\" in doi
        or any(part in {".", ".."} for part in doi.split("/"))
        or possible_split_doi(raw, doi)
    ):
        return None
    url = _record_url(settings.citation_doi_source_url_template, doi)
    if not url or not supported_reference_url(url):
        return None
    parsed = urlsplit(url)
    patterns = load_reference_metadata_patterns(settings.reference_metadata_patterns_file)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in patterns.doi_resolver_hosts
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not same_doi(unquote(parsed.path.removeprefix("/")), doi)
    ):
        return None
    return url


def _safe_url(value):
    if not value or not supported_reference_url(value):
        return None
    try:
        return str(TypeAdapter(HttpUrl).validate_python(value))
    except (ValidationError, ValueError):
        return None


def citation_source_locator(check_kind, raw, detail, *, settings=None):
    """Read only a bound saved snapshot; never query current citations or providers."""
    if (
        check_kind != "citation_integrity"
        or not isinstance(raw, str)
        or not isinstance(detail, dict)
    ):
        return None
    if detail.get("kind") == "possible_metadata_mismatch":
        return None
    if "citation_source_locator" not in detail:
        if detail.get("kind") != "unverifiable_not_found" or detail.get("key_kind") != "doi":
            return None
        try:
            doi_url = _doi_url(detail.get("key_value"), raw, settings or get_settings())
        except (OSError, ValueError, UnicodeError):
            return None
        return (
            {
                "basis": "recorded_source_key",
                "order_index": None,
                "links": [{"kind": "doi_recorded_lookup", "url": doi_url}],
            }
            if doi_url
            else None
        )
    snapshot = detail.get("citation_source_locator")
    claim_snapshot = (
        isinstance(snapshot, dict) and snapshot.get("schema_version") == "citation-claim-source-v1"
    )
    try:
        saved = (
            SavedClaimReferenceLocator if claim_snapshot else SavedReferenceLocator
        ).model_validate(snapshot)
    except ValidationError:
        return None
    if claim_snapshot:
        if (
            saved.claim_text_sha256 != hashlib.sha256(raw.encode()).hexdigest()
            or saved.reference_text_sha256
            != hashlib.sha256(saved.reference_text.encode()).hexdigest()
            or type(detail.get("reference_order_index")) is not int
            or type(detail.get("claim_support_pair_index")) is not int
            or detail["reference_order_index"] != saved.reference_order_index
            or detail["claim_support_pair_index"] != saved.claim_support_pair_index
        ):
            return None
        reference_text, order_index = saved.reference_text, saved.reference_order_index
    else:
        if saved.reference_text_sha256 != hashlib.sha256(raw.encode()).hexdigest():
            return None
        reference_text, order_index = raw, saved.order_index
    if saved.finding_kind != detail.get("kind"):
        return None
    if "order_index" in detail and (
        type(detail["order_index"]) is not int or detail["order_index"] != order_index
    ):
        return None
    links = []
    try:
        doi_url = _doi_url(saved.doi, reference_text, settings or get_settings())
    except (OSError, ValueError, UnicodeError):
        doi_url = None
    if doi_url:
        links.append({"kind": "doi_as_cited", "url": doi_url})
    cited_url = _safe_url(saved.cited_url)
    if cited_url and not any(link["url"] == cited_url for link in links):
        links.append({"kind": "url_as_cited", "url": cited_url})
    return (
        {"basis": "saved_reference", "order_index": order_index, "links": links} if links else None
    )
