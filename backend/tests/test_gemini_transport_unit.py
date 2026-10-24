"""GeminiTransport error handling — no network, the SDK client is
replaced with a stub so malformed-response paths are reproducible
(the JSONDecodeError this guards was found live, not imagined: V-011)."""

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from google.genai.errors import ClientError, ServerError

from app.llm.queue import (
    TransportDailyQuotaExhausted,
    TransportInvalidKey,
    TransportModelUnavailable,
    TransportRateLimited,
    TransportServerError,
)
from app.llm.transport import GeminiTransport


@dataclass
class _FakeResponse:
    text: str | None


class _FakeModels:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def generate_content(self, **kwargs: Any) -> _FakeResponse:
        return self._response


class _FakeAio:
    def __init__(self, response: _FakeResponse):
        self.models = _FakeModels(response)


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self.aio = _FakeAio(response)


def _transport_with_response(text: str | None) -> GeminiTransport:
    transport = GeminiTransport(api_key="test-key", timeout_seconds=1.0)
    transport._client = _FakeClient(_FakeResponse(text=text))  # swap in the test double
    return transport


async def test_malformed_json_response_raises_transport_server_error_not_json_decode_error():
    """Found live (V-011): response_mime_type=application/json does not
    guarantee syntactically valid JSON. A raw JSONDecodeError used to
    escape generate() entirely, bypassing the queue's retry/backoff."""
    transport = _transport_with_response("{not valid json")
    with pytest.raises(TransportServerError):
        await transport.generate(model="m", prompt="p", temperature=0.0)


async def test_empty_response_raises_transport_server_error():
    transport = _transport_with_response("")
    with pytest.raises(TransportServerError):
        await transport.generate(model="m", prompt="p", temperature=0.0)


async def test_valid_json_response_parses_normally():
    transport = _transport_with_response('{"pong": true}')
    result = await transport.generate(model="m", prompt="p", temperature=0.0)
    assert result.payload == {"pong": True}
    assert result.model_version is None


async def test_timeout_maps_to_recoverable_transport_failure_and_sdk_cannot_retry():
    transport = _transport_with_response(None)

    async def timed_out(**kwargs):
        assert kwargs["config"].http_options.retry_options.attempts == 1
        raise httpx.ReadTimeout("controlled")

    transport._client.aio.models.generate_content = timed_out
    with pytest.raises(TransportServerError, match="timed out"):
        await transport.generate(model="m", prompt="p", temperature=0)


def _transport_with_error(error):
    transport = _transport_with_response(None)

    async def rejected(**kwargs):
        raise error

    transport._client.aio.models.generate_content = rejected
    return transport


@pytest.mark.parametrize("model", ["retired", "models/retired"])
@pytest.mark.parametrize(
    "message",
    [
        "models/retired is not found for API version v1beta, "
        "or is not supported for generateContent.",
        "models/retired was not found for API version v1beta.",
        "models/retired is not supported for generateContent.",
    ],
)
async def test_recognized_exact_model_404_is_sanitized_availability(model, message):
    error = ClientError(404, {"error": {"status": "NOT_FOUND", "message": message}})
    transport = _transport_with_error(error)
    with pytest.raises(TransportModelUnavailable) as caught:
        await transport.generate(model=model, prompt="private prompt", temperature=0)
    assert str(caught.value) == "The requested model cannot serve this operation."
    assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    "code,status,message",
    [
        (404, "NOT_FOUND", "files/input was not found"),
        (404, "NOT_FOUND", "models/retired-other is not found for API version v1beta"),
        (404, "NOT_FOUND", "models/retired is not found in the input document"),
        (404, "NOT_FOUND", "file missing; models/retired is not found for API version v1beta"),
        (404, "NOT_FOUND", "models/retired"),
        (404, "NOT_FOUND", None),
        (404, "UNKNOWN", "models/retired is not found for API version v1beta"),
        (400, "NOT_FOUND", "models/retired is not found for API version v1beta"),
        (400, "INVALID_ARGUMENT", "Malformed request"),
    ],
)
async def test_other_client_errors_are_not_reclassified(code, status, message):
    error = ClientError(code, {"error": {"status": status, "message": message}})
    transport = _transport_with_error(error)
    with pytest.raises(ClientError) as caught:
        await transport.generate(model="retired", prompt="p", temperature=0)
    assert caught.value is error


@pytest.mark.parametrize(
    "code,message,expected",
    [
        (401, "Rejected key", TransportInvalidKey),
        (403, "Denied key", TransportInvalidKey),
        (429, "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", TransportRateLimited),
        (429, "GenerateRequestsPerDayPerProjectPerModel-FreeTier", TransportDailyQuotaExhausted),
        (503, "Service unavailable", TransportServerError),
    ],
)
async def test_existing_provider_failure_classes_are_preserved(code, message, expected):
    error_type = ServerError if code >= 500 else ClientError
    transport = _transport_with_error(error_type(code, {"error": {"message": message}}))
    with pytest.raises(expected):
        await transport.generate(model="retired", prompt="p", temperature=0)
