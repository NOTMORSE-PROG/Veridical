"""Auto-migration on startup (V-055 follow-up): production deploys used to
need a manual `alembic upgrade head` step (V-048) -- a real schema change
(migration 0011) landed with no automated way to apply it. Verifies the
gating logic only, not a real Alembic run (that's what CI's own
`alembic upgrade head` against the live Postgres service already proves
works, migration by migration)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import get_settings


def test_prod_env_triggers_a_migration_check_on_startup(monkeypatch):
    monkeypatch.setenv("VERIDICAL_ENV", "prod")
    get_settings.cache_clear()
    from app.main import app

    with (
        patch("app.main._upgrade_to_head") as mock_upgrade,
        patch("app.main._seed_programs_on_boot") as mock_seed,
    ):
        with TestClient(app):
            pass
        mock_upgrade.assert_called_once()
        mock_seed.assert_called_once()
    get_settings.cache_clear()


def test_dev_env_never_runs_a_migration_on_startup(monkeypatch):
    monkeypatch.setenv("VERIDICAL_ENV", "dev")
    get_settings.cache_clear()
    from app.main import app

    with (
        patch("app.main._upgrade_to_head") as mock_upgrade,
        patch("app.main._seed_programs_on_boot") as mock_seed,
    ):
        with TestClient(app):
            pass
        mock_upgrade.assert_not_called()
        mock_seed.assert_not_called()
    get_settings.cache_clear()
