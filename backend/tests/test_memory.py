"""BUG-532: freed heap pages are handed back after heavy work."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from app import memory


@pytest.fixture(autouse=True)
def _fresh_lookup():
    memory._malloc_trim.cache_clear()
    yield
    memory._malloc_trim.cache_clear()


def test_trim_is_called_with_zero_padding_when_available():
    trim = MagicMock(return_value=1)
    with patch.object(memory, "_malloc_trim", return_value=trim):
        memory.return_freed_memory()
    trim.assert_called_once_with(0)


def test_no_trim_function_is_a_quiet_no_op():
    with patch.object(memory, "_malloc_trim", return_value=None):
        memory.return_freed_memory()


def test_other_platforms_have_no_trim_function(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert memory._malloc_trim() is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc only")
def test_glibc_trim_is_found_on_linux():
    assert memory._malloc_trim() is not None
    memory.return_freed_memory()
