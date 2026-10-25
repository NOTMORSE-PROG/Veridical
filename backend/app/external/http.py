"""Shared request helper for every provider client (V-028): rate-governed
(`app.ratelimit.RateGovernor`), retried with backoff on transient failure,
raising the ONE taxonomy code (`ApiDownError`) a caller needs to handle —
same shape as `app/llm/queue.py`'s transport retry, generalized past Gemini.

A 404 alone is a missing-record answer. Other non-retryable refusals
mean the lookup could not be completed, not that a publication is absent.
Malformed or non-object JSON uses the existing bounded retry budget.
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings
from app.errors import ApiDownError
from app.rate_governor import RateGovernor

SleepFn = Callable[[float], Awaitable[None]]


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    """One client shared across every provider call in a request/stage —
    `follow_redirects=True` is required (Open Library's `/isbn/{isbn}.json`
    302s to the real edition record, confirmed live)."""
    return httpx.AsyncClient(timeout=settings.external_http_timeout_seconds, follow_redirects=True)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    provider: str,
    governor: RateGovernor,
    max_retries: int,
    retry_base_seconds: float,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    sleep: SleepFn | None = None,
) -> dict | None:
    """Returns the parsed JSON body, or None for a 404 (a clean "doesn't
    exist" answer, not a failure). Raises `ApiDownError` after exhausting
    retries on request/response failures, or immediately on another refusal."""
    sleep = sleep or _default_sleep
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        await governor.acquire()
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.RequestError as exc:
            last_exc = exc
        else:
            if response.status_code == 404:
                return None
            if response.status_code == 429 or response.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"{provider} returned {response.status_code}",
                    request=response.request,
                    response=response,
                )
            elif response.status_code >= 400:
                raise ApiDownError(
                    f"{provider} lookup could not be completed (HTTP {response.status_code})."
                )
            else:
                try:
                    payload = response.json()
                except ValueError as exc:
                    last_exc = exc
                else:
                    if isinstance(payload, dict):
                        return payload
                    last_exc = ValueError(f"{provider} returned a non-object JSON response.")
        if attempt < max_retries:
            await sleep(retry_base_seconds * (2**attempt))
    raise ApiDownError(
        f"{provider} lookup could not be completed after {max_retries + 1} attempts."
    ) from last_exc
