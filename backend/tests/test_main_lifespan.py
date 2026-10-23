"""Auto-migration on startup (V-055 follow-up): production deploys used to
need a manual `alembic upgrade head` step (V-048) -- a real schema change
(migration 0011) landed with no automated way to apply it. Verifies the
gating logic only, not a real Alembic run (that's what CI's own
`alembic upgrade head` against the live Postgres service already proves
works, migration by migration)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


async def test_preparation_lifespan_closes_if_startup_fails_after_admission_start(monkeypatch):
    from app import main
    from app.ingest import admission

    monkeypatch.setenv("VERIDICAL_ENV", "dev")
    get_settings.cache_clear()
    try:
        with (
            patch("app.main.asyncio.Event", side_effect=RuntimeError("Controlled startup failure")),
            pytest.raises(RuntimeError, match="Controlled startup failure"),
        ):
            async with main._lifespan(main.app):
                pytest.fail("Startup must not have reached yield")
        assert admission._managed_lifespan is None
        assert not admission._managed_closing
        retired = admission._current
        assert retired is not None and retired.drained and not retired.accepting
        handle = admission.start_preparation_admission(get_settings())
        try:
            assert handle.controller is not retired
        finally:
            admission.begin_preparation_shutdown(handle)
            admission.finish_preparation_lifespan(handle)
    finally:
        get_settings.cache_clear()


def test_prod_env_triggers_a_migration_check_on_startup(monkeypatch):
    monkeypatch.setenv("VERIDICAL_ENV", "prod")
    get_settings.cache_clear()
    from app.main import app

    with (
        patch("app.main._upgrade_to_head") as mock_upgrade,
        patch("app.main._seed_programs_on_boot") as mock_seed,
        patch("app.main._prewarm_embedding_model_on_boot") as mock_prewarm,
    ):
        with TestClient(app):
            pass
        mock_upgrade.assert_called_once()
        mock_seed.assert_called_once()
        mock_prewarm.assert_called_once()
    get_settings.cache_clear()


def test_a_prewarm_failure_never_fails_the_boot(monkeypatch):
    """BUG-152 (backend review finding, live-verified): an exception
    raised in a FastAPI lifespan BEFORE `yield` prevents the app from
    serving ANY request at all. Render's entire process boots fresh on
    every spin-down, so an unguarded pre-warm would turn one transient
    HuggingFace hiccup into total downtime for every instructor, not just
    the two checks that actually need the model -- strictly worse than
    the failure this fix exists to catch. A real load failure (mocked at
    the actual `get_embedding_model` call, not by mocking the wrapper
    itself away) must be swallowed, logged, and never stop the app from
    booting and serving a real request afterward."""
    monkeypatch.setenv("VERIDICAL_ENV", "prod")
    get_settings.cache_clear()
    from app.main import app

    with (
        patch("app.main._upgrade_to_head"),
        patch("app.main._seed_programs_on_boot"),
        patch("app.main.get_embedding_model", side_effect=RuntimeError("HF unreachable")),
        TestClient(app) as client,
    ):
        response = client.get("/health")
    assert response.status_code == 200
    get_settings.cache_clear()


def test_dev_env_never_runs_a_migration_on_startup(monkeypatch):
    monkeypatch.setenv("VERIDICAL_ENV", "dev")
    get_settings.cache_clear()
    from app.main import app

    with (
        patch("app.main._upgrade_to_head") as mock_upgrade,
        patch("app.main._seed_programs_on_boot") as mock_seed,
        patch("app.main._prewarm_embedding_model_on_boot") as mock_prewarm,
    ):
        with TestClient(app):
            pass
        mock_upgrade.assert_not_called()
        mock_seed.assert_not_called()
        # BUG-152: a test importing this module must never trigger a real
        # HuggingFace fetch by surprise -- same gate as the migration check.
        mock_prewarm.assert_not_called()
    get_settings.cache_clear()


def test_prod_env_autostarts_the_pipeline_worker_loop_even_with_the_flag_unset(monkeypatch):
    """BUG-136: production ran for a long time with this loop silently
    OFF -- `pipeline_worker_autostart` (default False) was never actually
    set in Render's real environment, despite a since-corrected comment
    claiming it was. A blocked check_run's "resumes automatically" promise
    was never true. The fix removes the human-configured-dashboard-var
    dependency entirely: prod always starts the loop, the same way it
    always runs migrations, regardless of this flag."""
    monkeypatch.setenv("VERIDICAL_ENV", "prod")
    monkeypatch.delenv("PIPELINE_WORKER_AUTOSTART", raising=False)
    get_settings.cache_clear()
    from app.main import app

    with (
        patch("app.main._upgrade_to_head"),
        patch("app.main._seed_programs_on_boot"),
        patch("app.main._prewarm_embedding_model_on_boot"),
        patch("app.main.worker_loop") as mock_worker_loop,
    ):
        with TestClient(app):
            pass
        mock_worker_loop.assert_called_once()
    get_settings.cache_clear()


def test_dev_env_starts_waiting_scheduler_without_periodic_polling(monkeypatch):
    monkeypatch.setenv("VERIDICAL_ENV", "dev")
    monkeypatch.delenv("PIPELINE_WORKER_AUTOSTART", raising=False)
    get_settings.cache_clear()
    from app.main import app

    with patch("app.main.worker_loop") as mock_worker_loop:
        with TestClient(app):
            pass
        mock_worker_loop.assert_called_once()
        assert mock_worker_loop.call_args.kwargs["periodic"] is False
    get_settings.cache_clear()


def test_explicit_flag_still_starts_the_worker_loop_outside_prod(monkeypatch):
    """The flag survives as an opt-in for exercising the loop locally
    without faking `veridical_env`."""
    monkeypatch.setenv("VERIDICAL_ENV", "dev")
    monkeypatch.setenv("PIPELINE_WORKER_AUTOSTART", "true")
    get_settings.cache_clear()
    from app.main import app

    with patch("app.main.worker_loop") as mock_worker_loop:
        with TestClient(app):
            pass
        mock_worker_loop.assert_called_once()
    get_settings.cache_clear()
