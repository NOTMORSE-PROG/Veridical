"""V-009: get_llm_client's real-mode wiring (construction only — no network
call happens just from building genai.Client, so this needs no live API key
or DB connection)."""

import app.llm as llm_module
from app.config import Settings
from app.llm import GeminiLLMClient, get_llm_client
from app.llm.fake import FakeLLMClient
from app.llm.queue import LLMQueue


def test_factory_wires_fake_client_with_a_session_factory():
    """BUG-038: the real app wiring (as opposed to a bare `FakeLLMClient()`
    in a test) must always pass a `session_factory`, or fake-mode completions
    go back to writing no audit trail at all — the exact gap the bug was
    about."""
    settings = Settings(_env_file=None, veridical_fake_llm=True)
    client = get_llm_client(settings)
    assert isinstance(client, FakeLLMClient)
    assert client._session_factory is not None


def test_factory_returns_gemini_client_when_key_configured():
    llm_module._real_client = None
    settings = Settings(_env_file=None, veridical_fake_llm=False, gemini_api_key="test-key")
    client = get_llm_client(settings)
    assert isinstance(client, GeminiLLMClient)
    assert isinstance(client.queue, LLMQueue)
    llm_module._real_client = None


def test_factory_reuses_the_same_client_across_calls():
    """The RateGovernor's sliding window must persist across calls within a
    process — a fresh client per call would silently defeat the RPM
    governor (ENGINEERING §3)."""
    llm_module._real_client = None
    settings = Settings(_env_file=None, veridical_fake_llm=False, gemini_api_key="test-key")
    first = get_llm_client(settings)
    second = get_llm_client(settings)
    assert first is second
    llm_module._real_client = None
