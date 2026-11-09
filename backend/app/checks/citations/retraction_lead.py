"""F5.4: OpenAlex's retraction signal as a lead to verify, never a finding.

Retraction is read from Crossref (BUG-610). OpenAlex is asked only when a
title search falls back to it, and its retraction signal is then offered to
the instructor as a Low lead to verify that never counts for readiness. A
retraction notice is described as a notice: citing one is not citing a
retracted work. A lead needs OpenAlex's own signal; a title alone never
raises one. Wording, notice detection and severity are data (BUG-640).
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.checks.citations.metadata import registry_plain_title

_DEFAULT_FILE = Path(__file__).parent / "data" / "openalex_retraction_lead.json"

POSSIBLE_RETRACTION = "possible_retraction_unconfirmed"
RETRACTION_NOTICE = "retraction_notice_cited"

_REASON_FIELDS = {"title": "t", "link": "l"}
_LINK_FIELDS = {"url": "u"}
_DOI_NOTICE_FIELDS = {"title": "t", "retracted": "r"}
_RETRACTED_LINK_FIELDS = {"doi": "d"}


class _PolicyData(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    notice_types: list[str]
    notice_title_patterns: list[str]
    severity: str = Field(pattern=r"^(low|med|high)$")
    retracted_work_reason: str = Field(min_length=1)
    retracted_work_reason_crossref_not_asked: str = Field(min_length=1)
    notice_reason: str = Field(min_length=1)
    link_doi: str = Field(min_length=1)
    link_record: str = Field(min_length=1)
    link_unknown: str = Field(min_length=1)
    doi_notice_reason: str = Field(min_length=1)
    retracted_link: str = Field(min_length=1)
    retracted_link_separator: str

    @model_validator(mode="after")
    def templates_fill(self):
        # A template naming a field this code does not supply would fail on
        # every lead; refuse the file instead, so no lead is raised at all.
        templates = [
            (t, _REASON_FIELDS)
            for t in (
                self.retracted_work_reason,
                self.retracted_work_reason_crossref_not_asked,
                self.notice_reason,
            )
        ] + [(t, _LINK_FIELDS) for t in (self.link_doi, self.link_record, self.link_unknown)]
        templates += [
            (self.doi_notice_reason, _DOI_NOTICE_FIELDS),
            (self.retracted_link, _RETRACTED_LINK_FIELDS),
        ]
        for template, fields in templates:
            # Filled with empty values too, as an empty title would be.
            for values in (fields, dict.fromkeys(fields, "")):
                try:
                    template.format(**values)
                except Exception as exc:  # any template the lead cannot fill
                    raise ValueError(f"Unusable template: {template!r}") from exc
        for pattern in self.notice_title_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Invalid notice title pattern: {pattern!r}") from exc
        return self


@dataclass(frozen=True)
class RetractionLeadPolicy:
    notice_types: frozenset[str]
    notice_title_patterns: tuple[re.Pattern[str], ...]
    severity: str
    retracted_work_reason: str
    retracted_work_reason_crossref_not_asked: str
    notice_reason: str
    link_doi: str
    link_record: str
    link_unknown: str
    doi_notice_reason: str
    retracted_link: str
    retracted_link_separator: str


@lru_cache(maxsize=4)
def load_retraction_lead_policy(path: Path | None = None) -> RetractionLeadPolicy:
    raw = _PolicyData.model_validate(
        json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    )
    return RetractionLeadPolicy(
        frozenset(t.casefold() for t in raw.notice_types),
        tuple(re.compile(p, re.IGNORECASE) for p in raw.notice_title_patterns),
        raw.severity,
        raw.retracted_work_reason,
        raw.retracted_work_reason_crossref_not_asked,
        raw.notice_reason,
        raw.link_doi,
        raw.link_record,
        raw.link_unknown,
        raw.doi_notice_reason,
        raw.retracted_link,
        raw.retracted_link_separator,
    )


def _link(url, policy: RetractionLeadPolicy) -> str:
    if not isinstance(url, str) or not url.startswith("https://"):
        return policy.link_unknown
    if url.startswith("https://doi.org/"):
        return policy.link_doi.format(url=url)
    return policy.link_record.format(url=url)


def openalex_retraction_lead(result, policy: RetractionLeadPolicy) -> dict | None:
    """The lead's kind, wording and evidence for an OpenAlex record, or None.

    Only an OpenAlex record is read, and only one OpenAlex itself marks as
    retracted or types as a retraction notice."""
    if result is None or getattr(result, "provider", None) != "openalex":
        return None
    raw = result.raw if isinstance(result.raw, dict) else {}
    record_type = raw.get("type") if isinstance(raw.get("type"), str) else ""
    typed_notice = record_type.casefold() in policy.notice_types
    if raw.get("is_retracted") is not True and not typed_notice:
        return None
    title = registry_plain_title(result.title) if isinstance(result.title, str) else ""
    notice = typed_notice or any(p.search(title) for p in policy.notice_title_patterns)
    if notice:
        template = policy.notice_reason
    elif "crossref" in (getattr(result, "attempted_providers", None) or []):
        template = policy.retracted_work_reason
    else:
        template = policy.retracted_work_reason_crossref_not_asked
    url = result.url if isinstance(result.url, str) else None
    return {
        "kind": RETRACTION_NOTICE if notice else POSSIBLE_RETRACTION,
        "reason": template.format(title=title, link=_link(url, policy)),
        "provider": "openalex",
        "registry_title": title or None,
        "registry_record_url": url,
        "registry_record_type": record_type or None,
        "openalex_is_retracted": raw.get("is_retracted") is True,
        "affects_readiness": False,
    }


def doi_notice_lead(result, retracted_dois, policy: RetractionLeadPolicy) -> dict:
    """The lead for an entry whose DOI leads to a retraction notice: the same
    kind as a notice OpenAlex lists, never the counted retracted finding."""
    title = registry_plain_title(result.title) if isinstance(result.title, str) else ""
    retracted = policy.retracted_link_separator.join(
        policy.retracted_link.format(doi=doi) for doi in retracted_dois
    )
    return {
        "kind": RETRACTION_NOTICE,
        "reason": policy.doi_notice_reason.format(title=title, retracted=retracted),
        "provider": result.provider,
        "registry_title": title or None,
        "retracted_dois": list(retracted_dois),
        "affects_readiness": False,
    }
