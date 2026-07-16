"""LLM client factory: fake (fixtures) vs real (Gemini, arrives in V-009)."""

from app.config import Settings
from app.llm.base import LLMClient, LLMNotConfiguredError
from app.llm.fake import FakeLLMClient

__all__ = ["LLMClient", "LLMNotConfiguredError", "FakeLLMClient", "get_llm_client"]


def get_llm_client(settings: Settings) -> LLMClient:
    if settings.veridical_fake_llm:
        return FakeLLMClient()
    raise LLMNotConfiguredError(
        "The real Gemini client lands in V-009 (LLM queue + quota meter). "
        "Until then, set VERIDICAL_FAKE_LLM=1 to use the fixture-backed stub."
    )
