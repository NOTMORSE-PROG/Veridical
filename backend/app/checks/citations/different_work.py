"""F5.2: whether a DOI whose registry title differs leads to a different work.

A metadata mismatch is usually drift: a subtitle the registry leaves out, a
print year, a name order. A DOI that resolves to another paper entirely is a
different finding, and usually a typo in the DOI. They are told apart by how
much of the title the two records share, with the threshold kept as data.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.checks.citations.metadata import registry_plain_title

_DEFAULT_FILE = Path(__file__).parent / "data" / "metadata_different_work.json"


class _PolicyData(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    max_title_word_overlap: float = Field(gt=0, lt=1)
    min_title_words: int = Field(ge=1)
    ignored_words: list[str]
    author_statuses: list[str] = Field(min_length=1)
    severity: str = Field(pattern=r"^(low|med|high)$")
    reason: str = Field(min_length=1)
    unknown_author: str = Field(min_length=1)
    unknown_year: str = Field(min_length=1)


@dataclass(frozen=True)
class DifferentWorkPolicy:
    max_title_word_overlap: float
    min_title_words: int
    ignored_words: frozenset[str]
    author_statuses: frozenset[str]
    severity: str
    reason: str
    unknown_author: str
    unknown_year: str


@lru_cache(maxsize=4)
def load_different_work_policy(path: Path | None = None) -> DifferentWorkPolicy:
    raw = _PolicyData.model_validate(
        json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    )
    return DifferentWorkPolicy(
        raw.max_title_word_overlap,
        raw.min_title_words,
        frozenset(_words(" ".join(raw.ignored_words))),
        frozenset(raw.author_statuses),
        raw.severity,
        raw.reason,
        raw.unknown_author,
        raw.unknown_year,
    )


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text).casefold())


def _folded_words(text) -> set[str]:
    """Name words without accents: "García" and "Garcia" are one name."""
    if not isinstance(text, str):
        return set()
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return {w for w in _words(plain) if len(w) > 1}


def title_word_overlap(cited, registered, policy: DifferentWorkPolicy) -> float | None:
    """Shared content words over the shorter title's content words; None when
    either title is too short to say. Registry markup is left out first."""
    if not isinstance(cited, str) or not isinstance(registered, str):
        return None
    left = {w for w in _words(cited) if w not in policy.ignored_words}
    right = {w for w in _words(registry_plain_title(registered)) if w not in policy.ignored_words}
    if min(len(left), len(right)) < policy.min_title_words:
        return None
    return len(left & right) / min(len(left), len(right))


def _author_differs(comparison, policy: DifferentWorkPolicy) -> bool:
    if comparison.author_status not in policy.author_statuses:
        return False
    # A name variant ("Dela Cruz" for "Cruz", "Santos-Cruz" for "Santos", an
    # accent left off) shares a surname word: not evidence of another work.
    cited = comparison.cited.get("first_author")
    cited_surname = cited.split(",", 1)[0] if isinstance(cited, str) else None
    return not (
        _folded_words(cited_surname) & _folded_words(comparison.registered.get("first_author"))
    )


def leads_to_different_work(comparison, policy: DifferentWorkPolicy) -> float | None:
    """The title overlap when the comparison shows a different work, else None.

    Both must differ: the title, by more than the threshold, and the first
    author, as the registry records it. An author the registry does not record
    or that cannot be compared keeps the finding a Low mismatch."""
    if comparison is None or comparison.title_status != "differs":
        return None
    if not _author_differs(comparison, policy):
        return None
    overlap = title_word_overlap(
        comparison.cited.get("title"), comparison.registered.get("title"), policy
    )
    if overlap is None or overlap >= policy.max_title_word_overlap:
        return None
    return overlap


def different_work_reason(comparison, policy: DifferentWorkPolicy) -> str:
    registered = comparison.registered
    return policy.reason.format(
        title=registry_plain_title(registered.get("title")),
        first_author=registered.get("first_author") or policy.unknown_author,
        year=registered.get("year") or policy.unknown_year,
    )
