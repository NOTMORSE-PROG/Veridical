"""F3/F4/F5: lazy, execution-scoped AI capability and honest terminal causes."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from app.errors import RuntimeApiKeyRejectedError
from app.llm.base import LLMClient, LLMError, LLMNotConfiguredError

AIUnavailableCause = Literal["ai_not_configured", "ai_key_rejected"]
ClientResolver = Callable[[], Awaitable[LLMClient]]


class AIUnavailableError(LLMError):
    """A known capability limit, with no provider or credential diagnostics."""

    def __init__(self, cause: AIUnavailableCause, *, request_attempted: bool = False):
        super().__init__(cause)
        self.cause = cause
        # Current logical client-request provenance, not an SDK-attempt count.
        # False makes no statement about requests made earlier in this run.
        self.request_attempted = request_attempted


class RunLLMClient(LLMClient):
    """Resolve only when AI is needed; terminal cause memory belongs to this run.

    The resolved client already owns the applicable fallback policy. Only its
    final runtime rejection is terminal here; successful fallback is transparent.
    An unexpected constructor/DB/programming failure is never a capability floor.
    """

    def __init__(self, resolve: ClientResolver):
        self._resolve = resolve
        self._client: LLMClient | None = None
        self._unavailable: AIUnavailableCause | None = None

    async def complete(
        self, prompt_type: str, prompt: str, *, prompt_version: str = "unversioned", **context: Any
    ) -> dict[str, Any]:
        if self._unavailable is not None:
            raise AIUnavailableError(self._unavailable)
        if self._client is None:
            try:
                self._client = await self._resolve()
            except LLMNotConfiguredError:
                self._unavailable = "ai_not_configured"
                raise AIUnavailableError(self._unavailable) from None
        try:
            return await self._client.complete(
                prompt_type, prompt, prompt_version=prompt_version, **context
            )
        except RuntimeApiKeyRejectedError:
            self._unavailable = "ai_key_rejected"
            raise AIUnavailableError(self._unavailable, request_attempted=True) from None
