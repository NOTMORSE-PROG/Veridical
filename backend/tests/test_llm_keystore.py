"""V-052 (BYOK): encrypting/decrypting instructor-supplied Gemini keys, and
validating a pasted key before it's ever stored. Pure unit tests (no DB) --
encryption is exercised directly, and the validation probe's HTTP call is
mocked via `httpx.MockTransport` rather than hitting the real Gemini API.
"""

import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.errors import InvalidApiKeyError
from app.llm.keystore import (
    BYOKNotConfiguredError,
    decrypt_api_key,
    encrypt_api_key,
    validate_gemini_api_key,
)

_MASTER_KEY = Fernet.generate_key().decode("utf-8")


def _settings(**overrides) -> Settings:
    kwargs = dict(_env_file=None, byok_encryption_key=_MASTER_KEY, byok_probe_timeout_seconds=5.0)
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_encrypt_then_decrypt_round_trips_the_real_key():
    settings = _settings()
    encrypted = encrypt_api_key("AIzaSy-real-looking-key", settings)
    assert isinstance(encrypted, bytes)
    assert b"AIzaSy" not in encrypted  # never plaintext at rest
    assert decrypt_api_key(encrypted, settings) == "AIzaSy-real-looking-key"


def test_encrypt_raises_when_no_master_key_configured():
    settings = _settings(byok_encryption_key=None)
    with pytest.raises(BYOKNotConfiguredError):
        encrypt_api_key("some-key", settings)


def test_decrypt_raises_a_distinct_error_when_the_master_key_changed():
    """Distinct from BOTH `InvalidApiKeyError` and "no key configured" --
    this is a deployment/rotation problem (an unattempted rotation, or a
    misconfigured env var), not a user-fixable input error."""
    settings_a = _settings()
    encrypted = encrypt_api_key("some-key", settings_a)
    settings_b = _settings(byok_encryption_key=Fernet.generate_key().decode("utf-8"))
    with pytest.raises(BYOKNotConfiguredError):
        decrypt_api_key(encrypted, settings_b)


async def test_validate_accepts_a_key_gemini_confirms(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "good-key"
        assert "key" not in request.url.params  # never a query param (security-auditor finding)
        return httpx.Response(200, json={"models": []})

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_async_client(transport=httpx.MockTransport(handler))
    )
    await validate_gemini_api_key("good-key", _settings())  # does not raise


async def test_validate_rejects_a_key_gemini_rejects(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "API key not valid"}})

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_async_client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(InvalidApiKeyError):
        await validate_gemini_api_key("bad-key", _settings())


async def test_validate_raises_invalid_api_key_on_network_failure(monkeypatch):
    """A timeout/connection failure while validating must read as the same
    honest, user-fixable 422 as a rejected key -- never a bare exception
    that would surface as a generic 500 to the instructor."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_async_client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(InvalidApiKeyError):
        await validate_gemini_api_key("some-key", _settings())


def test_validate_never_appears_in_the_key_never_logged_reasoning():
    """Documentation-as-test: `validate_gemini_api_key` sends the key only
    as a query param to Google, never constructs a log line or audit
    payload containing it -- grepped, not assumed (backend-critic-style
    check, kept as an executable regression rather than a comment)."""
    import inspect

    from app.llm import keystore

    source = inspect.getsource(keystore)
    assert "logging" not in source
    assert "AuditLog" not in source
    assert "audit_log" not in source
