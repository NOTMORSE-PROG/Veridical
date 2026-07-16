#!/usr/bin/env python3
"""Pre-commit audit: code changes require a changelog entry.

VERIDICAL keeps its changelog in context/CHANGELOG.md, which is
LOCAL-ONLY (gitignored) by project policy. That means the cashflow-style
"changelog entry in the same commit" rule can't work here; instead this
hook verifies, at commit time, that the local changelog has an entry
dated today whenever the staged changes touch code paths.

Wire it up once per clone:
    git config commit.template .gitmessage
    copy tools\\pre-commit .git\\hooks\\pre-commit   (Windows)
    cp tools/pre-commit .git/hooks/pre-commit        (POSIX)

Exit 0 = commit allowed, exit 1 = commit blocked with an explanation.
"""

import datetime
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "context" / "CHANGELOG.md"

# Paths whose staged changes demand a changelog entry.
ENFORCED_PREFIXES = ("backend/", "frontend/", "src/", "tools/")
ENFORCED_FILES = ("pyproject.toml", "package.json", "uv.lock", "run.py")


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def touches_enforced(paths: list[str]) -> bool:
    for p in paths:
        norm = p.replace("\\", "/")
        if norm.startswith(ENFORCED_PREFIXES) or norm in ENFORCED_FILES:
            return True
    return False


def changelog_has_entry_for_today() -> bool:
    if not CHANGELOG.exists():
        return False
    today = datetime.date.today().isoformat()
    head = CHANGELOG.read_text(encoding="utf-8", errors="replace")[:4000]
    # Entries look like: ## 2026-07-12 · V-0xx · Short imperative title
    return bool(re.search(rf"^##\s+{re.escape(today)}\b", head, re.MULTILINE))


def main() -> int:
    paths = staged_files()
    if not touches_enforced(paths):
        return 0
    if changelog_has_entry_for_today():
        return 0
    sys.stderr.write(
        "\nCOMMIT BLOCKED by tools/check_changelog.py\n"
        "Staged changes touch code paths but context/CHANGELOG.md has no\n"
        "entry dated today. Add one at the TOP of the file:\n\n"
        f"  ## {datetime.date.today().isoformat()} · V-0xx · Short imperative title\n"
        "  What: <one or two lines>\n"
        "  Why: <one line>\n"
        "  Files: <main files touched>\n\n"
        "Then retry the commit. (Bypass only for emergencies: git commit --no-verify)\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
