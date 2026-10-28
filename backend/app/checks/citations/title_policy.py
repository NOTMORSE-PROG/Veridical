"""Conservative F5 DOI-title comparison; uncertain typography stays unassessed."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, model_validator

_DEFAULT_FILE = Path(__file__).parent / "data" / "metadata_title_policy.json"


def _canonical(value: str) -> str:
    return unicodedata.normalize("NFC", value)


class _PolicyData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(min_length=1)
    ignored_characters: list[str]
    terminal_characters: list[str]
    hyphen_characters: list[str]
    case_sensitive_patterns: list[str]
    preserve_mixed_word_case: bool
    preserve_uppercase_words: bool
    preserve_internal_uppercase: bool
    si_prefix_symbols: list[str]
    si_unit_symbols: list[str]
    equivalent_words: list[list[str]]
    equivalent_punctuation: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_policy(self):
        for chars, category in (
            (self.ignored_characters, "Cf"),
            (self.terminal_characters, "P"),
            (self.hyphen_characters, "Pd"),
        ):
            if len(set(chars)) != len(chars) or any(
                len(char) != 1 or not unicodedata.category(char).startswith(category)
                for char in chars
            ):
                raise ValueError("Title policy contains invalid or repeated characters")
        if set(self.terminal_characters) & set(self.hyphen_characters):
            raise ValueError("Title hyphens cannot be stripped at title edges")
        seen_punctuation: set[str] = set()
        for group in self.equivalent_punctuation:
            if (
                len(group) < 2
                or len(set(group)) != len(group)
                or seen_punctuation.intersection(group)
                or any(
                    len(char) != 1 or not unicodedata.category(char).startswith("P")
                    for char in group
                )
                or set(group).intersection(
                    self.ignored_characters + self.terminal_characters + self.hyphen_characters
                )
            ):
                raise ValueError("Title punctuation groups must contain disjoint punctuation")
            seen_punctuation.update(group)
        seen = set()
        for group in self.equivalent_words:
            normalized = [_canonical(word.casefold()) for word in group]
            if (
                len(normalized) < 2
                or any(
                    not word or not all(unicodedata.category(c)[0] in "LM" for c in word)
                    for word in normalized
                )
                or len(set(normalized)) != len(normalized)
                or seen.intersection(normalized)
            ):
                raise ValueError("Title spelling groups must contain disjoint whole words")
            seen.update(normalized)
        for pattern in self.case_sensitive_patterns:
            try:
                compiled = re.compile(pattern)
            except re.error as error:
                raise ValueError("Invalid title case pattern") from error
            if not pattern.startswith("^") or not pattern.endswith("$") or compiled.fullmatch(""):
                raise ValueError("Title case patterns must be nonempty and anchored")
        if (
            not self.si_unit_symbols
            or any(not word or not word.isalpha() for word in self.si_unit_symbols)
            or any(word and not word.isalpha() for word in self.si_prefix_symbols)
        ):
            raise ValueError("SI symbols must be alphabetic tokens")
        return self


@dataclass(frozen=True)
class TitlePolicy:
    version: str
    sha256: str
    ignored: frozenset[str]
    terminal: frozenset[str]
    hyphens: frozenset[str]
    case_patterns: tuple[re.Pattern[str], ...]
    preserve_mixed_word_case: bool
    preserve_uppercase_words: bool
    preserve_internal_uppercase: bool
    si_symbols: frozenset[str]
    equivalent_words: Mapping[str, str]
    equivalent_punctuation: Mapping[str, str]


@lru_cache
def load_title_policy(path: Path | None = None) -> TitlePolicy:
    """A process snapshot: config edits require restart, as other settings do."""
    content = (path or _DEFAULT_FILE).read_bytes()
    raw = _PolicyData.model_validate(json.loads(content))
    equivalents = {}
    for group in raw.equivalent_words:
        normalized = [_canonical(word.casefold()) for word in group]
        equivalents.update(dict.fromkeys(normalized, normalized[0]))
    return TitlePolicy(
        raw.version,
        hashlib.sha256(content).hexdigest(),
        frozenset(raw.ignored_characters),
        frozenset(raw.terminal_characters),
        frozenset(raw.hyphen_characters),
        tuple(re.compile(pattern) for pattern in raw.case_sensitive_patterns),
        raw.preserve_mixed_word_case,
        raw.preserve_uppercase_words,
        raw.preserve_internal_uppercase,
        frozenset(
            prefix + unit for prefix in raw.si_prefix_symbols for unit in raw.si_unit_symbols
        ),
        MappingProxyType(equivalents),
        MappingProxyType(
            {char: group[0] for group in raw.equivalent_punctuation for char in group}
        ),
    )


def _tokens(value: str, policy: TitlePolicy) -> tuple[list[str], list[str]]:
    text = _canonical("".join(c for c in value if c not in policy.ignored)).strip()
    while text and text[-1] in policy.terminal:
        text = text[:-1].rstrip()
    tokens, word = [], []
    for char in text:
        if unicodedata.category(char)[0] in "LMN" or char == "_":
            word.append(char)
        else:
            if word:
                tokens.append("".join(word))
                word.clear()
            if not char.isspace():
                tokens.append(char)
    if word:
        tokens.append("".join(word))
    normalized = []
    for token in tokens:
        # Lowercase leaves distinct lexemes such as sharp-s/ss intact.
        # A broader casefold-only match below is uncertainty, not agreement.
        folded = policy.equivalent_punctuation.get(token, _canonical(token.lower()))
        mixed_word = (
            policy.preserve_mixed_word_case
            and any(c.isalpha() for c in token)
            and not token.isalpha()
        )
        uppercase_word = policy.preserve_uppercase_words and token.isupper()
        internal_uppercase = policy.preserve_internal_uppercase and any(
            c.isupper() for c in token[1:]
        )
        if (
            mixed_word
            or uppercase_word
            or internal_uppercase
            or token in policy.si_symbols
            or any(p.fullmatch(token) for p in policy.case_patterns)
        ):
            normalized.append(token)
        else:
            normalized.append(policy.equivalent_words.get(folded, folded))
    return tokens, normalized


def compare_titles(left: object, right: object, policy: TitlePolicy) -> tuple[str, str | None]:
    if not isinstance(left, str) or not isinstance(right, str):
        return "unassessed", "title_metadata_unavailable"
    raw_left, a = _tokens(left, policy)
    raw_right, b = _tokens(right, policy)
    if not a or not b or not all(any(c.isalnum() for c in "".join(tokens)) for tokens in (a, b)):
        return "unassessed", "title_metadata_unavailable"
    if a == b:
        return "agrees", None
    if [t for t in a if t not in policy.hyphens] == [t for t in b if t not in policy.hyphens]:
        return "unassessed", "title_hyphenation_unassessed"
    if [policy.equivalent_words.get(t.casefold(), t.casefold()) for t in a] == [
        policy.equivalent_words.get(t.casefold(), t.casefold()) for t in b
    ] and not any(
        token in policy.si_symbols
        and not any(pattern.fullmatch(token) for pattern in policy.case_patterns)
        for index, pair in enumerate(zip(raw_left, raw_right, strict=True))
        if a[index] != b[index]
        for token in pair
    ):
        return "unassessed", "title_case_unassessed"
    return "differs", None
