"""Gates a staged native-compute helper must pass before it is deployed.

The helper is its own deployment: it installs only compute-helper/requirements.txt
and bundles api/ plus backend/app. Two things have broken it in production
without any backend test noticing:

1. A backend-only import on its load path, so every call failed at import.
2. Code that loads but fails on the request path.

Gate 1 loads the staged entrypoint in an interpreter that refuses imports
outside the standard library, the helper's pins and `app` (the same probe as
backend/tests/test_helper_imports.py). Gate 2 serves the staged handler in an
interpreter that has ONLY the helper's pins installed, and the API's own client
(`extract_remote`, from this checkout) sends it three signed jobs (a PDF, a
DOCX and a PDF with an image table) through shared local storage. Each must be
accepted as the API accepts one in production: a signed receipt for that job,
then a manifest and an extraction that read back and match.

Run from backend/ (`uv run python ../compute-helper/gate/check_helper.py ...`)
so the API's code is importable:

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
        [sys.executable, "-I", "-B", "-c", probe.LOAD_HELPER, str(staged)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    last = (run.stderr.strip().splitlines() or [""])[-1]
    return run.returncode == 0 and "helper loaded: handler" in run.stdout, last


def signed_job_gate(staged: Path, pins_python: str, out: Path) -> tuple[bool, str]:
    """The API's own client sends each job to the staged helper and accepts
    the result only as it would in production: signed receipt, matching job,
    then the manifest and extraction read back and checked from storage."""
    import os
    import shutil

    # The API's code, from this checkout (the backend's environment has its
    # dependencies); the helper's code runs from the staged tree instead.
    sys.path.insert(0, str(REPO / "backend"))
    from app.config import Settings
    from app.ingest.compute_client import extract_remote

    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    secret = "gate-" + "x" * 43
    env = {
        **os.environ,
        "DATA_DIR": str(data),
        "STORAGE_BACKEND": "local",
        "NATIVE_COMPUTE_SECRET": secret,
    }
    helper = subprocess.Popen(
        [pins_python, "-I", "-B", str(HERE / "e2e_helper.py"), str(staged)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    lines = []
    try:
        first = helper.stdout.readline().strip()
        lines.append(first)
        if not first.startswith("listening "):
            helper.stdin.close()
            lines.append(helper.stdout.read())
            return False, "\n".join(lines)
        settings = Settings(
            _env_file=None,
            data_dir=data,
            storage_backend="local",
            native_compute_url=first.removeprefix("listening "),
            native_compute_secret=secret,
        )
        passed = True
        for name in FIXTURES:
            source = data / "uploads" / name
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE_DIR / name, source)
            try:
                result = extract_remote(str(source), settings, owner_id=1)
                ok = bool(result.blocks)  # a DOCX has no pages, only blocks
                lines.append(
                    f"{name}: accepted; {result.page_count} pages, {len(result.blocks)} blocks"
                )
            except Exception as exc:  # the gate reports any refusal, whatever its kind
                ok = False
                lines.append(f"{name}: refused: {type(exc).__name__}: {exc}")
            passed = passed and ok
    finally:
        if helper.poll() is None:
            helper.stdin.close()
            try:
                helper.wait(timeout=30)
            except subprocess.TimeoutExpired:
                helper.kill()
        lines.append(helper.stdout.read().strip())
    log = "\n".join(line for line in lines if line)
    return passed and "Traceback" not in log, log


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
        f"PASS ({len(FIXTURES)} of {len(FIXTURES)} signed jobs accepted by the API's own client)"
        if ok
        else "FAIL",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
