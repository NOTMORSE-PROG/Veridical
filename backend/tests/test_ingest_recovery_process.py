"""F1 worker lifecycle controls; use only disposable scripts and processes."""

import ctypes
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import psutil
import pytest

from app.config import Settings
from app.ingest import recovery, recovery_process


@pytest.mark.parametrize("limit", ["time", "memory", "output"])
def test_limits_terminate_started_helpers(monkeypatch, tmp_path, limit):
    sentinel = tmp_path / "helper.pid"
    helper = (
        "import os,pathlib,time; "
        f"pathlib.Path({str(sentinel)!r}).write_text(str(os.getpid())); time.sleep(15)"
    )
    script = tmp_path / "recovery_worker.py"
    script.write_text(
        "import pathlib,subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-I','-c',{helper!r}])\n"
        f"sentinel=pathlib.Path({str(sentinel)!r})\n"
        "deadline=time.monotonic()+2\n"
        "while not sentinel.is_file() and time.monotonic()<deadline: time.sleep(.01)\n"
        "assert sentinel.is_file()\n"
        + ("allocation=bytearray(128*1024**2)\n" if limit == "memory" else "")
        + (
            "pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text('x'*2000)\n"
            if limit == "output"
            else ""
        )
        + "time.sleep(15)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(recovery, "__file__", str(tmp_path / "recovery.py"))
    child = None
    try:
        reply, reason, _, _ = recovery._run_worker(
            {"max_output_bytes": 1000},
            tmp_path,
            Settings(
                _env_file=None,
                ingest_recovery_python=Path(sys.executable),
                ingest_recovery_timeout_seconds=1 if limit == "time" else 4,
                ingest_recovery_memory_mb=128 if limit == "memory" else 512,
                ingest_recovery_poll_seconds=0.01,
            ),
        )
        assert reply is None
        assert reason == f"worker_{limit}_limit"
        assert sentinel.is_file(), "The helper must start before its limit is reached"
        with suppress(psutil.NoSuchProcess):
            child = psutil.Process(int(sentinel.read_text()))
            assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        if child is not None:
            with suppress(psutil.NoSuchProcess):
                child.kill()
                psutil.wait_procs([child], timeout=2)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object bootstrap")
def test_missing_job_cannot_execute_worker(tmp_path):
    sentinel = tmp_path / "executed"
    script = tmp_path / "worker.py"
    script.write_text(
        f"from pathlib import Path; Path({str(sentinel)!r}).touch()", encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            recovery_process.__file__,
            "--job-object",
            f"Local\\missing-{tmp_path.name}",
            "--worker",
            str(script),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert not sentinel.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object API")
def test_failed_assignment_closes_handle_and_does_not_run_worker(monkeypatch):
    events = []

    class Api:
        def OpenJobObjectW(self, access, inherit, name):
            events.append(("open", access, inherit))
            return 7

        def GetCurrentProcess(self):
            return 8

        def AssignProcessToJobObject(self, job, process):
            events.append(("assign", job, process))
            ctypes.set_last_error(5)
            return False

        def CloseHandle(self, handle):
            events.append(("close", handle))
            return True

    monkeypatch.setattr(recovery_process, "_windows_api", Api)
    monkeypatch.setattr(sys, "argv", ["bootstrap", "--job-object", "job", "--worker", "script"])
    monkeypatch.setattr(recovery_process.runpy, "run_path", lambda *a, **k: pytest.fail("executed"))
    with pytest.raises(OSError):
        recovery_process.main()
    assert events == [("open", 1, False), ("assign", 7, 8), ("close", 7)]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object ownership")
@pytest.mark.parametrize("failure", ["launch", "body", "terminate", "wait"])
def test_failure_paths_release_job_and_reap_launcher(monkeypatch, tmp_path, failure):
    events = []

    class Job:
        name = "test-job"

        def terminate(self, timeout, poll):
            events.append("terminate")
            if failure == "terminate":
                raise OSError("query failed")

        def close(self):
            events.append("close")

    class Process:
        def poll(self):
            return None

        def kill(self):
            events.append("kill")

        def wait(self, timeout):
            events.append("wait")
            if failure == "wait":
                raise subprocess.TimeoutExpired("worker", timeout)

    def launch(*args, **kwargs):
        events.append("launch")
        if failure == "launch":
            raise OSError("launch failed")
        return Process()

    monkeypatch.setattr(recovery_process, "_WindowsJob", Job)
    monkeypatch.setattr(recovery_process.subprocess, "Popen", launch)
    with (
        pytest.raises(OSError),
        recovery_process.managed_worker(
            Path(sys.executable),
            tmp_path / "worker.py",
            [],
            directory=tmp_path,
            environment={},
            cleanup_timeout=1,
            poll=0.01,
        ),
    ):
        if failure == "body":
            raise OSError("body failed")
    assert events == (
        ["launch", "terminate", "close"]
        if failure == "launch"
        else ["launch", "terminate", "kill", "wait", "close"]
    )
