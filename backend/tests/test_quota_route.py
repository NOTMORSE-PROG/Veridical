"""V-009: GET /quota — dashboard meter (screens 4e/4u)."""

from fastapi.testclient import TestClient

from app.config import get_settings


def test_quota_route_reports_fake_mode_honestly(monkeypatch):
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/quota")

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "fake"
    assert body["calls_used"] == 0
    assert body["cache_hit_rate"] == 0.0
    assert "reset_at" in body
