"""F5.4: OpenAlex's retraction signal as a lead to verify, never a finding.

Retraction is read from Crossref (BUG-610). OpenAlex is asked only when a
title search falls back to it, and its `is_retracted` flag is then offered to
the instructor as a Low "possible retraction, please verify" lead that never
counts for readiness. A retraction notice is described as a notice: citing
one is not citing a retracted work. Wording, notice detection and severity
are data (BUG-640).
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.checks.citations.metadata import registry_plain_title

_DEFAULT_FILE = Path(__file__).parent / "data" / "openalex_retraction_lead.json"

POSSIBLE_RETRACTION = "possible_retraction_unconfirmed"
RETRACTION_NOTICE = "retraction_notice_cited"


class _PolicyData(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    notice_types: list[str]
    notice_title_patterns: list[str]
    severity: str = Field(pattern=r"^(low|med|high)$")
    retracted_work_reason: str = Field(min_length=1)
    notice_reason: str = Field(min_length=1)
    unknown_url: str = Field(min_length=1)


@dataclass(frozen=True)
class RetractionLeadPolicy:
    notice_types: frozenset[str]
    notice_title_patterns: tuple[re.Pattern[str], ...]
    severity: str
    retracted_work_reason: str
    notice_reason: str
    unknown_url: str


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
        raw.notice_reason,
        raw.unknown_url,
    )


def openalex_retraction_lead(result, policy: RetractionLeadPolicy) -> dict | None:
    """The lead's kind, wording and evidence for an OpenAlex record, or None.

    Only an OpenAlex record is read; any other registry's answer is None."""
    if result is None or getattr(result, "provider", None) != "openalex":
        return None
    raw = result.raw if isinstance(result.raw, dict) else {}
    title = registry_plain_title(result.title) if isinstance(result.title, str) else None
    record_type = raw.get("type") if isinstance(raw.get("type"), str) else ""
    notice = record_type.casefold() in policy.notice_types or bool(
        title and any(p.search(title) for p in policy.notice_title_patterns)
    )
    if not notice and raw.get("is_retracted") is not True:
        return None
    url = result.url if isinstance(result.url, str) and result.url.startswith("http") else None
    template = policy.notice_reason if notice else policy.retracted_work_reason
    return {
        "kind": RETRACTION_NOTICE if notice else POSSIBLE_RETRACTION,
        "reason": template.format(title=title or "", url=url or policy.unknown_url),
        "provider": "openalex",
        "registry_title": title,
        "registry_record_url": url,
        "registry_record_type": record_type or None,
        "openalex_is_retracted": raw.get("is_retracted") is True,
        "affects_readiness": False,
    }
