import os

import pytest

from app.config import get_settings

# A bare `uv run pytest` skips every DB-backed test and still exits 0. On
# 2026-08-16 that was 300 of 722 tests, 41.5%, green. The skip guard used
# across ~45 test files checks whether DATABASE_URL is SET, not whether the
# database is reachable, so its own advice ("use local docker-compose") does
# not clear it. TESTING.md documents a prior incident of exactly this shape
# (399 passed / 189 skipped, reported as a full run); the fix applied then
# stopped a lint failure from hiding a test result and did nothing about a
# green build where a third of the suite never ran.
#
# So: say so, loudly, every time. And in CI -- where the database IS
# provisioned and a skip means something broke -- fail instead of warning.
_SKIP_FLOOR = int(os.environ.get("VERIDICAL_MAX_SKIPS", "5"))


@pytest.fixture(autouse=True)
def clean_settings_cache():
    """Settings are lru_cached; tests that touch env vars need a fresh read."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Refuse to let a heavily-skipped run look like a verified one."""
    skipped = len(terminalreporter.stats.get("skipped", []))
    if skipped <= _SKIP_FLOOR:
        return

    has_db = bool(os.environ.get("DATABASE_URL"))
    in_ci = bool(os.environ.get("CI"))

    terminalreporter.write_sep("=", "INCOMPLETE VERIFICATION", red=True, bold=True)
    terminalreporter.write_line(f"{skipped} tests were SKIPPED. This run did NOT verify the suite.")
    if not has_db:
        terminalreporter.write_line(
            "DATABASE_URL is not set, so every database-backed test was skipped."
        )
        terminalreporter.write_line(
            "Full run:  DATABASE_URL=postgresql://veridical:veridical"
            "@localhost:5433/veridical uv run pytest"
        )
        terminalreporter.write_line(
            "Bringing docker-compose up is NOT enough on its own -- the guard "
            "reads the variable, not the database."
        )
    terminalreporter.write_line(
        "Do not report this as 'tests green' anywhere: not in a ticket's QA "
        "evidence, not on the board, not in the paper (DoD item 10, D-013)."
    )

    if in_ci and has_db:
        # CI provisions Postgres, so a skip here means something is wrong.
        terminalreporter.write_line("")
        terminalreporter.write_line(
            f"FAILING: CI provides a database, so at most {_SKIP_FLOOR} skips "
            "are expected. Override with VERIDICAL_MAX_SKIPS if a new "
            "legitimately-skipped test is added, and say why in the ticket."
        )
        pytest.exit(f"{skipped} skipped tests in CI (floor {_SKIP_FLOOR})", returncode=1)
