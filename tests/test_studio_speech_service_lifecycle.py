"""Actual Studio speech queue/shutdown with synthetic local commands, no models."""

import importlib.util
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def consumer():
    path = ROOT / "examples/nexus-bot/src/studio_windows_speech_lifecycle.py"
    spec = importlib.util.spec_from_file_location("studio_windows_speech_consumer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def posix_worker(path):
    child = f"import os,time;from pathlib import Path;os.setsid();Path({str(path)!r}).write_text(str(os.getpid()));time.sleep(60)"
    return [
        sys.executable,
        "-c",
        "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"
        + repr(child)
        + "]);time.sleep(60)",
    ]


def observe_posix(path):
    deadline = time.monotonic() + 10
    while not path.exists() or not path.read_text():
        if time.monotonic() > deadline:
            raise AssertionError("Synthetic speech child never became ready")
        time.sleep(0.02)
    return (int(path.read_text()),)


def removed_posix(pid):
    def alive():
        if sys.platform == "linux":
            try:
                return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
            except FileNotFoundError:
                return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    deadline = time.monotonic() + 3
    try:
        while alive():
            if time.monotonic() >= deadline:
                raise AssertionError("Owned synthetic speech child survived")
            time.sleep(0.02)
    finally:
        if alive():
            os.kill(pid, signal.SIGKILL)


def test_actual_speech_jobs_cancel_shutdown_and_remove_queued_input(tmp_path):
    module = consumer()
    if sys.platform == "win32":
        report = module.service_cases(tmp_path)
    else:
        report = module.service_cases(
            tmp_path,
            command_factory=posix_worker,
            observe=observe_posix,
            confirm_removed=removed_posix,
        )
    assert set(report) == {"service_cancel", "service_shutdown", "queued_cancel"}
