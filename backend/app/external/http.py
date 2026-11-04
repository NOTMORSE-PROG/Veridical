"""Shared request helper for every provider client (V-028): rate-governed
(`app.ratelimit.RateGovernor`), retried with backoff on transient failure,
raising the ONE taxonomy code (`ApiDownError`) a caller needs to handle —
same shape as `app/llm/queue.py`'s transport retry, generalized past Gemini.

A 404 alone is a missing-record answer. Other non-retryable refusals
mean the lookup could not be completed, not that a publication is absent.
Malformed or non-object JSON uses the existing bounded retry budget. A rate
rejection stops retries and defers further provider admissions.
"""

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.config import Settings, get_settings
from app.errors import ApiDownError
from app.rate_governor import RateGovernor, RateLimitDeferredError

SleepFn = Callable[[float], Awaitable[None]]


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _rate_limit_delay(response: httpx.Response) -> float:
    fallback = get_settings().external_rate_limit_cooldown_seconds
    value = response.headers.get("Retry-After")
    if value is None:
        return fallback
    try:
        seconds = float(value)
    except ValueError:
        try:
            until = parsedate_to_datetime(value)
            if until.tzinfo is None:
                return fallback
            seconds = (until - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    return max(fallback, seconds) if math.isfinite(seconds) else fallback


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    """One client shared across provider calls in a request/stage.

    Ordinary providers retain automatic redirects. Governed book lookups
    override that default and admit each HTTPX-generated redirect explicitly.
    """
    return httpx.AsyncClient(timeout=settings.external_http_timeout_seconds, follow_redirects=True)


async def _get_with_paced_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    governor: RateGovernor,
    params: dict[str, str] | None,
    headers: dict[str, str] | None,
) -> httpx.Response:
    """Reuse HTTPX redirect requests while admitting every physical send."""
    await governor.acquire()
    response = await client.get(url, params=params, headers=headers, follow_redirects=False)
    for _ in range(client.max_redirects):
        request = response.next_request
        if request is None:
            return response
        await response.aclose()
        await governor.acquire()
        # next_request already applies HTTPX's credential/cookie rules. Do not
        # reapply the client's default auth to a request stripped on redirect.
        response = await client.send(request, follow_redirects=False, auth=None)
    if response.next_request is not None:
        request = response.next_request
        await response.aclose()
        raise httpx.TooManyRedirects("Exceeded maximum allowed redirects.", request=request)
    return response


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
    pace_redirects: bool = False,
) -> dict | None:
    """Returns the parsed JSON body, or None for a 404 (a clean "doesn't
    exist" answer, not a failure). Raises `ApiDownError` after exhausting
    retries on request/response failures, or immediately on another refusal."""
    sleep = sleep or _default_sleep
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            if pace_redirects:
                response = await _get_with_paced_redirects(
                    client, url, governor=governor, params=params, headers=headers
                )
            else:
                await governor.acquire()
                response = await client.get(url, params=params, headers=headers)
        except RateLimitDeferredError as exc:
            raise ApiDownError(f"{provider} lookup could not be completed (HTTP 429).") from exc
        except httpx.RequestError as exc:
            last_exc = exc
        else:
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                defer = getattr(governor, "defer_for", None)
                if defer is not None:
                    defer(_rate_limit_delay(response))
                raise ApiDownError(f"{provider} lookup could not be completed (HTTP 429).")
            if response.status_code >= 500:
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
