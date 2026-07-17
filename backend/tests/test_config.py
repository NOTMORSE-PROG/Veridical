from app.config import Settings


def _bare_settings(**env: str) -> Settings:
    # _env_file=None: ignore any developer .env so tests are deterministic.
    return Settings(_env_file=None, **env)


def test_defaults_require_no_env(monkeypatch):
    for var in ("VERIDICAL_ENV", "VERIDICAL_FAKE_LLM", "DATABASE_URL", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = _bare_settings()
    assert s.veridical_env == "dev"
    assert s.veridical_fake_llm is False
    assert s.database_url.startswith("postgresql://")
    assert s.gemini_api_key is None


def test_fake_llm_flag_parses_1_as_true(monkeypatch):
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    assert _bare_settings().veridical_fake_llm is True


def test_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@example:5432/x")
    monkeypatch.setenv("VERIDICAL_ENV", "prod")
    s = _bare_settings()
    assert s.database_url == "postgresql://u:p@example:5432/x"
    assert s.veridical_env == "prod"


def test_blank_patterns_file_env_means_unset(monkeypatch):
    """.env.example ships INGEST_PATTERNS_FILE= (blank); that must read as
    None, not Path('.') — found live: ingestion crashed with a PermissionError
    trying to read the current directory as the patterns file."""
    from app.config import get_settings

    monkeypatch.setenv("INGEST_PATTERNS_FILE", "")
    get_settings.cache_clear()
    assert get_settings().ingest_patterns_file is None
