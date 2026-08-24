"""Grader-directed instruction detection (BUG-045, F3.3 hardening).

Cheap, deterministic pattern match over the text a semantic-grading batch is
about to send an LLM (D-011's Tier-0 signal convention: heuristic,
informational, never itself a verdict -- see app.checks.signals). No LLM
call, no network, same input always produces the same output.

`app.checks.consistency.vote_batch` runs this once per batch and, on a
match, forces every criterion in that batch to escalate regardless of what
the self-consistency vote agreed on: BUG-045's finding is that an injected
instruction can make both grading passes comply identically, so their
agreement is exactly the signal that cannot be trusted once this matches.

Patterns live in data/injection_patterns.json (ground rule 7): describing a
NEW way a document might address a grader must never need a code change.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_FILE = Path(__file__).parent / "data" / "injection_patterns.json"
_MAX_SNIPPET_CHARS = 160
_SNIPPET_CONTEXT_CHARS = 20


@dataclass(frozen=True)
class InjectionPattern:
    id: str
    regex: re.Pattern[str]
    label: str


@dataclass(frozen=True)
class InjectionSignal:
    """`suspected=False` is the default, unremarkable case -- most
    manuscripts never touch any of this. `matched_snippet` is bounded and
    trimmed so the evidence shown to an instructor is a short excerpt, not
    the whole injected passage."""

    suspected: bool
    matched_pattern_id: str | None = None
    matched_label: str | None = None
    matched_snippet: str | None = None


def load_injection_patterns(path: Path | None = None) -> tuple[InjectionPattern, ...]:
    raw = json.loads((path or _DEFAULT_FILE).read_text(encoding="utf-8"))
    entries = raw.get("patterns") or []
    return tuple(
        InjectionPattern(
            id=str(entry["id"]),
            regex=re.compile(str(entry["regex"]), re.IGNORECASE),
            label=str(entry["label"]),
        )
        for entry in entries
    )


def _snippet_around(text: str, start: int, end: int) -> str:
    lo = max(start - _SNIPPET_CONTEXT_CHARS, 0)
    hi = min(end + _SNIPPET_CONTEXT_CHARS, len(text))
    snippet = " ".join(text[lo:hi].split())
    if len(snippet) > _MAX_SNIPPET_CHARS:
        snippet = snippet[:_MAX_SNIPPET_CHARS].rstrip() + "..."
    return snippet


def detect_injection_signal(
    text: str, patterns: tuple[InjectionPattern, ...] | None = None
) -> InjectionSignal:
    """Pure function over manuscript text. Checks patterns in listed order
    and returns on the first match -- which pattern matched first is not
    meaningful, only whether any did."""
    for pattern in patterns if patterns is not None else load_injection_patterns():
        match = pattern.regex.search(text)
        if match:
            return InjectionSignal(
                suspected=True,
                matched_pattern_id=pattern.id,
                matched_label=pattern.label,
                matched_snippet=_snippet_around(text, match.start(), match.end()),
            )
    return InjectionSignal(suspected=False)
