"""LLM client interface. All AI calls in the pipeline go through this."""

from abc import ABC, abstractmethod
from typing import Any


class LLMError(Exception):
    """Base class for LLM-layer errors."""


class LLMNotConfiguredError(LLMError):
    """Raised when a real LLM client is requested but not available."""


class UnknownPromptTypeError(LLMError):
    """Raised by the fake client when no fixture exists for a prompt type."""


class LLMClient(ABC):
    """Every pipeline stage that needs AI talks to this interface only.

    `prompt_type` is a stable identifier ("rubric_decomposition",
    "semantic_grading", ...) — the fake client keys fixtures on it, and the
    real client (V-009) will key prompt templates and versions on it.
    """

    @abstractmethod
    async def complete(self, prompt_type: str, prompt: str, **context: Any) -> dict[str, Any]:
        """Return the model's structured (JSON) response for this prompt."""
