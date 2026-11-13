"""Explicit actual POSIX service restart/recovery checks; no live accounts.

Run: pytest -o addopts='' tests/studio_server_recovery_acceptance.py -q
Run inside the documented isolated Linux image for Linux-specific evidence.
"""

import json
import os
import runpy
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("sql", [False, True], ids=["files", "sqlite"])
def test_real_server_restart_and_backup_restore(sql):
    example = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_server_recovery.py"
        )
    )
    report = example["exercise"](sql=sql)
    assert report == {
        "backend": "sqlite" if sql else "files",
        "server_launches": 4,
        "sigterm_cleanup": True,
        "sigkill_restart": True,
        "exclusive_writer": True,
        "inactive_restore": True,
        "artifact_revisions_and_citations": True,
        "approval_retained": True,
        "extra_runs_created": 0,
        "external_account_calls": 0,
    }


PROCESS = r"""
import pathlib,runpy,sys,time
from nexus.core.studio import StudioService
marker=pathlib.Path(sys.argv[1])
original=StudioService.close
def close(self):
    marker.write_text('closing')
    time.sleep(.3)
    original(self)
    marker.write_text('closed')
StudioService.close=close
sys.argv=['nexus','studio',*sys.argv[2:]]
runpy.run_module('nexus.cli',run_name='__main__')
"""


@pytest.mark.parametrize("worker_only", [False, True], ids=["controller", "worker"])
def test_sigterm_calls_real_service_cleanup_even_with_a_second_stop(tmp_path, worker_only):
    assert os.name == "posix", "This explicit process-signal check requires POSIX"
    root, runtime = tmp_path / "project", tmp_path / "runtime.json"
    marker, log = tmp_path / "closed.txt", tmp_path / "process.log"
    runtime.write_text(
        json.dumps(
            {
                "state": {
                    "url_env": "NEXUS_RECOVERY_STATE_URL",
                    "namespace": "cleanup",
                    "workers": 1,
                }
            }
        )
    )
    runtime.chmod(0o600)
    args = ["--dir", str(root), "--port", "0", "--runtime-config", str(runtime)]
    if worker_only:
        args.append("--worker-only")
    with log.open("w") as output:
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", PROCESS, str(marker), *args],
            cwd=Path(__file__).resolve().parents[1],
            stdout=output,
            stderr=output,
            env={
                **os.environ,
                "NEXUS_RECOVERY_STATE_URL": "sqlite:///" + str(tmp_path / "state.sqlite"),
            },
        )
        try:
            deadline = time.monotonic() + 20
            prefix = "Nexus Studio worker:" if worker_only else "Nexus Studio:"
            while prefix not in log.read_text():
                assert child.poll() is None, log.read_text()
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(0.02)
            child.send_signal(signal.SIGTERM)
            while not marker.exists():
                assert child.poll() is None, log.read_text()
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(0.01)
            child.send_signal(signal.SIGTERM)
            assert child.wait(timeout=15) == 0, log.read_text()
            assert marker.read_text() == "closed"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
