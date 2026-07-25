"""GeminiTransport error handling — no network, the SDK client is
replaced with a stub so malformed-response paths are reproducible
(the JSONDecodeError this guards was found live, not imagined: V-011)."""

from dataclasses import dataclass
from typing import Any

import pytest

from app.llm.queue import TransportServerError
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
    assert result == {"pong": True}
