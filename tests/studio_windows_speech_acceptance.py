"""Explicit native Windows speech acceptance with a portable result record.

This runner never treats another OS as a skip/pass. It runs the existing example
consumer against actual Windows processes, without models, accounts or installers.
"""

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = "examples/nexus-bot/src/studio_windows_speech_lifecycle.py"
SOURCES = (
    "nexus/core/studio_speech_windows.py",
    "nexus/core/studio_speech_process.py",
    "nexus/core/studio_speech.py",
    "nexus/core/studio.py",
    "nexus/core/studio_desktop_sidecar.py",
    CONSUMER,
    "tests/studio_windows_speech_acceptance.py",
)
EXPECTED = {
    "cancel": "owned detached child removed",
    "timeout": "owned detached child removed",
    "owner_death": "owned detached child removed",
    "finite_input": "bytes, stderr and nonzero status retained",
    **dict.fromkeys(
        ("service_cancel", "service_shutdown", "queued_cancel"),
        "run settled; no child, temporary recording, owner record or artifact",
    ),
}


def native_abi():
    from nexus.core import studio_speech_windows as windows

    expected = {
        "BasicLimits": 64,
        "ExtendedLimits": 144,
        "Accounting": 48,
        "Startup": 104,
        "StartupEx": 112,
        "ProcessInfo": 24,
    }
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise ValueError("This acceptance gate requires native Windows x64")
    actual = {name: ctypes.sizeof(getattr(windows, name)) for name in expected}
    if actual != expected:
        raise ValueError("Native Windows x64 API structure sizes do not match")
    return actual


def run_acceptance():
    report = {
        "format": "nexus-windows-speech-acceptance/v1",
        "date": datetime.now(UTC).isoformat(),
        "status": "unavailable",
        "native_verified": False,
        "platform": sys.platform,
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES
        },
        "scope": "Source process ownership and synthetic Studio job cancellation/shutdown; no installed app or real speech model acceptance",
    }
    revision = os.environ.get("GITHUB_SHA", "")
    if re.fullmatch(r"[a-fA-F0-9]{40,64}", revision):
        report["revision"] = revision
    if sys.platform != "win32":
        report["reason"] = "Native Windows required; another OS is not acceptance evidence"
        return report, 2
    try:
        report["abi_bytes"] = native_abi()
        result = subprocess.run(
            [sys.executable, str(ROOT / CONSUMER)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        report["consumer_exit_code"] = result.returncode
        if result.returncode:
            report["status"] = "failed"
            # Only this synthetic consumer runs: retain bounded diagnostics, not
            # environment variables or arbitrary provider/account responses.
            report["diagnostic"] = result.stderr[-4000:]
            return report, 1
        outcomes = json.loads(result.stdout)
        if outcomes != EXPECTED:
            raise ValueError("Native consumer did not confirm every required ownership case")
        report.update(status="passed", native_verified=True, outcomes=outcomes)
        return report, 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        report.update(status="failed", error_type=type(error).__name__)
        return report, 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args(argv)
    report, code = run_acceptance()
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "native_verified": report["native_verified"]}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
