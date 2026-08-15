"""V-052 (BYOK): encrypting instructor-supplied Gemini keys at rest, and
validating a pasted key before it is ever stored.

Fernet (symmetric, authenticated AES-128-CBC + HMAC-SHA256) is deliberately
not a hand-rolled scheme (charter: boring tech for real security surface).
Rotation of `settings.byok_encryption_key` is NOT solved here -- it would
need a decrypt-with-old/re-encrypt-with-new pass over every stored key; not
attempted, documented as a known limitation (ticket V-052 edge case).
"""

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.errors import InvalidApiKeyError

__all__ = [
    "BYOKNotConfiguredError",
    "decrypt_api_key",
    "encrypt_api_key",
    "validate_gemini_api_key",
]

_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


class BYOKNotConfiguredError(Exception):
    """No master encryption key set on this deployment (settings.py should
    have already hidden the feature via `byok_available` -- reaching this
    means a caller skipped that check)."""


def _fernet(settings: Settings) -> Fernet:
    if not settings.byok_encryption_key:
        raise BYOKNotConfiguredError(
            "BYOK_ENCRYPTION_KEY is not set on this deployment; instructor "
            "API keys cannot be encrypted or read."
        )
    return Fernet(settings.byok_encryption_key.encode("utf-8"))


def encrypt_api_key(raw_key: str, settings: Settings) -> bytes:
    return _fernet(settings).encrypt(raw_key.encode("utf-8"))


def decrypt_api_key(encrypted: bytes, settings: Settings) -> str:
    try:
        return _fernet(settings).decrypt(bytes(encrypted)).decode("utf-8")
    except InvalidToken as exc:
        # Only reachable if BYOK_ENCRYPTION_KEY changed under a stored key
        # (an unattempted rotation, or a misconfigured deployment) -- never
        # silently treated as "no key configured", since that would make a
        # spent/broken key look like a normal fallback-to-shared case.
        raise BYOKNotConfiguredError(
            "Stored instructor API key could not be decrypted with the "
            "current BYOK_ENCRYPTION_KEY (it may have changed)."
        ) from exc


async def validate_gemini_api_key(api_key: str, settings: Settings) -> None:
    """The AC's "real probe call": `GET /models`, authenticated by the key,
    spends zero generation quota (unlike `tools/probe_gemini_quota.py`'s own
    `call_once`, which is the cheapest possible generation call but still
    costs 1 real unit) -- this only needs to know the key is live, not that
    it can generate anything. Raises `InvalidApiKeyError` (never a bare
    exception) on any non-2xx response, so a mistyped or revoked key reads
    as an honest, user-fixable 422.
    """
    # Header, not a `?key=` query param (security-auditor finding, Low):
    # not a live vulnerability either way (destination host is the
    # hardcoded constant above, and neither httpx nor httpcore log request
    # URLs by default), but a header keeps this consistent with the real
    # transport's own auth method (`GeminiTransport`, via the `google-genai`
    # SDK's `x-goog-api-key` header) and narrows incidental exposure surface
    # (proxy/access logs) if either is ever added in front of this call.
    async with httpx.AsyncClient(timeout=settings.byok_probe_timeout_seconds) as client:
        try:
            response = await client.get(
                f"{_API_ROOT}/models",
                params={"pageSize": 1},
                headers={"x-goog-api-key": api_key},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise InvalidApiKeyError(
                "Could not reach Gemini to verify this key. Check your connection and try again."
            ) from exc
    if response.status_code != 200:
        raise InvalidApiKeyError(
            "Gemini rejected this key. Check that it's correct and still active, then try again."
        )
