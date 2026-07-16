import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def clean_settings_cache():
    """Settings are lru_cached; tests that touch env vars need a fresh read."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
