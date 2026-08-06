"""Shared result shape every provider client returns (V-028) — V-029's
existence/retraction check reads this, never a provider's raw response
shape directly, so adding a fifth provider later never touches V-029.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerificationResult:
    """`found=False` and `retracted=False` both mean exactly what they say
    and nothing more — "not found" is `unverifiable`, never a claim the
    source doesn't exist (charter rule 3: never "fake")."""

    found: bool
    provider: str  # "crossref" | "s2" | "openlibrary" | "gbooks"
    title: str | None = None
    retracted: bool = False
    retraction_detail: str | None = None
    is_correction: bool = False  # a Crossref "correction" relation, not a retraction
    # False for books/paywalled sources reached only via existence metadata
    # (ISBN/title match) with no way to check the cited content itself —
    # ticket AC: "books/paywalled = existence-only + 'content not checkable'".
    content_checkable: bool = True
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
