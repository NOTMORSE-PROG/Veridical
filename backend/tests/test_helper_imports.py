"""The native-compute helper loads with only the packages it installs.

The helper is its own deployment and installs compute-helper/requirements.txt,
not the backend's full set. A backend module on its import path that pulls in
anything else (the database driver, say) makes every helper call fail at load,
and the backend's own tests never notice because their venv has everything.
So the helper's entrypoint is loaded in a fresh interpreter that refuses any
import outside the standard library, those requirements and `app`.

This covers what the helper imports when it loads, not imports made later
inside a function.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

LOAD_HELPER = r"""
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import re
import sys
import sysconfig
from pathlib import Path

repo = Path(sys.argv[1])
requirements = (repo / "compute-helper" / "requirements.txt").read_text(encoding="utf-8")


def project(text):
    found = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", text)
    return re.sub(r"[-_.]+", "-", found.group(1)).lower() if found else None


installed = set()
pending = [project(line.split("#", 1)[0]) for line in requirements.splitlines()]
while pending:
    name = pending.pop()
    if not name or name in installed:
        continue
    installed.add(name)
    for requirement in importlib.metadata.requires(name) or []:
        if "extra ==" not in requirement:
            pending.append(project(requirement))

allowed = set(sys.stdlib_module_names) | {"app"}
for module, distributions in importlib.metadata.packages_distributions().items():
    if any(project(d) in installed for d in distributions):
        allowed.add(module)

# Python ships a few modules the name list leaves out, such as the platform's
# generated _sysconfigdata module, so anything found in its own folders counts
# as standard library. Installed packages never live there.
paths = sysconfig.get_paths()
python_home = {paths["stdlib"], paths["platstdlib"]}
python_home |= {str(Path(p) / "lib-dynload") for p in list(python_home)}
python_home.add(str(Path(sys.base_prefix) / "DLLs"))
python_home = [p for p in python_home if Path(p).is_dir()]


class OnlyHelperPackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.partition(".")[0]
        if top in allowed:
            return None
        if importlib.machinery.PathFinder.find_spec(top, python_home) is not None:
            allowed.add(top)
            return None
        raise ModuleNotFoundError(f"the helper does not install {top!r}", name=top)


sys.meta_path.insert(0, OnlyHelperPackages())
spec = importlib.util.spec_from_file_location("native_extract", repo / "api" / "native_extract.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
print("helper loaded:", entry.handler.__name__)
"""


def test_the_helper_entrypoint_loads_with_only_the_helper_requirements():
    result = subprocess.run(
        [sys.executable, "-I", "-c", LOAD_HELPER, str(REPO)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert "helper loaded: handler" in result.stdout


def _pins(path):
    lines = (REPO / path).read_text(encoding="utf-8").splitlines()
    return sorted(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def test_the_root_requirements_list_the_helper_pins_directly():
    # A deployment built from the repository root reads requirements.txt, and
    # Vercel's builder no longer follows a "-r" include there.
    assert _pins("requirements.txt") == _pins("compute-helper/requirements.txt")
    assert not any(pin.startswith("-") for pin in _pins("requirements.txt"))


def test_the_helper_reports_the_commit_it_was_deployed_from(monkeypatch):
    import importlib.util
    import io
    import json

    spec = importlib.util.spec_from_file_location(
        "native_extract_commit", REPO / "api" / "native_extract.py"
    )
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)

    def get():
        handler = entry.handler.__new__(entry.handler)
        handler.wfile = io.BytesIO()
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET / HTTP/1.1"
        handler.command = "GET"
        handler.close_connection = True
        handler.do_GET()
        return json.loads(handler.wfile.getvalue().split(b"\r\n\r\n", 1)[1])

    for name in ("DEPLOYED_COMMIT", "RENDER_GIT_COMMIT", "VERCEL_GIT_COMMIT_SHA"):
        monkeypatch.delenv(name, raising=False)
    assert get() == {"service": "native-compute", "version": "native-v1", "commit": None}
    monkeypatch.setenv("DEPLOYED_COMMIT", "abc123")
    assert get()["commit"] == "abc123"
