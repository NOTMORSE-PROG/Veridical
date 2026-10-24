"""F3/F4/F5 capability resolution without a provider or database."""

from unittest.mock import AsyncMock

import pytest

import app.llm as factory
from app.config import Settings
from app.errors import (
    ApiDownError,
    InvalidApiKeyError,
    QuotaExhaustedError,
    RuntimeApiKeyRejectedError,
)
from app.llm.base import LLMNotConfiguredError
from app.llm.resolution import AIUnavailableError, RunLLMClient


@pytest.mark.parametrize("cause", ["ai_not_configured", "ai_key_rejected"])
async def test_terminal_cause_is_remembered_only_by_one_execution(cause):
    client = AsyncMock()
    resolver = AsyncMock(return_value=client)
    if cause == "ai_not_configured":
        resolver.side_effect = LLMNotConfiguredError("private constructor diagnostic")
    else:
        client.complete.side_effect = RuntimeApiKeyRejectedError("private rejection diagnostic")
    run = RunLLMClient(resolver)
    assert resolver.await_count == 0
    for index in range(2):
        with pytest.raises(AIUnavailableError) as caught:
            await run.complete("semantic_grading", "text")
        assert caught.value.cause == cause
        assert str(caught.value) == cause
        assert caught.value.request_attempted is (cause == "ai_key_rejected" and index == 0)
    assert resolver.await_count == 1
    assert client.complete.await_count == (0 if cause == "ai_not_configured" else 1)
    client.complete.side_effect = None
    client.complete.return_value = {"recovered": True}
    resolver.side_effect = None
    assert await RunLLMClient(resolver).complete("semantic_grading", "text") == {"recovered": True}
    with pytest.raises(AIUnavailableError):
        await run.complete("semantic_grading", "text")


@pytest.mark.parametrize(
    "failure",
    [ApiDownError("outage"), QuotaExhaustedError("quota"), InvalidApiKeyError("save probe")],
)
async def test_nonterminal_and_save_probe_errors_are_not_reclassified(failure):
    client = AsyncMock()
    client.complete.side_effect = failure
    resolver = AsyncMock(return_value=client)
    run = RunLLMClient(resolver)
    for _ in range(2):
        with pytest.raises(type(failure)) as caught:
            await run.complete("kind", "text")
        assert caught.value is failure
    assert client.complete.await_count == 2
    assert resolver.await_count == 1


async def test_unexpected_resolution_error_remains_original():
    failure = RuntimeError("storage/programming failure")
    resolver = AsyncMock(side_effect=failure)
    run = RunLLMClient(resolver)
    with pytest.raises(RuntimeError) as caught:
        await run.complete("kind", "text")
    assert caught.value is failure


async def test_request_identity_and_success_survive_adapter():
    client = AsyncMock()
    answer = {"result": "original"}
    client.complete.return_value = answer
    run = RunLLMClient(AsyncMock(return_value=client))
    assert await run.complete("kind", "text", prompt_version="pinned", check_run_id=42) is answer
    client.complete.assert_awaited_once_with(
        "kind", "text", prompt_version="pinned", check_run_id=42
    )


@pytest.mark.parametrize("failure_type", [None, QuotaExhaustedError, RuntimeApiKeyRejectedError])
async def test_own_only_client_does_not_require_shared_configuration(monkeypatch, failure_type):
    settings = Settings(_env_file=None, veridical_fake_llm=False, gemini_api_key=None)
    own = AsyncMock()
    own.complete.return_value = {"own": True}
    if failure_type:
        own.complete.side_effect = failure_type("controlled own failure")
    monkeypatch.setattr(factory, "_real_client", None)
    monkeypatch.setattr(factory, "_resolve_own_client", AsyncMock(return_value=own))

    def forbidden_shared(*args, **kwargs):
        pytest.fail("Own-only capability must not construct an absent shared client")

    monkeypatch.setattr(factory, "_shared_client", forbidden_shared)
    client = await factory.get_llm_client_for(object(), settings, 17)
    assert client is own
    if failure_type:
        with pytest.raises(failure_type):
            await client.complete("kind", "text")
    else:
        assert await client.complete("kind", "text") == {"own": True}


async def test_successful_applicable_fallback_does_not_poison_run():
    primary = AsyncMock()
    primary.complete.side_effect = RuntimeApiKeyRejectedError("own rejection")
    shared = AsyncMock()
    shared.complete.return_value = {"shared": True}
    run = RunLLMClient(AsyncMock(return_value=factory.FallbackLLMClient(primary, shared)))
    for _ in range(2):
        assert await run.complete("kind", "text") == {"shared": True}
    assert primary.complete.await_count == shared.complete.await_count == 2


async def test_last_fallback_classification_is_preserved():
    primary = AsyncMock()
    primary.complete.side_effect = RuntimeApiKeyRejectedError("own rejection")
    shared = AsyncMock()
    failure = ApiDownError("shared outage")
    shared.complete.side_effect = failure
    run = RunLLMClient(AsyncMock(return_value=factory.FallbackLLMClient(primary, shared)))
    with pytest.raises(ApiDownError) as caught:
        await run.complete("kind", "text")
    assert caught.value is failure
