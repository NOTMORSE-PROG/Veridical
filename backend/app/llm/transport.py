"""The ONE module that imports the google-genai SDK (CODING.md §2)."""

import json
import re
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.llm.queue import (
    TransportDailyQuotaExhausted,
    TransportInvalidKey,
    TransportModelUnavailable,
    TransportRateLimited,
    TransportServerError,
)

# Google returns BOTH per-minute and per-day caps as a 429; only the
# `quotaId` distinguishes them (verified live 2026-07-26 against this
# project's key: `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
# quotaValue "20", vs `GenerateRequestsPerMinutePerProjectPerModel-FreeTier`,
# quotaValue "5"). Protocol constants, not tunables — a per-day 429 must
# never be retried like a per-minute one.
_DAILY_QUOTA_MARKERS = ("PerDay", "RequestsPerDay", "GenerateRequestsPerDayPerProjectPerModel")


def _is_daily_quota_error(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _DAILY_QUOTA_MARKERS)


def _is_model_unavailable_error(exc: genai_errors.APIError, model: str) -> bool:
    # GenerateContent also uses 404 for missing input resources. Recognize
    # only the provider's model/operation response for this exact model;
    # unknown resource failures must retain their separate client semantics.
    if exc.code != 404 or exc.status != "NOT_FOUND" or not isinstance(exc.message, str):
        return False
    resource = "models/" + model.removeprefix("models/")
    return (
        re.match(
            rf"^{re.escape(resource)} (?:is|was) "
            r"(?:not found for API version\b|not supported for generateContent\b)",
            exc.message,
        )
        is not None
    )


class GeminiTransport:
    """Wraps `genai.Client` behind the `Transport` protocol `LLMQueue` expects."""

    def __init__(self, api_key: str, *, timeout_seconds: float) -> None:
        self._client = genai.Client(api_key=api_key)
        self._timeout_ms = int(timeout_seconds * 1000)

    async def generate(
        self, *, model: str, prompt: str, temperature: float, **context: Any
    ) -> dict[str, Any]:
        # Multimodal (V-007 vision pass): image PNG bytes ride in the
        # `images` context kwarg and must become Parts, or they'd silently
        # never reach the model — text-only calls (V-010 rubric parsing,
        # etc.) never set this.
        images: list[bytes] = context.get("images") or []
        contents: str | list[Any] = (
            prompt
            if not images
            else [
                prompt,
                *(genai_types.Part.from_bytes(data=img, mime_type="image/png") for img in images),
            ]
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type="application/json",
                    http_options=genai_types.HttpOptions(
                        timeout=self._timeout_ms,
                        retry_options=genai_types.HttpRetryOptions(attempts=1),
                    ),
                ),
            )
        except httpx.TransportError as exc:
            raise TransportServerError("The model service connection failed or timed out.") from exc
        except genai_errors.APIError as exc:
            if _is_model_unavailable_error(exc, model):
                raise TransportModelUnavailable(
                    "The requested model cannot serve this operation."
                ) from exc
            if exc.code == 429:
                if _is_daily_quota_error(exc):
                    raise TransportDailyQuotaExhausted(str(exc)) from exc
                raise TransportRateLimited(str(exc)) from exc
            if exc.code is not None and exc.code >= 500:
                raise TransportServerError(str(exc)) from exc
            if exc.code in (401, 403):
                # backend review finding (P1, V-052): the key itself was
                # rejected (revoked, or a BYOK key that passed the save-time
                # probe but stopped working since) -- scoped to 401/403
                # specifically, not every other non-429/non-5xx code, since
                # a 400 can be a genuine per-model/per-request problem that
                # would fail identically on any key, not a key problem.
                raise TransportInvalidKey(str(exc)) from exc
            raise

        text = response.text
        if not text:
            raise TransportServerError("Gemini returned an empty response.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # response_mime_type="application/json" asks for JSON but
            # doesn't guarantee syntactically valid JSON every time (seen
            # live: a truncated/malformed response on a long rubric
            # decomposition, V-011) — treat it as a retryable transport
            # failure like a 5xx, not an unhandled crash bypassing the
            # queue's retry/backoff entirely.
            raise TransportServerError(f"Gemini response was not valid JSON: {exc}") from exc
