"""LLM client factory: fake (fixtures) vs real (Gemini, V-009)."""

from app import db
from app.config import Settings
from app.llm.base import LLMClient, LLMNotConfiguredError
from app.llm.client import GeminiLLMClient
from app.llm.fake import FakeLLMClient
from app.llm.queue import LLMQueue

__all__ = [
    "LLMClient",
    "LLMNotConfiguredError",
    "FakeLLMClient",
    "GeminiLLMClient",
    "get_llm_client",
]

# Process-wide singleton: the RateGovernor's sliding window must persist
# across requests within one process, or every call would restart at zero
# and never actually serialize (ENGINEERING §3).
_real_client: GeminiLLMClient | None = None


def _build_real_client(settings: Settings) -> GeminiLLMClient:
    if not settings.gemini_api_key:
        raise LLMNotConfiguredError(
            "VERIDICAL_FAKE_LLM=0 requires GEMINI_API_KEY to be set (real Gemini client, V-009)."
        )
    # Imported lazily: transport.py is the only module that imports the
    # google-genai SDK, and fake-LLM mode (dev/CI default) must never need
    # it installed.
    from app.llm.transport import GeminiTransport

    transport = GeminiTransport(
        api_key=settings.gemini_api_key, timeout_seconds=settings.gemini_request_timeout_seconds
    )
    queue = LLMQueue(
        transport=transport,
        session_factory=db.get_session_factory(),
        model=settings.gemini_model,
        temperature=settings.gemini_temperature,
        rpm=settings.llm_rpm,
        daily_quota=settings.llm_daily_quota,
        max_retries=settings.llm_max_retries,
        retry_base_seconds=settings.llm_retry_base_seconds,
        reset_timezone=settings.llm_quota_reset_timezone,
    )
    return GeminiLLMClient(queue)


def get_llm_client(settings: Settings) -> LLMClient:
    if settings.veridical_fake_llm:
        return FakeLLMClient()
    global _real_client
    if _real_client is None:
        _real_client = _build_real_client(settings)
    return _real_client


def get_llm_queue(settings: Settings) -> LLMQueue:
    """For callers that need quota status directly (the `/quota` route)
    without going through the `LLMClient.complete` interface."""
    global _real_client
    if _real_client is None:
        _real_client = _build_real_client(settings)
    return _real_client.queue
