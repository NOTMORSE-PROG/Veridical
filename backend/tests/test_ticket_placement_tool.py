"""BUG-230: the board registers must exactly mirror ticket placement.

The production tool lives at repository root rather than in the backend
package. Load it by path so these regressions run in the ordinary backend CI
suite without turning ``tools/`` into an application package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "check_ticket_placement.py"


@pytest.fixture(scope="module")
def placement_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_ticket_placement_under_test", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_ticket(folder: Path, ticket_id: str, status: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ticket_id}.md").write_text(
        f"# {ticket_id}\n\nFound: test · Status: **{status}**\n", encoding="utf-8"
    )


def _board_fixture(
    tool: ModuleType,
    *,
    open_rows: list[str],
    closed_rows: list[str],
    narrative: str = "",
) -> str:
    open_table = "\n".join(f"| `{ticket_id}` | Medium | open | why |" for ticket_id in open_rows)
    closed_table = "\n".join(f"| `{ticket_id}` | test | fixed |" for ticket_id in closed_rows)
    return f"""# BOARD

## Open bugs - 1

{narrative}

| ID | Severity | What is wrong | Why it matters |
|---|---|---|---|
{open_table}

## Open story tickets - 0

# {tool.CLOSED_MARKER} 1

| ID | Fixed in | What it was |
|---|---|---|
{closed_table}

## {tool.GAPS_MARKER}
"""


def _run_fixture(
    placement_tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    open_rows: list[str],
    closed_rows: list[str],
    narrative: str = "",
    write_board: bool = True,
) -> int:
    tickets = tmp_path / "tickets"
    _write_ticket(tickets / "BUGS" / "open", "BUG-001", "TODO")
    _write_ticket(tickets / "BUGS" / "fixed", "BUG-002", "FIXED")
    board = tickets / "BOARD.md"
    if write_board:
        board.write_text(
            _board_fixture(
                placement_tool,
                open_rows=open_rows,
                closed_rows=closed_rows,
                narrative=narrative,
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(placement_tool, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(placement_tool, "TICKETS", tickets)
    monkeypatch.setattr(placement_tool, "BOARD", board)
    monkeypatch.setattr(sys, "argv", ["check_ticket_placement.py", "--quiet"])
    return placement_tool.main()


def test_valid_bug_register_bijection_passes(placement_tool, monkeypatch, tmp_path):
    assert (
        _run_fixture(
            placement_tool,
            monkeypatch,
            tmp_path,
            open_rows=["BUG-001"],
            closed_rows=["BUG-002"],
        )
        == 0
    )


def test_missing_board_is_rejected(placement_tool, monkeypatch, tmp_path, capsys):
    result = _run_fixture(
        placement_tool,
        monkeypatch,
        tmp_path,
        open_rows=["BUG-001"],
        closed_rows=["BUG-002"],
        write_board=False,
    )

    assert result == 1
    assert "tickets/BOARD.md is missing, unreadable, or empty" in capsys.readouterr().out


def test_heading_phrase_in_narrative_does_not_truncate_register(
    placement_tool, monkeypatch, tmp_path
):
    assert (
        _run_fixture(
            placement_tool,
            monkeypatch,
            tmp_path,
            open_rows=["BUG-001"],
            closed_rows=["BUG-002"],
            narrative="Historical prose mentions `## Open story tickets` inline.",
        )
        == 0
    )


def test_duplicate_register_heading_is_rejected(placement_tool, monkeypatch, tmp_path, capsys):
    result = _run_fixture(
        placement_tool,
        monkeypatch,
        tmp_path,
        open_rows=["BUG-001"],
        closed_rows=["BUG-002"],
        narrative="## Open bugs — duplicate",
    )

    assert result == 1
    assert "BOARD.md must contain exactly one bounded Open bugs register" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("open_rows", "closed_rows", "narrative", "expected_message"),
    [
        (
            ["BUG-001", "BUG-002"],
            ["BUG-002"],
            "",
            "BUG-002 is in BUGS/fixed/ but has 1 row in the board's Open bugs register",
        ),
        (
            [],
            ["BUG-002"],
            "Historical note mentioning BUG-001 outside the table.",
            "BUG-001 is in BUGS/open/ but has 0 rows in the board's Open bugs register",
        ),
        (
            ["BUG-001", "BUG-001"],
            ["BUG-002"],
            "",
            "BUG-001 has 2 rows in the board's Open bugs register",
        ),
        (
            ["BUG-001"],
            ["BUG-002", "BUG-002"],
            "",
            "BUG-002 has 2 rows in the board's CLOSED BUGS register",
        ),
    ],
    ids=("fixed-row-left-open", "open-row-missing", "duplicate-open", "duplicate-closed"),
)
def test_malformed_bug_register_is_rejected(
    placement_tool,
    monkeypatch,
    tmp_path,
    capsys,
    open_rows,
    closed_rows,
    narrative,
    expected_message,
):
    result = _run_fixture(
        placement_tool,
        monkeypatch,
        tmp_path,
        open_rows=open_rows,
        closed_rows=closed_rows,
        narrative=narrative,
    )

    assert result == 1
    assert expected_message in capsys.readouterr().out
