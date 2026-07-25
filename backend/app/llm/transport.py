"""The ONE module that imports the google-genai SDK (CODING.md §2)."""

import json
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.llm.queue import TransportRateLimited, TransportServerError


class GeminiTransport:
    """Wraps `genai.Client` behind the `Transport` protocol `LLMQueue` expects."""

    def __init__(self, api_key: str, *, timeout_seconds: float) -> None:
        self._client = genai.Client(api_key=api_key)
        self._timeout_ms = int(timeout_seconds * 1000)

    async def generate(
        self, *, model: str, prompt: str, temperature: float, **context: Any
    ) -> dict[str, Any]:
        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type="application/json",
                    http_options=genai_types.HttpOptions(timeout=self._timeout_ms),
                ),
            )
        except genai_errors.APIError as exc:
            if exc.code == 429:
                raise TransportRateLimited(str(exc)) from exc
            if exc.code is not None and exc.code >= 500:
                raise TransportServerError(str(exc)) from exc
            raise

        text = response.text
        if not text:
            raise TransportServerError("Gemini returned an empty response.")
        return json.loads(text)
