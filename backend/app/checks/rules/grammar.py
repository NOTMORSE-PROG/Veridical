"""Validation shared by packaged complete-claim capability grammars."""

import json
import re
from pathlib import Path


def read_grammar(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("A rule capability grammar must be an object.")
    return raw


def strings(raw: dict, key: str, *, allow_empty: bool = False) -> list[str]:
    values = raw.get(key)
    if (
        not isinstance(values, list)
        or (not values and not allow_empty)
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise ValueError(f"Rule capability grammar requires a valid {key} list.")
    return values


def alternatives(values: list[str]) -> str:
    return "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True))


def expand(pattern: str, values: dict[str, str]) -> str:
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("A rule capability pattern is missing.")
    for key, value in values.items():
        pattern = pattern.replace("{{" + key + "}}", value)
    if "{{" in pattern or "}}" in pattern:
        raise ValueError("A rule capability pattern has an unknown placeholder.")
    return pattern


def compile_pattern(pattern: str, *, groups: tuple[str, ...] = ()) -> re.Pattern[str]:
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("A rule capability pattern is missing.")
    compiled = re.compile(pattern, re.IGNORECASE)
    if compiled.fullmatch("") is not None or not set(groups).issubset(compiled.groupindex):
        raise ValueError("A rule capability pattern is empty or lacks required captures.")
    return compiled
