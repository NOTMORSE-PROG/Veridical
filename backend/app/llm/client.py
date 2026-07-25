"""Real Gemini client: `LLMClient` interface backed by the queue (V-009)."""

from typing import Any

from app.llm.base import LLMClient
from app.llm.queue import LLMQueue


class GeminiLLMClient(LLMClient):
    def __init__(self, queue: LLMQueue) -> None:
        self.queue = queue

    async def complete(
        self, prompt_type: str, prompt: str, *, prompt_version: str = "unversioned", **context: Any
    ) -> dict[str, Any]:
        # check_run_id identifies the PIPELINE RUN, not the prompt content —
        # it must never enter the cache key, or a Flow E re-run (same rubric
        # version) would never hit cache (D-011).
        check_run_id = context.pop("check_run_id", None)
        return await self.queue.submit(
            prompt_type=prompt_type,
            prompt=prompt,
            prompt_version=prompt_version,
            check_run_id=check_run_id,
            **context,
        )
