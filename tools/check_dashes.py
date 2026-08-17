#!/usr/bin/env python3
"""Pre-commit gate: no em/en dashes in user-facing strings.

The project rule (tickets/README.md) is absolute: no em dash (U+2014) or
en dash (U+2013) in ANY user-facing text, including flag wording and
report copy. It had been enforced by hand with

    grep -rn '—\\|–' frontend/src/

and that check fails on two counts, both found on 2026-08-16:

  1. It only looks at frontend/src/. EVERY flag-wording string in this
     product is generated in backend/app/checks/. The gate structurally
     could not see the files it most needed to. Two dedicated sweeps ran
     clean and missed 37 real occurrences between them.
  2. It does not exclude comments or docstrings. Run against frontend/src/
     it returns 232 hits, all of them prose in comments. A gate with a
     100% false-positive rate is not a gate; it trains you to ignore it.

So this parses instead of grepping:

  * Python  -> ast, string literals only, docstrings excluded.
  * TS/TSX  -> line scan with comment stripping (no TS parser is available
               in a pre-commit hook here; the frontend has historically
               been clean, and the residual risk is a dash inside a string
               on a line that also opens a block comment).

Regex literals are exempt: an en dash inside a character class is
pattern-matching input, not output. See ALLOW_REGEX_FILES.

Usage:
    python tools/check_dashes.py            # whole repo
    python tools/check_dashes.py --staged   # staged files only (hook mode)

Exit 0 = clean, exit 1 = blocked with a file:line list.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

DASHES = ("—", "–")  # em, en

PY_ROOTS = ("backend/app", "backend/scripts")
TS_ROOTS = ("frontend/src",)

# Files where a dash inside a string is legitimately pattern-matching
# input rather than output. Keep this list SHORT and justify every entry.
ALLOW_REGEX_FILES = {
    # citation page-range patterns match en-dashed ranges ("pp. 12–14")
    "backend/app/checks/citations/extract.py",
}

# Paths whose strings never reach a user.
SKIP_PARTS = {"tests", "fixtures", "__pycache__", "node_modules", ".venv"}
# Colocated test files: a dash in a test NAME is not user-facing. (Two of
# these are regex literals inside a test that asserts there are no dashes.)
SKIP_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", "_test.py")


def _skip(path: pathlib.Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return True
    return path.name.endswith(SKIP_SUFFIXES)


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def scan_python(path: pathlib.Path) -> list[tuple[int, str]]:
    """String literals containing a dash, docstrings excluded."""
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return []

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and any(d in node.value for d in DASHES)
        ):
            excerpt = " ".join(node.value.split())[:90]
            hits.append((node.lineno, excerpt))
    return hits


_LINE_COMMENT = re.compile(r"//.*$")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def scan_ts(path: pathlib.Path) -> list[tuple[int, str]]:
    """Dash-bearing lines with comments stripped."""
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return []
    src = _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), src)

    hits: list[tuple[int, str]] = []
    for i, line in enumerate(src.splitlines(), start=1):
        stripped = _LINE_COMMENT.sub("", line)
        if any(d in stripped for d in DASHES):
            hits.append((i, stripped.strip()[:90]))
    return hits


def staged_files() -> set[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def main() -> int:
    # Windows consoles default to cp1252, which cannot encode the very
    # characters this gate exists to report. Without this the hook dies with
    # UnicodeEncodeError instead of printing its findings -- i.e. it would
    # fail exactly when it had something to say.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="only staged files")
    args = ap.parse_args()

    only = staged_files() if args.staged else None

    findings: list[str] = []

    for root in PY_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            rel = _rel(path)
            if _skip(path) or rel in ALLOW_REGEX_FILES:
                continue
            if only is not None and rel not in only:
                continue
            for lineno, excerpt in scan_python(path):
                findings.append(f"{rel}:{lineno}  {excerpt}")

    for root in TS_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for pattern in ("*.ts", "*.tsx"):
            for path in sorted(base.rglob(pattern)):
                rel = _rel(path)
                if _skip(path) or rel in ALLOW_REGEX_FILES:
                    continue
                if only is not None and rel not in only:
                    continue
                for lineno, excerpt in scan_ts(path):
                    findings.append(f"{rel}:{lineno}  {excerpt}")

    if not findings:
        return 0

    print("BLOCKED: em/en dash found in user-facing text.")
    print("Project rule (tickets/README.md): never, in any user-facing string.")
    print("Use a comma, a colon, or a full stop.\n")
    for line in findings:
        print("  " + line)
    print(
        f"\n{len(findings)} occurrence(s). If one is genuinely a regex literal, "
        "add its file to ALLOW_REGEX_FILES in tools/check_dashes.py with a reason."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
