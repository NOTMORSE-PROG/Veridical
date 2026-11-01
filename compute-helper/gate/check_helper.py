"""Gates a staged native-compute helper must pass before it is deployed.

The helper is its own deployment: it installs only compute-helper/requirements.txt
and bundles api/ plus backend/app. Two things have broken it in production
without any backend test noticing:

1. A backend-only import on its load path, so every call failed at import.
2. Code that loads but fails on the request path.

Gate 1 loads the staged entrypoint in an interpreter that refuses imports
outside the standard library, the helper's pins and `app` (the same probe as
backend/tests/test_helper_imports.py). Gate 2 serves the staged handler in an
interpreter that has ONLY the helper's pins installed and sends it three signed
jobs (a PDF, a DOCX and a PDF with an image table) through local storage. Each
must return 200 with a receipt and no error.

    python check_helper.py STAGED_DIR --pins-python PATH [--out DIR]

STAGED_DIR holds api/, backend/app and compute-helper/ exactly as they will be
deployed. --pins-python is a Python with only compute-helper/requirements.txt
installed. Exits non-zero, naming the gate, when either gate fails.
"""

import argparse
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# The backend's own committed ingest fixtures: synthetic documents.
FIXTURE_DIR = REPO / "backend" / "tests" / "fixtures" / "ingest"
FIXTURES = ("native.pdf", "native.docx", "image_table.pdf")


def import_gate(staged: Path) -> tuple[bool, str]:
    spec = importlib.util.spec_from_file_location(
        "helper_imports", REPO / "backend" / "tests" / "test_helper_imports.py"
    )
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    run = subprocess.run(
        [sys.executable, "-I", "-c", probe.LOAD_HELPER, str(staged)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    last = (run.stderr.strip().splitlines() or [""])[-1]
    return run.returncode == 0, last


def signed_job_gate(staged: Path, pins_python: str, out: Path) -> tuple[bool, str]:
    run = subprocess.run(
        [
            pins_python,
            "-I",
            str(HERE / "e2e_helper.py"),
            str(staged),
            str(out),
            *(str(FIXTURE_DIR / name) for name in FIXTURES),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    log = run.stdout + run.stderr
    ok_lines = [line for line in log.splitlines() if line.endswith(": HTTP 200; error=None")]
    passed = (
        run.returncode == 0
        and len(ok_lines) == len(FIXTURES)
        and "Traceback" not in log
        and "ModuleNotFoundError" not in log
    )
    return passed, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("staged", type=Path)
    parser.add_argument("--pins-python", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    staged = args.staged.resolve()

    ok, detail = import_gate(staged)
    print("import gate:", "PASS" if ok else f"FAIL {detail}")
    if not ok:
        return 1

    out = args.out or Path(tempfile.mkdtemp(prefix="helper-gate-"))
    ok, log = signed_job_gate(staged, args.pins_python, out)
    print(log.rstrip())
    print(
        "signed-job gate:",
        f"PASS ({len(FIXTURES)} of {len(FIXTURES)} signed jobs returned 200 with no error)"
        if ok
        else "FAIL",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
