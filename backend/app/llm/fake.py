"""Fixture-backed fake LLM client (VERIDICAL_FAKE_LLM=1).

Returns canned JSON per prompt type from app/llm/fixtures/<prompt_type>.json.
Zero network, zero keys, zero quota — dev and tests run entirely on this.
"""

import json
from pathlib import Path
from typing import Any

from app.llm.base import LLMClient, UnknownPromptTypeError

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeLLMClient(LLMClient):
    def __init__(self, fixtures_dir: Path = FIXTURES_DIR):
        self._fixtures_dir = fixtures_dir

    async def complete(
        self, prompt_type: str, prompt: str, *, prompt_version: str = "unversioned", **context: Any
    ) -> dict[str, Any]:
        # A per-pass fixture (`<type>__<consistency_pass>.json`) lets V-022's
        # self-consistency voting be exercised deterministically in fake
        # mode (ticket AC) — e.g. `semantic_grading__pass_2.json` can script
        # a disagreement with `semantic_grading__pass_1.json`. Falls back to
        # the plain fixture when no per-pass file exists, so every other
        # prompt type (and single-pass callers) is unaffected.
        consistency_pass = context.get("consistency_pass")
        candidates = (
            [self._fixtures_dir / f"{prompt_type}__{consistency_pass}.json"]
            if consistency_pass
            else []
        )
        candidates.append(self._fixtures_dir / f"{prompt_type}.json")
        fixture = next((f for f in candidates if f.is_file()), None)
        if fixture is None:
            available = sorted(p.stem for p in self._fixtures_dir.glob("*.json"))
            raise UnknownPromptTypeError(
                f"No fixture for prompt type {prompt_type!r}. "
                f"Available: {available}. Add {candidates[-1].name} to {self._fixtures_dir}."
            )
        return json.loads(fixture.read_text(encoding="utf-8"))
