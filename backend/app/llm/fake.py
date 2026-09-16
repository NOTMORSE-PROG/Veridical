"""Fixture-backed fake LLM client (VERIDICAL_FAKE_LLM=1).

Returns canned JSON per prompt type from app/llm/fixtures/<prompt_type>.json.
Zero network, zero keys, zero quota — dev and tests run entirely on this.
When constructed with a `session_factory` (the app's real wiring, via
`get_llm_client()`), each `complete()` also writes an `llm_call` audit row —
so a fake-mode completion does have a Postgres dependency in that path.
"""

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.base import LLMClient, UnknownPromptTypeError
from app.llm.queue import LLMQueue
from app.models.audit import AuditLog

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# BUG-119: `semantic_grading.json`'s canned `evidence_quotes` are a fixed
# placeholder sentence that never appears in any real manuscript -- so
# `_verify_quotes` (app/checks/semantic.py) rejects it on every real check
# run, both self-consistency passes come back unverifiable, and a real
# majority (hence "Accept AI") is unreachable in fake-LLM mode, the
# project's zero-budget default for every session/CI run/demo.
#
# Fixed here, not by threading manuscript text through `**context` into
# `llm.complete()` -- that would ride all the way into the REAL Gemini
# client's response-cache key and audit payload (`LLMQueue.submit`, D-011)
# for a need that's exclusively fake-mode's. Instead: every rendered
# `semantic_grading` prompt already wraps the real manuscript excerpt in
# `<manuscript_text>...</manuscript_text>` (see
# `app/checks/prompts/semantic_grading_v3.txt`) -- extracted directly from
# the `prompt` string this fake client already receives, zero real-path
# change, zero new interface surface.
_SEMANTIC_GRADING_PROMPT_TYPE = "semantic_grading"
_MANUSCRIPT_TEXT_RE = re.compile(r"<manuscript_text>\n(.*?)\n</manuscript_text>", re.DOTALL)
_REAL_QUOTE_WORD_COUNT = 8


def _real_quotes_from_prompt(prompt: str) -> list[str]:
    """Every line of the prompt's own embedded manuscript text long enough
    to make a plausible quote, each taken from a SINGLE line
    (`context_text`'s own `\\n` join is one line per source block, never
    spanning a line boundary, so it stays contiguous in `_verify_quotes`'s
    block-level view too, which interleaves furniture blocks `context_text`
    itself already filters out). Empty when the prompt carries no usable
    manuscript text (whole-document batches on a near-empty extraction,
    deliberately not faked)."""
    match = _MANUSCRIPT_TEXT_RE.search(prompt)
    if not match:
        return []
    quotes = []
    for line in match.group(1).split("\n"):
        words = line.split()
        if len(words) >= _REAL_QUOTE_WORD_COUNT:
            quotes.append(" ".join(words[:_REAL_QUOTE_WORD_COUNT]))
    return quotes


def _with_real_quotes(response: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Replaces each verdict's fixture `evidence_quotes` with a real
    excerpt, verbatim, so `_verify_quotes` can actually verify it against
    the manuscript being graded -- the verdict itself
    (pass/partial/fail/reasoning) is untouched, still scripted by the
    fixture. Assigns a DIFFERENT candidate line per verdict where enough
    exist (`verdicts[i]` gets `quotes[i % len(quotes)]`) -- reusing the
    SAME quote for every verdict in a batch would trip the existing
    same-batch duplicate-evidence guard (`_drop_claimed_quotes`,
    BUG-155/BUG-176) and escalate every verdict after the first, the exact
    opposite of this fix's own goal. When fewer real lines exist than
    verdicts, later verdicts legitimately share one and may still
    escalate -- that guard is correct behavior, not something to route
    around. Both self-consistency passes (V-022) load the identical
    fixture today (no `__pass_1`/`__pass_2` files exist) and this
    extraction is deterministic given the same prompt, so this alone is
    enough to reach a real, verified majority in fake mode -- no new
    per-pass fixtures needed for the base "Accept AI is reachable" case.
    Falls back to the fixture's own placeholder quotes (still
    unverifiable, an honest no-op) when the prompt carries no extractable
    manuscript text, rather than fabricating one."""
    quotes = _real_quotes_from_prompt(prompt)
    if not quotes or "verdicts" not in response:
        return response
    return {
        **response,
        "verdicts": [
            {**v, "evidence_quotes": [quotes[i % len(quotes)]]}
            for i, v in enumerate(response["verdicts"])
        ],
    }


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
        if prompt_type == _SEMANTIC_GRADING_PROMPT_TYPE:
            response = _with_real_quotes(response, prompt)
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
