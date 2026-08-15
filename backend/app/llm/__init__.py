"""LLM client factory: fake (fixtures) vs real (Gemini, V-009); real is
either the shared pool key or an instructor's own BYOK key (V-052)."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.config import Settings
from app.errors import InvalidApiKeyError, QuotaExhaustedError
from app.llm.base import LLMClient, LLMNotConfiguredError
from app.llm.client import GeminiLLMClient
from app.llm.fake import FakeLLMClient
from app.llm.keystore import BYOKNotConfiguredError, decrypt_api_key
from app.llm.pool import load_model_pool
from app.llm.queue import LLMQueue
from app.models.instructor import Instructor

logger = logging.getLogger(__name__)

__all__ = [
    "LLMClient",
    "LLMNotConfiguredError",
    "FakeLLMClient",
    "GeminiLLMClient",
    "FallbackLLMClient",
    "evict_instructor_client",
    "get_llm_client",
    "get_llm_client_for",
    "get_quota_status_for",
    "load_model_pool",
]

# Process-wide singletons: the RateGovernor's sliding window must persist
# across requests within one process, or every call would restart at zero
# and never actually serialize (ENGINEERING §3). Same reasoning extends to
# an instructor's own BYOK client (V-052) -- their own RPM window must
# persist too, so it's cached per instructor, not rebuilt per call.
_real_client: GeminiLLMClient | None = None
# Keyed by instructor_id -> (encrypted key blob it was built from, client).
# Comparing the encrypted blob (not just the instructor_id) makes the cache
# self-invalidating when a key is changed or removed via settings, the same
# "self-healing, no explicit invalidation call" pattern as V-040's
# `_revoke_all_active` -- no separate cache-busting plumbing needed.
_instructor_clients: dict[int, tuple[bytes, GeminiLLMClient]] = {}


def _build_real_client(
    settings: Settings, *, api_key: str | None = None, instructor_id: int | None = None
) -> GeminiLLMClient:
    key = api_key or settings.gemini_api_key
    if not key:
        raise LLMNotConfiguredError(
            "VERIDICAL_FAKE_LLM=0 requires GEMINI_API_KEY to be set (real Gemini client, V-009)."
        )
    # Imported lazily: transport.py is the only module that imports the
    # google-genai SDK, and fake-LLM mode (dev/CI default) must never need
    # it installed.
    from app.llm.transport import GeminiTransport

    transport = GeminiTransport(
        api_key=key, timeout_seconds=settings.gemini_request_timeout_seconds
    )
    queue = LLMQueue(
        transport=transport,
        session_factory=db.get_session_factory(),
        pool=load_model_pool(settings.llm_model_pool_file),
        temperature=settings.gemini_temperature,
        max_retries=settings.llm_max_retries,
        retry_base_seconds=settings.llm_retry_base_seconds,
        reset_timezone=settings.llm_quota_reset_timezone,
        instructor_id=instructor_id,
    )
    return GeminiLLMClient(queue)


def _shared_client(settings: Settings) -> GeminiLLMClient:
    global _real_client
    if _real_client is None:
        _real_client = _build_real_client(settings)
    return _real_client


def _instructor_client(
    settings: Settings, instructor_id: int, encrypted_key: bytes, decrypted_key: str
) -> GeminiLLMClient:
    cached = _instructor_clients.get(instructor_id)
    if cached is not None and cached[0] == encrypted_key:
        return cached[1]
    client = _build_real_client(settings, api_key=decrypted_key, instructor_id=instructor_id)
    _instructor_clients[instructor_id] = (encrypted_key, client)
    return client


def evict_instructor_client(instructor_id: int) -> None:
    """Drops a cached instructor client, if any (V-052, backend-critic
    finding, P2): the encrypted-blob comparison in `_instructor_client`
    already makes the cache self-invalidate on the NEXT lookup after a key
    change, but until that next lookup happens, the OLD decrypted key
    string and a live, authenticated `genai.Client` built from it stay
    reachable from a GC root indefinitely -- a real secret-hygiene gap,
    the same "scrub, don't just stop using" principle this app already
    applies to session revocation on logout. Called by
    `app/settings/service.py` on both delete and rotate (a fresh save
    over an existing key), so a removed/replaced key's material is
    actually collectible, not just unreachable via the routing logic."""
    _instructor_clients.pop(instructor_id, None)


class FallbackLLMClient(LLMClient):
    """V-052 AC: an instructor's own key, spent for the day OR revoked/
    invalid, must fall back to the shared key rather than fail the call
    outright -- and if the shared key is ALSO spent, `QuotaExhaustedError`
    still propagates, which is exactly what V-050's existing availability
    floor (machine.py's own `except QuotaExhaustedError`) already turns
    into an honest `quota_exhausted` result instead of a hard failure.
    `InvalidApiKeyError` reroutes too (backend-critic finding, P1,
    live-reproduced: a revoked/mistyped key raised an unmapped SDK
    exception straight through this class -- FallbackLLMClient only
    caught `QuotaExhaustedError`, so the ticket's own "revoked... key
    must degrade to the shared key" edge case was a hard crash, not a
    fallback, until `app/llm/transport.py`/`queue.py` were fixed to
    classify a 401/403 into this typed error instead of leaking the raw
    SDK exception). A transient `ApiDownError` on the instructor's own
    key still propagates as-is (LLMQueue's own retry/backoff already ran
    before it reached here); silently rerouting a real outage to a
    different key would make a failure look like a config problem with
    the wrong key.
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self._primary = primary
        self._fallback = fallback

    async def complete(
        self, prompt_type: str, prompt: str, *, prompt_version: str = "unversioned", **context: Any
    ) -> dict[str, Any]:
        try:
            return await self._primary.complete(
                prompt_type, prompt, prompt_version=prompt_version, **context
            )
        except (QuotaExhaustedError, InvalidApiKeyError):
            return await self._fallback.complete(
                prompt_type, prompt, prompt_version=prompt_version, **context
            )


def get_llm_client(settings: Settings) -> LLMClient:
    """Shared-pool-key client only -- no instructor context. Kept for fake
    mode and any caller that genuinely has none (e.g. no manuscript/
    instructor yet); every real pipeline call site uses `get_llm_client_for`
    instead so an instructor's own BYOK key (V-052) is actually used."""
    if settings.veridical_fake_llm:
        # BUG-038: without a session_factory, FakeLLMClient writes no
        # audit trail at all — silently false against the audit log's own
        # "every AI call" header claim in this project's zero-budget
        # default mode. `db.get_session_factory()` is the same lazy
        # process-wide factory `_build_real_client`'s `LLMQueue` already
        # uses below.
        return FakeLLMClient(session_factory=db.get_session_factory())
    return _shared_client(settings)


async def _resolve_own_client(
    session: AsyncSession, settings: Settings, instructor_id: int
) -> GeminiLLMClient | None:
    """The instructor's own BYOK client, or None if they have no key OR it
    can't be decrypted right now.

    security-auditor finding (High, V-052): an undecryptable key (e.g.
    `BYOK_ENCRYPTION_KEY` was rotated -- the ticket's own documented,
    unsolved limitation) used to raise `BYOKNotConfiguredError` straight
    out of `get_llm_client_for`, called from `run_check_run` BEFORE that
    function's own `try:` block even starts. The exception then propagated
    all the way to `worker_loop`'s generic `except Exception: back off`,
    which keeps the *process* alive but never marks the `check_run` row
    terminal -- and since this app runs one check_run at a time, FIFO,
    oldest-non-terminal-first (single-dyno constraint), that one broken
    run got retried forever and blocked every OTHER instructor's queued
    work too. A bad BYOK key must degrade the same way "no key configured"
    already does -- fall back to the shared key -- not wedge the whole
    pipeline for everyone.
    """
    instructor = await session.get(Instructor, instructor_id)
    if instructor is None or instructor.gemini_api_key_encrypted is None:
        return None
    try:
        own_key = decrypt_api_key(instructor.gemini_api_key_encrypted, settings)
    except BYOKNotConfiguredError:
        logger.warning(
            "Instructor %s's stored API key could not be decrypted "
            "(BYOK_ENCRYPTION_KEY may have changed since it was saved) -- "
            "falling back to the shared key for this call.",
            instructor_id,
        )
        return None
    return _instructor_client(settings, instructor_id, instructor.gemini_api_key_encrypted, own_key)


async def get_llm_client_for(
    session: AsyncSession, settings: Settings, instructor_id: int | None
) -> LLMClient:
    """Resolves the real caller-facing client for a given instructor
    (V-052): their own BYOK key if configured and decryptable, composed
    with the shared key as a fallback on quota exhaustion
    (`FallbackLLMClient`); the shared key directly otherwise. Fake mode and
    a missing `instructor_id` both degrade to the exact same shared-key
    path `get_llm_client` already used before this ticket -- no behavior
    change for either case.
    """
    if settings.veridical_fake_llm:
        return get_llm_client(settings)
    shared = _shared_client(settings)
    if instructor_id is None:
        return shared
    own_client = await _resolve_own_client(session, settings, instructor_id)
    if own_client is None:
        return shared
    return FallbackLLMClient(primary=own_client, fallback=shared)


async def get_quota_status_for(
    session: AsyncSession, settings: Settings, instructor_id: int
) -> tuple[dict[str, Any], str]:
    """Quota status plus which key it describes ("own"/"shared") -- the
    AC's "quota meter shows your key vs shared key honestly". Reports the
    instructor's OWN island when they have a key configured and decryptable
    (what matters to them is their own remaining capacity, not the shared
    pool's), the shared pool's otherwise."""
    own_client = await _resolve_own_client(session, settings, instructor_id)
    if own_client is not None:
        return await own_client.queue.get_quota_status(), "own"
    return await _shared_client(settings).queue.get_quota_status(), "shared"


def get_llm_queue(settings: Settings) -> LLMQueue:
    """For callers that need quota status directly (the `/quota` route)
    without going through the `LLMClient.complete` interface."""
    return _shared_client(settings).queue
