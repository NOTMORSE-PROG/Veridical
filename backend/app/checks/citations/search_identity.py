"""Whether a registry search candidate is the reference that was cited.

A title search returns candidates, not answers. A candidate confirms a
reference only when its title, first author and year all agree with it.
Agreement tolerates the differences measured between how theses print a
reference and how registries store it (BUG-610), and nothing wider:

- title: equal once case, accents, punctuation, spacing and line-break
  hyphens are set aside ("bio-logging" and "bio‐logging", "micro- phone"
  and "microphone", "LashTech" and "Lash Tech"), while decimal numbers and
  the configured symbols still count ("C++" is not "C", "1.5" is not "15");
  a Crossref record's separate subtitle field is joined to its title; and
  a reference that gives only the record's main title (the part before a
  subtitle separator) matches when the year is exact, but a cited subtitle
  the record does not hold never does;
- first author: the same surname once accents, initials and name particles
  are set aside ("Castelblanco A" and "Castelblanco", "van Valkenburgh" and
  "Valkenburgh"), or a registry name that also holds given names and ends in
  the cited surname ("Brandes" and "Scott Brandes");
- year: equal to any publication date on the record, or within the
  configured tolerance of one when the full title agrees (online-first
  and print years differ by one).

A match that needed any of these allowances confirms that the source
exists, but never carries a retraction or correction over to the
reference: those findings need the strict match.
"""

import re
import unicodedata
from dataclasses import dataclass

from app.checks.citations.metadata import MetadataComparison, registry_plain_title
from app.config import Settings
from app.external.schemas import VerificationResult
from app.ingest.reference_metadata_patterns import load_reference_metadata_patterns

_CROSSREF_DATE_FIELDS = ("published", "published-print", "published-online", "issued")


@dataclass(frozen=True)
class SearchIdentity:
    """How each part of the identity agreed. `strict` is the original rule:
    title equal under the title policy, first author and year equal."""

    title: str  # "policy" | "normalized" | "main_title"
    author: str  # "policy" | "surname_words"
    year: str  # "exact" | "within_tolerance"

    @property
    def strict(self) -> bool:
        return self.allowances == 0

    @property
    def allowances(self) -> int:
        """How many parts needed a tolerance; fewer is a closer match."""
        return (self.title != "policy") + (self.author != "policy") + (self.year != "exact")

    def evidence(self) -> dict[str, str | bool]:
        return {
            "title": self.title,
            "author": self.author,
            "year": self.year,
            "strict": self.strict,
        }


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", value))
    return "".join(c for c in value if not unicodedata.combining(c)).casefold()


def normalized_title(value: object, symbols: list[str]) -> str:
    """Words joined with nothing between them, case and accents folded.
    Decimal numbers keep their point and `symbols` are kept, so "1.5" and
    "C++" stay distinct from "15" and "C"."""
    if not isinstance(value, str):
        return ""
    plain = _fold(registry_plain_title(value) or "")
    kept = "".join(re.escape(symbol) for symbol in symbols)
    pattern = r"\d+(?:[.,]\d+)+|[^\W_]+" + (f"|[{kept}]" if kept else "")
    return "".join(re.findall(pattern, plain))


def _main_title(value: object, separators: list[str], symbols: list[str]) -> str:
    """The normalized part before the first subtitle separator; empty when
    the title has none."""
    if not isinstance(value, str) or not separators:
        return ""
    plain = (registry_plain_title(value) or "").strip()
    pattern = "|".join(re.escape(s) for s in sorted(separators, key=len, reverse=True))
    parts = re.split(pattern, plain, maxsplit=1)
    return normalized_title(parts[0], symbols) if len(parts) == 2 else ""


def _name_words(value: object, particles: frozenset[str] = frozenset()) -> list[str]:
    """Name words in order, without initials or the given particles."""
    if not isinstance(value, str):
        return []
    return [
        word
        for word in re.findall(r"[^\W_]+", _fold(value))
        if len(word) > 1 and word not in particles
    ]


def same_surname(cited: object, registered: object, particles: frozenset[str]) -> bool:
    """Whether a cited first-author surname names the registry's first author.

    Equal word sets match. A registry name with more words matches only when
    it ends in the cited surname, as with a display name "Scott Brandes" or
    "John A. Stern": a cited "Wei" is a given name in "Wei Zhang", and a cited
    "Miller-Struttmann" is not the registry's "Miller". Particles ("van",
    "de") count as part of the name, so "Dela Cruz" is not "Juan Cruz"; they
    are set aside only when what is left is exactly the registry's name, as
    when a registry files "van Valkenburgh" as "Valkenburgh"."""
    cited_words = _name_words(cited)
    registry_words = _name_words(registered)
    if not cited_words or not registry_words:
        return False
    if set(cited_words) == set(registry_words):
        return True
    if set(cited_words) < set(registry_words) and registry_words[-1] in cited_words:
        return True
    cited_core = _name_words(cited, particles)
    return bool(cited_core) and set(cited_core) == set(registry_words)


def name_particles(settings: Settings | None) -> frozenset[str]:
    return load_reference_metadata_patterns(
        settings.reference_metadata_patterns_file if settings else None
    ).name_particles


def registry_years(result: VerificationResult) -> set[int]:
    raw = result.raw if isinstance(result.raw, dict) else {}
    values: list[object] = []
    if result.provider == "crossref":
        for field in _CROSSREF_DATE_FIELDS:
            value = raw.get(field)
            parts = value.get("date-parts") if isinstance(value, dict) else None
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                values.append(parts[0][0])
    elif result.provider == "openalex":
        values.append(raw.get("publication_year"))
    elif result.provider == "s2":
        values.append(raw.get("year"))
    return {value for value in values if type(value) is int}


def registry_full_titles(result: VerificationResult) -> list[str]:
    """The record's title, and for Crossref also the title with its separate
    subtitle field ("Micro-Atmospheres" + "Exploring Portable ...")."""
    titles = [result.title] if isinstance(result.title, str) else []
    raw = result.raw if isinstance(result.raw, dict) else {}
    subtitles = raw.get("subtitle") if result.provider == "crossref" else None
    if titles and isinstance(subtitles, list) and subtitles and isinstance(subtitles[0], str):
        titles.append(f"{titles[0]}: {subtitles[0]}")
    return titles


def registry_first_author(result: VerificationResult) -> str | None:
    raw = result.raw if isinstance(result.raw, dict) else {}
    if result.provider == "crossref":
        authors = raw.get("author")
        first = authors[0] if isinstance(authors, list) and authors else None
        if isinstance(first, dict):
            name = first.get("family") or first.get("name")
            return name if isinstance(name, str) else None
    elif result.provider == "openalex":
        authorships = raw.get("authorships")
        first = authorships[0] if isinstance(authorships, list) and authorships else None
        author = first.get("author") if isinstance(first, dict) else None
        name = author.get("display_name") if isinstance(author, dict) else None
        return name if isinstance(name, str) else None
    elif result.provider == "s2":
        authors = raw.get("authors")
        first = authors[0] if isinstance(authors, list) and authors else None
        name = first.get("name") if isinstance(first, dict) else None
        return name if isinstance(name, str) else None
    return None


def search_identity(
    citation, result: VerificationResult, comparison: MetadataComparison, settings: Settings
) -> SearchIdentity | None:
    """The identity a candidate shares with the reference, or None."""
    if not result.found or type(citation.year) is not int:
        return None

    symbols = settings.citation_title_search_significant_symbols
    separators = settings.citation_title_search_subtitle_separators
    cited_title = normalized_title(citation.title, symbols)
    registry_titles = registry_full_titles(result)
    if comparison.title_status == "agrees":
        title = "policy"
    elif cited_title and any(
        cited_title == normalized_title(value, symbols) for value in registry_titles
    ):
        title = "normalized"
    elif cited_title and any(
        cited_title == _main_title(value, separators, symbols) for value in registry_titles
    ):
        # The reference gives only the main title of the registry's record.
        # The reverse, a cited subtitle the record lacks, is not accepted:
        # nothing on the record could show that subtitle is right.
        title = "main_title"
    else:
        return None

    if comparison.author_status in {"agrees", "source_supported_sequence"}:
        author = "policy"
    else:
        cited_first = citation.authors[0].split(",", 1)[0] if citation.authors else None
        if not same_surname(cited_first, registry_first_author(result), name_particles(settings)):
            return None
        author = "surname_words"

    years = registry_years(result)
    if citation.year in years:
        year = "exact"
    elif title != "main_title" and any(
        abs(citation.year - value) <= settings.citation_title_search_year_tolerance
        for value in years
    ):
        year = "within_tolerance"
    else:
        return None

    identity = SearchIdentity(title, author, year)
    if not identity.strict and (result.retracted or result.is_correction):
        # A loose match is enough to say the source exists, not to report
        # that the cited work was retracted or corrected.
        return None
    return identity
