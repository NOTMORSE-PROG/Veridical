#!/usr/bin/env python3
"""Pre-push quality gate: run CI's own checks BEFORE the push leaves the machine.

Why this exists (2026-07-25): `ruff format --check` failed on eight
consecutive pushes to main (V-018 through V-026). Because it runs before
pytest in the workflow, the test step was SKIPPED every time — nine commits
landed on main with the test suite never having executed in CI, while
ticket after ticket recorded "CI green" from local runs only.

The lesson: local-only verification is not verification. This hook runs the
same commands CI runs, so a push that would go red never leaves the laptop.

Wire it up once per clone:
    cp tools/pre-push .git/hooks/pre-push     (POSIX)
    copy tools\\pre-push .git\\hooks\\pre-push  (Windows)

Checks (mirrors .github/workflows/ci.yml):
  backend  : ruff check · ruff format --check · pytest
  frontend : oxlint · vitest · tsc+vite build · token purge guard
Only the side(s) whose files changed are checked, so a docs-only push is fast.

Escape hatch: VERIDICAL_SKIP_QUALITY=1 git push  — for genuine emergencies
only, and it must be confessed in STATE.md (PLAYBOOK §8).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
FRONTEND = REPO_ROOT / "frontend"

# (label, command, working directory) — mirrors .github/workflows/ci.yml.
BACKEND_CHECKS = [
    ("ruff check", ["uv", "run", "ruff", "check", "."], BACKEND),
    ("ruff format --check", ["uv", "run", "ruff", "format", "--check", "."], BACKEND),
    ("pytest", ["uv", "run", "pytest", "-q"], BACKEND),
]
FRONTEND_CHECKS = [
    ("oxlint", ["npm", "run", "lint"], FRONTEND),
    ("vitest", ["npm", "test"], FRONTEND),
    ("build (tsc + vite)", ["npm", "run", "build"], FRONTEND),
    ("token purge guard", ["npm", "run", "check:css"], FRONTEND),
]


def changed_paths() -> set[str]:
    """Everything that differs from origin/main, committed or not.

    Uncommitted work counts: the checks read the working tree, so a dirty
    file decides which suites are relevant even before it is committed.
    """
    paths: set[str] = set()
    commands = [
        # Committed but unpushed (falls back to the last commit on a fresh clone).
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        ["git", "diff", "--name-only", "HEAD~1..HEAD"],
        # Unstaged and staged working-tree changes.
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        # Untracked files (a brand-new module still needs its suite run).
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    for cmd in commands:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
        if result.returncode == 0:
            paths.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return paths


def run(label: str, cmd: list[str], cwd: Path) -> bool:
    # npm ships as npm.cmd on Windows; resolve so subprocess can find it.
    exe = shutil.which(cmd[0])
    if exe is None:
        sys.stderr.write(f"  ? {label}: '{cmd[0]}' not found on PATH — cannot verify\n")
        return False
    sys.stderr.write(f"  · {label} ... ")
    sys.stderr.flush()
    result = subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True, text=True)
    if result.returncode == 0:
        sys.stderr.write("ok\n")
        return True
    sys.stderr.write("FAILED\n")
    tail = (result.stdout + result.stderr).strip().splitlines()[-25:]
    sys.stderr.write("\n".join(f"      {line}" for line in tail) + "\n")
    return False


def main() -> int:
    if os.environ.get("VERIDICAL_SKIP_QUALITY") == "1":
        sys.stderr.write(
            "\nWARNING: quality gate skipped (VERIDICAL_SKIP_QUALITY=1).\n"
            "Record why in context/STATE.md — PLAYBOOK §8.\n\n"
        )
        return 0

    paths = [p.replace("\\", "/") for p in changed_paths()]
    checks: list[tuple[str, list[str], Path]] = []
    if any(p.startswith("backend/") for p in paths):
        checks += BACKEND_CHECKS
    if any(p.startswith("frontend/") for p in paths):
        checks += FRONTEND_CHECKS

    if not checks:
        return 0

    sys.stderr.write("\nQuality gate (same checks CI runs):\n")
    failures = [label for label, cmd, cwd in checks if not run(label, cmd, cwd)]

    if failures:
        sys.stderr.write(
            "\nPUSH BLOCKED — these would fail CI:\n"
            + "".join(f"  - {name}\n" for name in failures)
            + "\nFix them, then push again. Formatting is auto-fixable:\n"
            "  cd backend && uv run ruff format .\n"
            "Emergency override (confess in STATE.md):\n"
            "  VERIDICAL_SKIP_QUALITY=1 git push\n\n"
        )
        return 1

    sys.stderr.write("Quality gate passed.\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
