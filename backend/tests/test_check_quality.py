"""The local quality gate must not hide an incomplete backend test run."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def gate():
    path = Path(__file__).resolve().parents[2] / "tools" / "check_quality.py"
    spec = importlib.util.spec_from_file_location("check_quality", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_database_cannot_report_quality_gate_passed(gate, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(gate, "changed_paths", lambda: {"backend/tests/test_check_quality.py"})
    monkeypatch.setattr(gate.shutil, "which", lambda command: command)

    def completed(command, **kwargs):
        output = (
            "================ INCOMPLETE VERIFICATION ================\n"
            "615 tests were SKIPPED. This run did NOT verify the suite.\n"
            if "pytest" in command
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(gate.subprocess, "run", completed)

    assert gate.main() == 0
    stderr = capsys.readouterr().err
    assert "Quality gate passed." not in stderr
    assert "partial run, database-backed tests skipped" in stderr
    assert "The backend suite was not run" in stderr


def test_ci_pytest_environment_is_derived_and_keeps_local_database(gate, monkeypatch):
    ci_env = gate.ci_pytest_env()
    assert {"DATABASE_URL", "VERIDICAL_FAKE_LLM", "VERIDICAL_MAX_SKIPS"} <= ci_env.keys()

    monkeypatch.setenv("DATABASE_URL", "postgresql://local:secret@localhost:55433/local")
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "0")
    monkeypatch.setenv("VERIDICAL_MAX_SKIPS", "42")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_check_quality")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setattr(
        gate,
        "ci_pytest_env",
        lambda: {**ci_env, "EXTRA_CI_TEST_SETTING": "included"},
    )
    child_env = gate.pytest_environment()

    assert child_env["DATABASE_URL"] == "postgresql://local:secret@localhost:55433/local"
    assert child_env["VERIDICAL_FAKE_LLM"] == ci_env["VERIDICAL_FAKE_LLM"] == "1"
    assert child_env["VERIDICAL_MAX_SKIPS"] == ci_env["VERIDICAL_MAX_SKIPS"]
    assert child_env["EXTRA_CI_TEST_SETTING"] == "included"
    assert "PYTEST_ADDOPTS" not in child_env
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in child_env


@pytest.mark.parametrize("workflow", ["jobs:\n", "      - name: Tests (pytest)\n        env:\n"])
def test_ci_pytest_environment_fails_closed_when_step_changes(gate, monkeypatch, workflow):
    monkeypatch.setattr(
        gate,
        "CI_WORKFLOW",
        SimpleNamespace(read_text=lambda **kwargs: workflow),
    )
    with pytest.raises(ValueError, match="CI pytest"):
        gate.ci_pytest_env()


@pytest.mark.parametrize(
    "parent_env",
    [
        "env:\n  ANYTHING: 1\n",
        "  backend:\n    env:\n      ANYTHING: 1\n",
        "env: # shared test settings\n  ANYTHING: 1\n",
        "  backend:\n    'env': {ANYTHING: 1}\n",
    ],
)
def test_ci_pytest_environment_rejects_unmirrored_parent_env(gate, monkeypatch, parent_env):
    workflow = gate.CI_WORKFLOW.read_text(encoding="utf-8")
    monkeypatch.setattr(
        gate,
        "CI_WORKFLOW",
        SimpleNamespace(read_text=lambda **kwargs: parent_env + workflow),
    )
    with pytest.raises(ValueError, match="parent-level env"):
        gate.ci_pytest_env()


def test_database_failure_blocks_gate(gate, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://local:secret@localhost:1/local")
    monkeypatch.setattr(gate, "changed_paths", lambda: {"backend/tests/test_check_quality.py"})
    monkeypatch.setattr(gate.shutil, "which", lambda command: command)

    def completed(command, **kwargs):
        is_pytest = "../tools/run_backend_acceptance.py" in command
        return subprocess.CompletedProcess(
            command,
            1 if is_pytest else 0,
            stdout="ConnectionRefusedError" if is_pytest else "",
            stderr="",
        )

    monkeypatch.setattr(gate.subprocess, "run", completed)

    assert gate.main() == 1
    stderr = capsys.readouterr().err
    assert "Database connection failed; check DATABASE_URL" in stderr
    assert "PUSH BLOCKED" in stderr
    assert "Quality gate passed." not in stderr


def test_missing_database_is_partial_even_if_skip_banner_is_absent(gate, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(gate.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="1 passed in 0.01s", stderr=""
        ),
    )

    assert (
        gate.run("pytest", ["uv", "run", "pytest", "-q"], gate.BACKEND) is gate.CheckResult.PARTIAL
    )
    stderr = capsys.readouterr().err
    assert "partial run, database-backed tests skipped" in stderr
    assert "DATABASE_URL is not set" in stderr


def test_database_configured_with_incomplete_banner_is_still_partial(gate, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://local:secret@localhost:55433/local")
    monkeypatch.setattr(gate.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="INCOMPLETE VERIFICATION\n11 skipped", stderr=""
        ),
    )

    assert (
        gate.run("pytest", ["uv", "run", "pytest", "-q"], gate.BACKEND) is gate.CheckResult.PARTIAL
    )
    stderr = capsys.readouterr().err
    assert "pytest reported too many skipped tests" in stderr
    assert "INCOMPLETE VERIFICATION" in stderr


def test_pytest_deselection_cannot_be_reported_as_pass(gate, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://local:secret@localhost:55433/local")
    monkeypatch.setattr(gate.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="8 passed, 1634 deselected in 0.04s", stderr=""
        ),
    )

    assert (
        gate.run("pytest", ["uv", "run", "pytest", "-q"], gate.BACKEND) is gate.CheckResult.PARTIAL
    )
    stderr = capsys.readouterr().err
    assert "pytest deselected tests" in stderr
    assert "ok" not in stderr


def test_emergency_skip_remains_explicit(gate, monkeypatch, capsys):
    monkeypatch.setenv("VERIDICAL_SKIP_QUALITY", "1")
    monkeypatch.setattr(gate, "changed_paths", lambda: pytest.fail("gate should not run"))

    assert gate.main() == 0
    stderr = capsys.readouterr().err
    assert "quality gate skipped" in stderr
    assert "STATE.md" in stderr


def test_ci_skip_floor_still_exits_nonzero(monkeypatch):
    from tests import conftest

    class Reporter:
        stats = {"skipped": [object()] * (conftest._SKIP_FLOOR + 1)}

        def write_sep(self, *args, **kwargs):
            pass

        def write_line(self, *args, **kwargs):
            pass

    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://local:secret@localhost:55433/local")
    with pytest.raises(pytest.exit.Exception) as raised:
        conftest.pytest_terminal_summary(Reporter(), 0, None)
    assert raised.value.returncode == 1


def _ci_runs_by_job() -> dict[str, list[str]]:
    """CI's `run:` commands per job, in order, by line scan.

    Deliberately not PyYAML: the pre-push hook runs under system Python and
    `tools/check_quality.py` refuses that dependency for the same reason.
    A test that needed a package the tool cannot have would be guarding a
    different program than the one that ships.
    """
    path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    runs: dict[str, list[str]] = {}
    job: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if len(line) - len(line.lstrip()) == 2 and stripped.endswith(":") and " " not in stripped:
            job = stripped[:-1]
            runs[job] = []
        elif job and stripped.startswith("run: "):
            runs[job].append(stripped[len("run: ") :].strip())
    return runs


def test_the_partial_marker_still_matches_conftest(gate):
    """BUG-252's partial detection is a string match against conftest's banner.

    That coupling is the right design -- conftest decides what counts as an
    unverified run, the gate only reports it -- but nothing tied the two
    together, so rewording the banner would silently restore the original
    defect with every test still green.
    """
    conftest = Path(__file__).resolve().parent / "conftest.py"
    assert gate.INCOMPLETE_MARKER in conftest.read_text(encoding="utf-8"), (
        f"tools/check_quality.py looks for {gate.INCOMPLETE_MARKER!r} in pytest's "
        "output, and backend/tests/conftest.py no longer emits it. The gate is "
        "now blind to a partial run. Change both together."
    )


def test_gate_backend_commands_match_ci(gate):
    """AC2 applied to the commands, not just the environment.

    AC2's own argument -- "fixing only today's two variables re-creates the
    same defect on the next CI change" -- applies verbatim to the command
    list, which is still restated by hand in BACKEND_CHECKS.
    """
    setup = {"uv sync --frozen", "npm ci"}
    ci = [cmd for cmd in _ci_runs_by_job()["backend"] if cmd not in setup]
    gate_cmds = [" ".join(cmd) for _, cmd, _ in gate.BACKEND_CHECKS]

    assert gate_cmds == ci, (
        "tools/check_quality.py's BACKEND_CHECKS no longer mirror ci.yml's "
        f"backend commands.\n  gate: {gate_cmds}\n  ci:   {ci}"
    )


def test_gate_frontend_commands_match_ci(gate):
    setup = {"uv sync --frozen", "npm ci"}
    ci = [cmd for cmd in _ci_runs_by_job()["frontend"] if cmd not in setup]
    gate_cmds = [" ".join(cmd) for _, cmd, _ in gate.FRONTEND_CHECKS]
    assert gate_cmds == ci, (
        "tools/check_quality.py's FRONTEND_CHECKS no longer mirror ci.yml's "
        f"frontend commands.\n  gate: {gate_cmds}\n  ci:   {ci}"
    )


def test_a_new_ci_setting_is_refused_rather_than_inherited(gate, monkeypatch, tmp_path):
    """A new CI variable may be machine-specific the way DATABASE_URL is.

    `pytest_environment` applies every CI value except DATABASE_URL verbatim,
    so inheriting an unreviewed one would run the local suite against a
    CI-only address. An addition must be routed through the gate deliberately.
    """
    original = gate.CI_WORKFLOW.read_text(encoding="utf-8")
    patched = original.replace(
        '          VERIDICAL_FAKE_LLM: "1"',
        '          VERIDICAL_FAKE_LLM: "1"\n          REDIS_URL: redis://localhost:6379',
        1,
    )
    assert patched != original, "fixture precondition: the anchor line must exist"
    workflow = tmp_path / "ci.yml"
    workflow.write_text(patched, encoding="utf-8")
    monkeypatch.setattr(gate, "CI_WORKFLOW", workflow)

    with pytest.raises(ValueError, match="cannot resolve locally"):
        gate.ci_pytest_env()


def test_changed_acceptance_command_is_rejected_before_running(gate, monkeypatch):
    workflow = gate.CI_WORKFLOW.read_text(encoding="utf-8").replace(
        "run_backend_acceptance.py --output-dir",
        "run_backend_acceptance.py --registry v1 --output-dir",
    )
    monkeypatch.setattr(gate, "CI_WORKFLOW", SimpleNamespace(read_text=lambda **kwargs: workflow))
    with pytest.raises(ValueError, match="acceptance command changed"):
        gate.ci_pytest_env()


def test_deploy_environment_does_not_change_backend_test_controls(gate):
    assert gate.ci_pytest_env()["VERIDICAL_FAKE_LLM"] == "1"


@pytest.mark.parametrize("diagnostics", ["", "Trailing child diagnostic\n"])
def test_acceptance_receipts_keep_the_default_failure_visible(
    gate, monkeypatch, capsys, tmp_path, diagnostics
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://local:secret@localhost:55433/local")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("PIPELINE_WORKER_AUTOSTART", "true")
    monkeypatch.setattr(gate.shutil, "which", lambda command: command)
    monkeypatch.setattr(gate.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    summary = "Default pytest: FAIL; backend acceptance: PASS WITH RETAINED FAILED DIAGNOSTIC"

    def completed(command, **kwargs):
        assert command[:-1] == gate.BACKEND_CHECKS[-1][1][:-1]
        assert command[-1] == str(tmp_path)
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert kwargs["env"]["CI"] == "true"
        assert "GEMINI_API_KEY" not in kwargs["env"]
        assert kwargs["env"]["PIPELINE_WORKER_AUTOSTART"] == "false"
        return subprocess.CompletedProcess(command, 0, stdout=summary, stderr=diagnostics)

    monkeypatch.setattr(gate.subprocess, "run", completed)
    assert gate.run(*gate.BACKEND_CHECKS[-1]) is gate.CheckResult.PASSED
    assert summary in capsys.readouterr().err
    assert (tmp_path / "output.log").read_text(encoding="utf-8") == summary + diagnostics


def test_missing_acceptance_summary_is_not_reported_as_passed(gate, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://local:secret@localhost:55433/local")
    monkeypatch.setattr(gate.shutil, "which", lambda command: command)
    monkeypatch.setattr(gate.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    assert gate.run(*gate.BACKEND_CHECKS[-1]) is gate.CheckResult.FAILED
    assert "acceptance summary is missing" in capsys.readouterr().err
