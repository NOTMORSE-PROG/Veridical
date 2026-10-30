#!/usr/bin/env python3
"""Pre-push quality gate: run CI's own checks BEFORE the push leaves the machine.

Why this exists (2026-07-25): `ruff format --check` failed on eight
consecutive pushes to main (V-018 through V-026). Because it runs before
pytest in the workflow, the test step was SKIPPED every time — nine commits
landed on main with the test suite never having executed in CI, while
ticket after ticket recorded "CI green" from local runs only.

The lesson: local-only verification is not verification. This hook runs
CI-aligned checks and distinguishes a full pytest run from a partial one.
CI remains the authoritative check when local Postgres is unavailable.

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
import re
import shutil
import subprocess
import sys
import tempfile
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
FRONTEND = REPO_ROOT / "frontend"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
INCOMPLETE_MARKER = "INCOMPLETE VERIFICATION"

# Every setting ci.yml's pytest step is allowed to carry. The gate applies all
# of them except DATABASE_URL verbatim to the child, so an unrecognised one is
# refused rather than inherited (it may be a CI-only address).
KNOWN_CI_PYTEST_SETTINGS = frozenset({"VERIDICAL_FAKE_LLM", "VERIDICAL_MAX_SKIPS", "DATABASE_URL"})


class CheckResult(Enum):
    PASSED = "passed"
    PARTIAL = "partial"
    FAILED = "failed"


# (label, command, working directory) — mirrors .github/workflows/ci.yml.
BACKEND_CHECKS = [
    ("ruff check", ["uv", "run", "ruff", "check", "."], BACKEND),
    ("ruff format --check", ["uv", "run", "ruff", "format", "--check", "."], BACKEND),
    (
        "acceptance classifier controls",
        [
            "uv",
            "run",
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "../tools",
            "-p",
            "test_backend_acceptance.py",
        ],
        BACKEND,
    ),
    (
        "pytest",
        [
            "uv",
            "run",
            "python",
            "../tools/run_backend_acceptance.py",
            "--output-dir",
            "../test-results/backend",
        ],
        BACKEND,
    ),
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


def ci_pytest_env() -> dict[str, str]:
    """Read the pytest step's scalar env from CI; reject layout drift.

    The pre-push hook uses system Python and cannot assume a YAML package.
    This deliberately supports only the workflow's current simple env block.
    An unfamiliar shape is an error, never a silently different test run.
    """
    lines = CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
    backend_start = next((i for i, line in enumerate(lines) if line == "  backend:"), None)
    if backend_start is None:
        raise ValueError("CI pytest backend job not found")
    backend_end = next(
        (i for i in range(backend_start + 1, len(lines)) if re.match(r"^  \S", lines[i])),
        len(lines),
    )
    if any(re.match(r"^(?:env|['\"]env['\"])\s*:", line) for line in lines) or any(
        re.match(r"^ {4}(?:env|['\"]env['\"])\s*:", line)
        for line in lines[backend_start:backend_end]
    ):
        # Workflow/job env is inherited by the pytest step. We currently have
        # none; fail closed if it is introduced rather than silently omit it.
        raise ValueError("CI parent-level env needs explicit gate support")
    lines = lines[backend_start:backend_end]
    expected_run = "run: " + " ".join(BACKEND_CHECKS[-1][1])
    step_start = next(
        (index for index, line in enumerate(lines) if line.strip() == expected_run),
        None,
    )
    if step_start is None:
        raise ValueError("CI pytest acceptance command changed or was not found")

    env_start = next(
        (
            index
            for index in range(step_start + 1, len(lines))
            if lines[index].startswith("        env:")
            and not any(line.startswith("      - ") for line in lines[step_start + 1 : index])
        ),
        None,
    )
    if env_start is None:
        raise ValueError("CI pytest step has no scalar env block")

    values: dict[str, str] = {}
    for line in lines[env_start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = len(line) - len(line.lstrip())
        if indentation <= 8:
            break
        if indentation != 10 or ":" not in line:
            raise ValueError("CI pytest env has an unsupported YAML layout")
        key, _, raw_value = line.strip().partition(":")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or not raw_value.strip():
            raise ValueError("CI pytest env has an unsupported value")
        value = raw_value.strip()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"CI pytest env value for {key} has mismatched quotes")
            value = value[1:-1]
        if key != "DATABASE_URL" and ("${{" in value or not value):
            raise ValueError(f"CI pytest env value for {key} cannot be resolved locally")
        if key in values:
            raise ValueError(f"CI pytest env repeats {key}")
        values[key] = value

    # Exact, not a subset. A NEW CI variable may be machine-specific the way
    # DATABASE_URL is (a service-container address, a local port), and
    # `pytest_environment` applies everything except DATABASE_URL verbatim.
    # Inheriting an unreviewed one would silently run the local suite against
    # a CI-only address, so an addition must be taught to the gate
    # deliberately. Fail closed, like the parent-level env check above.
    if values.keys() != KNOWN_CI_PYTEST_SETTINGS:
        missing = sorted(KNOWN_CI_PYTEST_SETTINGS - values.keys())
        if missing:
            raise ValueError(f"CI pytest env is missing a required setting: {missing}")
        added = sorted(values.keys() - KNOWN_CI_PYTEST_SETTINGS)
        raise ValueError(
            f"CI pytest env has new settings the gate cannot resolve locally: {added}. "
            "Decide whether each is portable or machine-specific, then update "
            "the gate rather than inheriting CI's value."
        )
    if values["VERIDICAL_FAKE_LLM"] != "1":
        raise ValueError("CI pytest no longer uses fake LLM mode")
    if not values["VERIDICAL_MAX_SKIPS"].isdigit():
        raise ValueError("CI pytest skip floor is not numeric")
    return values


def pytest_environment() -> dict[str, str]:
    """Mirror CI's test controls while respecting the caller's local DSN."""
    child_env = os.environ.copy()
    ci_env = ci_pytest_env()
    for key in list(child_env):
        if key.startswith("PYTEST_") and key not in ci_env:
            # PYTEST_ADDOPTS can select a tiny subset of tests while yielding
            # exit zero. CI does not inherit the developer's pytest controls.
            del child_env[key]
    for key, value in ci_env.items():
        if key != "DATABASE_URL":
            child_env[key] = value
    child_env["CI"] = "true"
    child_env["PIPELINE_WORKER_AUTOSTART"] = "false"
    child_env.pop("GEMINI_API_KEY", None)
    # Caller overrides for fake mode or the skip floor would break CI parity.
    # DATABASE_URL is the sole exception: CI's localhost:5432 is runner-only.
    return child_env


def run(label: str, cmd: list[str], cwd: Path) -> CheckResult:
    # npm ships as npm.cmd on Windows; resolve so subprocess can find it.
    exe = shutil.which(cmd[0])
    if exe is None:
        sys.stderr.write(f"  ? {label}: '{cmd[0]}' not found on PATH — cannot verify\n")
        return CheckResult.FAILED
    sys.stderr.write(f"  · {label} ... ")
    sys.stderr.flush()
    try:
        env = pytest_environment() if label == "pytest" else None
    except (OSError, ValueError) as error:
        sys.stderr.write(f"FAILED (cannot align pytest environment with CI: {error})\n")
        return CheckResult.FAILED
    if label == "pytest" and not env.get("DATABASE_URL"):
        sys.stderr.write(
            "partial run, database-backed tests skipped (DATABASE_URL is not set)\n"
            "      The backend suite was not run; CI must verify it.\n"
        )
        return CheckResult.PARTIAL
    receipt_dir = None
    if label == "pytest" and "../tools/run_backend_acceptance.py" in cmd:
        # Keep every attempt, including failures; never overwrite an older receipt.
        receipt_dir = Path(tempfile.mkdtemp(prefix="veridical-quality-"))
        cmd = [*cmd[:-1], str(receipt_dir)]
    result = subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True, text=True, env=env)
    output = result.stdout + result.stderr
    if receipt_dir is not None:
        (receipt_dir / "output.log").write_text(output, encoding="utf-8")
        sys.stderr.write(f"receipts: {receipt_dir}\n      ")
    if result.returncode == 0:
        deselected = label == "pytest" and bool(re.search(r"\b\d+ deselected\b", output))
        if label == "pytest" and (
            INCOMPLETE_MARKER in output or not env.get("DATABASE_URL") or deselected
        ):
            if not env.get("DATABASE_URL"):
                sys.stderr.write(
                    "partial run, database-backed tests skipped (DATABASE_URL is not set)\n"
                )
            elif deselected:
                sys.stderr.write("partial run, pytest deselected tests; full suite not verified\n")
            else:
                sys.stderr.write("partial run, pytest reported too many skipped tests\n")
            if INCOMPLETE_MARKER in output:
                lines = output.splitlines()
                marker_line = next(
                    index for index, line in enumerate(lines) if INCOMPLETE_MARKER in line
                )
                sys.stderr.write("\n".join(f"      {line}" for line in lines[marker_line:]) + "\n")
            else:
                if env.get("DATABASE_URL"):
                    sys.stderr.write("      The full suite was not verified.\n")
                else:
                    sys.stderr.write("      The DB-backed suite was not verified.\n")
            return CheckResult.PARTIAL
        if receipt_dir is not None:
            summary = next(
                (
                    line
                    for line in reversed(result.stdout.splitlines())
                    if line.startswith("Default pytest: ")
                ),
                None,
            )
            if summary is None:
                sys.stderr.write("FAILED (backend acceptance summary is missing)\n")
                return CheckResult.FAILED
            sys.stderr.write(summary + "\n")
        elif label == "pytest" and output.strip():
            sys.stderr.write(f"ok ({output.strip().splitlines()[-1]})\n")
        else:
            sys.stderr.write("ok\n")
        return CheckResult.PASSED
    sys.stderr.write("FAILED\n")
    if (
        label == "pytest"
        and env.get("DATABASE_URL")
        and any(
            marker in output
            for marker in ("ConnectionRefusedError", "InvalidPasswordError", "could not connect")
        )
    ):
        sys.stderr.write(
            "      Database connection failed; check DATABASE_URL and the local Postgres service.\n"
        )
    tail = output.strip().splitlines()[-25:]
    sys.stderr.write("\n".join(f"      {line}" for line in tail) + "\n")
    return CheckResult.FAILED


def main() -> int:
    if os.environ.get("VERIDICAL_SKIP_QUALITY") == "1":
        sys.stderr.write(
            "\nWARNING: quality gate skipped (VERIDICAL_SKIP_QUALITY=1).\n"
            "Record why in context/STATE.md — PLAYBOOK §8.\n\n"
        )
        return 0

    paths = [p.replace("\\", "/") for p in changed_paths()]
    checks: list[tuple[str, list[str], Path]] = []
    backend_gate_paths = {"tools/check_quality.py", ".github/workflows/ci.yml"}
    if any(
        p.startswith(("backend/", "tools/data/backend_acceptance_"))
        or p in backend_gate_paths
        or p in {"tools/run_backend_acceptance.py", "tools/test_backend_acceptance.py"}
        for p in paths
    ):
        checks += BACKEND_CHECKS
    if any(p.startswith("frontend/") for p in paths):
        checks += FRONTEND_CHECKS

    if not checks:
        return 0

    sys.stderr.write("\nQuality gate (CI-aligned checks):\n")
    outcomes = [(label, run(label, cmd, cwd)) for label, cmd, cwd in checks]
    failures = [label for label, result in outcomes if result is CheckResult.FAILED]
    partials = [label for label, result in outcomes if result is CheckResult.PARTIAL]

    if failures:
        sys.stderr.write(
            "\nPUSH BLOCKED — checks failed locally; CI outcome is not established:\n"
            + "".join(f"  - {name}\n" for name in failures)
            + "\nFix them, then push again. Formatting is auto-fixable:\n"
            "  cd backend && uv run ruff format .\n"
            "Emergency override (confess in STATE.md):\n"
            "  VERIDICAL_SKIP_QUALITY=1 git push\n\n"
        )
        return 1

    if partials:
        sys.stderr.write(
            "Quality gate partial: "
            + ", ".join(partials)
            + " did not fully verify the suite. Push may continue; CI must run the full checks.\n\n"
        )
        return 0

    sys.stderr.write("Quality gate passed.\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
