import pytest

from app.config import Settings
from app.llm import FakeLLMClient, LLMNotConfiguredError, get_llm_client
from app.llm.base import UnknownPromptTypeError


async def test_fake_client_returns_fixture_json():
    client = FakeLLMClient()
    result = await client.complete("ping", "hello")
    assert result["pong"] is True


async def test_fake_client_rubric_decomposition_shape():
    client = FakeLLMClient()
    result = await client.complete("rubric_decomposition", "decompose this rubric")
    assert isinstance(result["criteria"], list)
    assert {c["type"] for c in result["criteria"]} <= {"structural", "semantic"}


async def test_unknown_prompt_type_raises_with_available_list():
    client = FakeLLMClient()
    with pytest.raises(UnknownPromptTypeError, match="ping"):
        await client.complete("no_such_prompt", "x")


def test_factory_returns_fake_when_enabled():
    settings = Settings(_env_file=None, veridical_fake_llm=True)
    assert isinstance(get_llm_client(settings), FakeLLMClient)


def test_factory_refuses_real_mode_until_v009():
    settings = Settings(_env_file=None, veridical_fake_llm=False)
    with pytest.raises(LLMNotConfiguredError, match="V-009"):
        get_llm_client(settings)
