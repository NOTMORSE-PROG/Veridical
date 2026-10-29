"""F3.1/F3.2: one positive heading-presence claim, with no unassessed tail."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from app.checks.rules.grammar import alternatives, compile_pattern, expand, read_grammar, strings
from app.checks.rules.keywords import claim_text

_DEFAULT_FILE = Path(__file__).parent / "data" / "presence_rule_grammar.json"
PRESENCE_SCOPE_VERSION = "v1"


@lru_cache
def load_presence_grammar(path: Path | None = None) -> tuple[tuple[str, ...], str]:
    raw = read_grammar(path or _DEFAULT_FILE)
    subjects = alternatives(strings(raw, "subjects"))
    patterns = strings(raw, "patterns")
    for pattern in patterns:
        if pattern.count("{{target}}") != 1:
            raise ValueError("A presence capability must bind exactly one target.")
        compile_pattern(expand(pattern, {"subjects": subjects, "target": "heading"}))
    return tuple(patterns), subjects


def is_presence_claim(criterion, target: str | None, *, grammar=None) -> bool:
    patterns, subjects = grammar if grammar is not None else load_presence_grammar()
    if not patterns or not subjects:
        raise ValueError("The heading-presence capability grammar is empty.")
    if not isinstance(target, str) or not target.strip():
        return False
    text = " ".join(claim_text(criterion).split())
    values = {"subjects": subjects, "target": re.escape(target)}
    return any(compile_pattern(expand(pattern, values)).fullmatch(text) for pattern in patterns)


def presence_input_digest(criterion) -> str:
    payload = json.dumps(
        {"text": criterion.text, "evidence": getattr(criterion, "evidence", None)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def presence_scope_record(criterion) -> dict[str, str]:
    return {
        "version": PRESENCE_SCOPE_VERSION,
        "rule_id": "required_section_present",
        "input_digest": presence_input_digest(criterion),
    }
