"""Fixture-backed fake LLM client (VERIDICAL_FAKE_LLM=1).

Returns canned JSON per prompt type from app/llm/fixtures/<prompt_type>.json.
Zero network, zero keys, zero quota — dev and tests run entirely on this.
When constructed with a `session_factory` (the app's real wiring, via
`get_llm_client()`), each `complete()` also writes an `llm_call` audit row —
so a fake-mode completion does have a Postgres dependency in that path.
"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.base import LLMClient, UnknownPromptTypeError
from app.llm.queue import LLMQueue
from app.models.audit import AuditLog

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeLLMClient(LLMClient):
    def __init__(
        self,
        fixtures_dir: Path = FIXTURES_DIR,
        # BUG-038: optional, defaults to None so every existing bare
        # `FakeLLMClient()` construction across the test suite is
        # unaffected — only `get_llm_client()`'s real app wiring passes
        # one, the same pattern `_build_real_client`'s `LLMQueue` already
        # uses (`app/db.get_session_factory()`).
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ):
        self._fixtures_dir = fixtures_dir
        self._session_factory = session_factory

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
        response = json.loads(fixture.read_text(encoding="utf-8"))
        if self._session_factory is not None:
            await self._write_audit(prompt_type, prompt, prompt_version, response, context)
        return response

    async def _write_audit(
        self,
        prompt_type: str,
        prompt: str,
        prompt_version: str,
        response: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        # BUG-038: the audit log's own header claims "every AI call" is
        # tracked — before this, that was silently false in fake-LLM mode
        # (the project's own zero-budget default: every dev session, CI
        # run, and most demos), since only the real `LLMQueue.submit`
        # path ever wrote an `llm_call` row. Same event_type and same
        # `_json_safe` context encoding as the real path (`LLMQueue`,
        # queue.py) so the audit log's UI/replay tooling needs no
        # fake-mode special case, and so context values that aren't
        # JSON-plain (e.g. the vision pass's raw image bytes) get the same
        # length+sha256 summary instead of being inlined as text — a
        # blanket `str(v)` here previously stored full binary-as-text
        # payloads for every fake-mode vision call (backend-critic finding,
        # BUG-038 review). `model: "fake"` and `fake_llm: true` in the
        # payload keep this HONEST about being a fixture response, never
        # dressed up as a real Gemini call (charter rule 9); `temperature:
        # None` because fake mode has no sampling temperature to report.
        # No `input_hash` — fake mode has no response cache to key (D-011
        # doesn't apply here); `tools/replay_call.py` treats a `None`
        # input_hash as "nothing to replay" rather than a mismatch.
        check_run_id = context.get("check_run_id")
        safe_context = {k: v for k, v in context.items() if k != "check_run_id"}
        async with self._session_factory() as session:
            session.add(
                AuditLog(
                    event_type="llm_call",
                    check_run_id=check_run_id,
                    prompt_version=prompt_version,
                    input_hash=None,
                    payload={
                        "prompt_type": prompt_type,
                        "model": "fake",
                        "fake_llm": True,
                        "temperature": None,
                        "context": LLMQueue._json_safe(safe_context),
                        "prompt": prompt,
                        "response": response,
                    },
                )
            )
            await session.commit()
