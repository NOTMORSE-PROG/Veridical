"""V-077: Library submissions have their own rate-limit budget, separate from
the shared D-020 action limit (they spend no Gemini quota, but each one is
real CPU and storage work)."""

from app.config import Settings
from app.ratelimit import _max_attempts_for


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_library_submissions_use_their_own_budget():
    settings = _settings(
        library_submission_rate_limit_max_attempts=7, action_rate_limit_max_attempts=3
    )
    assert _max_attempts_for(settings, "library_submission") == 7


def test_every_other_scope_keeps_the_shared_budget():
    settings = _settings(
        library_submission_rate_limit_max_attempts=7, action_rate_limit_max_attempts=3
    )
    assert _max_attempts_for(settings, "manuscript_ingest") == 3
    assert _max_attempts_for(settings, "check_run") == 3
