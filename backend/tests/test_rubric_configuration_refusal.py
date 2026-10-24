"""F2 pre-creation refusal, distinct from an uncertain parsing outcome."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import app.llm as factory
from app.auth.dependencies import get_upload_instructor
from app.config import Settings
from app.db import get_session
from app.errors import ConflictError
from app.llm.base import LLMNotConfiguredError
from app.main import app
from app.models.instructor import Instructor
from app.rubric import router

MESSAGE = (
    "AI format parsing could not start because no usable AI configuration was available. "
    "No format draft was created by this attempt. Review your AI settings or contact "
    "the deployment administrator, then try again."
)


@pytest.fixture
def upload_context(monkeypatch):
    actor = SimpleNamespace(id=71, gemini_api_key_encrypted=None)

    class Session:
        async def get(self, model, key):
            assert model is Instructor and key == actor.id
            return actor

    async def session():
        yield Session()

    async def instructor():
        return actor

    overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_upload_instructor] = instructor
    settings = Settings(
        _env_file=None,
        veridical_fake_llm=False,
        gemini_api_key=None,
        byok_encryption_key=None,
    )
    monkeypatch.setattr(router, "get_settings", lambda: settings)
    monkeypatch.setattr(router, "enforce_action_rate_limit", lambda *args: None)
    monkeypatch.setattr(factory, "_real_client", None)
    create = AsyncMock(side_effect=ConflictError("controlled service entry"))
    monkeypatch.setattr(router, "create_rubric_from_upload", create)
    try:
        yield settings, create
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


async def upload():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.post(
            "/rubrics", files={"file": ("format.docx", b"not read before admission")}
        )


async def test_missing_effective_configuration_is_a_known_before_creation_refusal(upload_context):
    _, create = upload_context
    response = await upload()
    assert response.status_code == 503
    assert response.json() == {"error": {"code": "ai_not_configured", "message": MESSAGE}}
    create.assert_not_awaited()


async def test_factory_diagnostic_is_not_exposed(upload_context, monkeypatch):
    _, create = upload_context
    monkeypatch.setattr(
        router,
        "get_llm_client_for",
        AsyncMock(
            side_effect=LLMNotConfiguredError("private constructor diagnostic and key material")
        ),
    )
    response = await upload()
    assert response.status_code == 503
    assert response.json()["error"]["message"] == MESSAGE
    assert "private" not in response.text
    create.assert_not_awaited()


@pytest.mark.parametrize("stage", ["factory", "service"])
async def test_other_or_post_creation_failures_are_not_no_draft_receipts(
    upload_context, monkeypatch, stage
):
    _, create = upload_context
    if stage == "factory":
        monkeypatch.setattr(
            router,
            "get_llm_client_for",
            AsyncMock(side_effect=ValueError("unrelated constructor failure")),
        )
    else:
        monkeypatch.setattr(router, "get_llm_client_for", AsyncMock(return_value=object()))
        create.side_effect = LLMNotConfiguredError("failure after entering creation")
    response = await upload()
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert create.await_count == (stage == "service")


@pytest.mark.parametrize("mode", ["own-only", "shared", "warm", "fake"])
async def test_effective_client_eligibility_still_reaches_creation(
    upload_context, monkeypatch, mode
):
    settings, create = upload_context
    if mode == "own-only":
        monkeypatch.setattr(factory, "_resolve_own_client", AsyncMock(return_value=object()))
        monkeypatch.setattr(
            factory,
            "_shared_client",
            lambda *_: pytest.fail(
                "An eligible personal client must not require shared credentials"
            ),
        )
    elif mode == "shared":
        settings.gemini_api_key = "controlled-constructor-only"
        monkeypatch.setattr(factory, "_build_real_client", lambda *_: object())
    elif mode == "warm":
        monkeypatch.setattr(factory, "_real_client", object())
    else:
        settings.veridical_fake_llm = True
        monkeypatch.setattr(factory.db, "get_session_factory", lambda: object())
    response = await upload()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    create.assert_awaited_once()
