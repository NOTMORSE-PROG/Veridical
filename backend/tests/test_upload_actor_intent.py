"""F1/F2/F9: captured upload intent cannot select a different instructor.

Actual routers and authentication dependency composition; the first business
boundary is a sentinel. No database, upload service or provider is exercised.
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_instructor
from app.db import get_session
from app.errors import UnauthenticatedError, VeridicalError
from app.ingest import router as ingest_router
from app.main import veridical_error_handler
from app.rubric import router as rubric_router


class BusinessBoundaryReached(Exception):
    pass


@pytest.mark.parametrize("path", ["/manuscripts/ingest", "/rubrics"])
@pytest.mark.parametrize(
    ("actor", "expected", "status"),
    [
        (2, "1", 409),
        (1, "2", 409),
        (2, "2", 202),
        (2, None, 202),
        (2, "0", 422),
        (2, "-1", 422),
        (2, "wrong", 422),
        (None, "1", 401),
    ],
)
async def test_upload_intent_precedes_every_application_boundary(
    monkeypatch, path, actor, expected, status
):
    app = FastAPI()
    app.include_router(ingest_router.router)
    app.include_router(rubric_router.router)
    app.add_exception_handler(VeridicalError, veridical_error_handler)
    reached = []

    async def auth():
        if actor is None:
            raise UnauthenticatedError("Not signed in.")
        return SimpleNamespace(id=actor)

    async def session():
        yield object()

    def boundary(settings, action, instructor_id):
        reached.append((action, instructor_id))
        raise BusinessBoundaryReached

    async def business_boundary_handler(request, exc):
        return JSONResponse(status_code=202, content={"boundary": "reached"})

    app.dependency_overrides[get_current_instructor] = auth
    app.dependency_overrides[get_session] = session
    app.add_exception_handler(BusinessBoundaryReached, business_boundary_handler)
    monkeypatch.setattr(ingest_router, "enforce_action_rate_limit", boundary)
    monkeypatch.setattr(rubric_router, "enforce_action_rate_limit", boundary)
    headers = {} if expected is None else {"X-Veridical-Expected-Instructor": expected}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            path, headers=headers, files={"file": ("intent.docx", b"test")}
        )
    assert response.status_code == status, response.text
    if status == 202:
        assert reached == [
            ("manuscript_ingest" if path.endswith("ingest") else "rubric_upload", actor)
        ]
    else:
        assert reached == []
    if status == 409:
        assert response.json() == {
            "error": {
                "code": "actor_context_changed",
                "message": (
                    "Your signed-in account changed. Open the upload form again to continue."
                ),
            }
        }
