"""Shared source-assertion policy for recognition and quote validation."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache
def assertion_policy() -> dict:
    return json.loads(
        (Path(__file__).parent / "data" / "contradiction_reasons_v3.json").read_text(
            encoding="utf-8"
        )
    )
