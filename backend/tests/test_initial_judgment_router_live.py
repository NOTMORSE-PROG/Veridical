"""D-034 normal cookie authentication and the persisted initial-record route."""

import os

import pytest

from tests import test_judgment_router_live as revisions

api_scratch_url = revisions.api_scratch_url
client = revisions.client
logged_in_with_a_done_run = revisions.logged_in_with_a_done_run

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")


def test_initial_record_route_freeze_history_and_private_access(logged_in_with_a_done_run):
    http, run = logged_in_with_a_done_run
    report = http.get(f"/check-runs/{run}/report")
    assert report.status_code == 200
    criterion = report.json()["results"][0]["criterion_id"]
    base = f"/check-runs/{run}/criteria/{criterion}/judgment"
    context = http.get(base)
    assert context.status_code == 200 and context.headers["cache-control"] == "no-store"
    assert context.json()["can_record"] and not context.json()["can_revise"]
    reason = (
        "I compared the original requirement with this manuscript before recording my judgment."
    )
    body = {"expected_revision": 0, "resolution": "mark_fail", "reason": reason}
    for change in (
        {"expected_revision": True},
        {"expected_judgment_revision": 0},
        {"reason": " "},
        {"actor_id": 2},
        {"resolution": "accept_majority"},
    ):
        assert http.post(base + "/records", json={**body, **change}).status_code == 422
    decision = http.post(
        f"/check-runs/{run}/decision",
        json={"decision": "returned", "note": reason, "expected_revision": 0},
    )
    assert decision.status_code == 200, decision.text
    assert http.post(base + "/records", json={**body, "expected_revision": 1}).status_code == 409
    reopen = http.post(f"/check-runs/{run}/reopen", json={"reason": reason, "expected_revision": 1})
    assert reopen.status_code == 200, reopen.text
    saved = http.post(base + "/records", json={**body, "expected_revision": 2})
    assert saved.status_code == 200, saved.text
    assert saved.headers["cache-control"] == "no-store"
    ack = saved.json()
    assert ack["entry"]["schema_version"] == 2 and ack["entry"]["before"] is None
    assert ack["context"]["current"]["judgment_revision"] == 1
    assert ack["entry"]["original_assessment"]["original_outcome"] == "passed"
    assert ack["context"]["assessment_revision"] == ack["report"]["assessment_revision"] == 3
    assert http.post(base + "/records", json={**body, "expected_revision": 2}).status_code == 409
    assert http.post(base + "/records", json={**body, "expected_revision": 3}).status_code == 409
    exported = http.get(f"/check-runs/{run}/report/export.pdf")
    assert exported.status_code == 200
    import fitz

    with fitz.open(stream=exported.content, filetype="pdf") as document:
        text = " ".join(" ".join(page.get_text() for page in document).split())
    assert "Instructor Judgments" in text and "Instructor judgment recorded." in text
    assert "Escalation Resolutions" not in text and reason in text
    revised_reason = "Further manuscript evidence supports my revised instructor judgment."
    revised = http.post(
        base + "/revisions",
        json={
            "expected_revision": 3,
            "expected_judgment_revision": 1,
            "resolution": "mark_pass",
            "reason": revised_reason,
        },
    )
    assert revised.status_code == 200, revised.text
    history = http.get(
        base + "/history", params={"expected_revision": 4, "expected_judgment_revision": 2}
    )
    assert history.status_code == 200, history.text
    assert [entry["action"] for entry in history.json()["entries"]] == [
        "judgment_revised",
        "judgment_recorded",
    ]
    share = http.post(f"/check-runs/{run}/share", json={})
    assert share.status_code == 200, share.text
    token = share.json()["token"]
    assert http.post("/auth/logout").status_code == 200
    assert http.get(base).status_code == 401
    assert http.post(base + "/records", json=body).status_code == 401
    assert (
        http.post(
            base + "/records", json=body, headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )
    public = http.get(f"/shared/{token}/report")
    assert public.status_code == 200
    for private in (
        reason,
        revised_reason,
        "original_assessment",
        "judgment_revision",
        "performed_by",
    ):
        assert private not in public.text
    assert (
        http.post(
            "/auth/login", json={"email": "stranger@tip.edu.ph", "password": "other!"}
        ).status_code
        == 200
    )
    assert http.get(base).status_code == 404
    assert http.post(base + "/records", json=body).status_code == 404
    assert (
        http.get(
            base + "/history", params={"expected_revision": 4, "expected_judgment_revision": 2}
        ).status_code
        == 404
    )
    assert http.post("/auth/logout").status_code == 200
