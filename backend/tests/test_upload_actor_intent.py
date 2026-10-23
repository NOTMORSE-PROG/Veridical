"""F1/F2/F7/F9: captured upload intent cannot select a different instructor.

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
from app.library import router as library_router
from app.main import veridical_error_handler
from app.rubric import router as rubric_router


class BusinessBoundaryReached(Exception):
    pass


@pytest.mark.parametrize(
    "path",
    [
        "/manuscripts/ingest",
        "/rubrics",
        "/library/entries",
        "/library/21/decision",
        "/library/21/remove",
    ],
)
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
    app.include_router(library_router.router)
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

    async def decision_boundary(session, instructor_id, manuscript_id, action, settings):
        reached.append(("library_decision", instructor_id))
        raise BusinessBoundaryReached

    async def remove_boundary(session, instructor_id, manuscript_id, settings):
        reached.append(("library_remove", instructor_id))
        raise BusinessBoundaryReached

    async def business_boundary_handler(request, exc):
        return JSONResponse(status_code=202, content={"boundary": "reached"})

    app.dependency_overrides[get_current_instructor] = auth
    app.dependency_overrides[get_session] = session
    app.add_exception_handler(BusinessBoundaryReached, business_boundary_handler)
    monkeypatch.setattr(ingest_router, "enforce_action_rate_limit", boundary)
    monkeypatch.setattr(rubric_router, "enforce_action_rate_limit", boundary)
    monkeypatch.setattr(library_router, "enforce_action_rate_limit", boundary)
    monkeypatch.setattr(library_router, "decide_library_entry", decision_boundary)
    monkeypatch.setattr(library_router, "remove_library_entry", remove_boundary)
    headers = {} if expected is None else {"X-Veridical-Expected-Instructor": expected}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = (
            {"json": {"action": "add"}}
            if path.endswith("decision")
            else {}
            if path.endswith("remove")
            else {"files": {"file": ("intent.docx", b"test")}}
        )
        response = await client.post(path, headers=headers, **body)
    assert response.status_code == status, response.text
    if status == 202:
        action = {
            "/manuscripts/ingest": "manuscript_ingest",
            "/rubrics": "rubric_upload",
            "/library/entries": "library_submission",
            "/library/21/decision": "library_decision",
            "/library/21/remove": "library_remove",
        }[path]
        assert reached == [(action, actor)]
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
