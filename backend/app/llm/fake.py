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

    async def complete(self, prompt_type: str, prompt: str, **context: Any) -> dict[str, Any]:
        fixture = self._fixtures_dir / f"{prompt_type}.json"
        if not fixture.is_file():
            available = sorted(p.stem for p in self._fixtures_dir.glob("*.json"))
            raise UnknownPromptTypeError(
                f"No fixture for prompt type {prompt_type!r}. "
                f"Available: {available}. Add {fixture.name} to {self._fixtures_dir}."
            )
        return json.loads(fixture.read_text(encoding="utf-8"))
