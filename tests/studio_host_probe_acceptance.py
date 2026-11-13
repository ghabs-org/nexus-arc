"""Explicit POSIX Unix-socket/stat acceptance. No prerequisite skips."""

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from nexus.core.studio_host_checks import STAT_PROBE


@pytest.mark.parametrize(
    "fault", ["symlink-directory", "mode", "ledger-link", "ledger-mode", "hardlink", "world-socket"]
)
def test_fixed_probe_checks_real_metadata_without_reading_ledger(tmp_path, fault):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    ledger = private / "ownership.sqlite"
    ledger.write_text("THIS_IS_NOT_SQLITE_SECRET")
    ledger.chmod(0o600)
    with (
        tempfile.TemporaryDirectory(prefix="nexus-stat-", dir="/tmp") as short,
        socket.socket(socket.AF_UNIX) as server,
    ):
        socket_path = Path(short) / "socket"
        server.bind(str(socket_path))
        socket_path.chmod(0o600)
        target = private
        if fault == "symlink-directory":
            target = tmp_path / "link"
            target.symlink_to(private, target_is_directory=True)
        elif fault == "mode":
            private.chmod(0o750)
        elif fault == "ledger-link":
            ledger.rename(private / "actual")
            ledger.symlink_to(private / "actual")
        elif fault == "ledger-mode":
            ledger.chmod(0o640)
        elif fault == "hardlink":
            os.link(ledger, private / "copy")
        else:
            socket_path.chmod(0o666)
        result = subprocess.run(
            [sys.executable, "-I", "-c", STAT_PROBE, str(target), str(socket_path)],
            capture_output=True,
            check=True,
        )
    assert b"SECRET" not in result.stdout
    metadata = json.loads(result.stdout)
    expected = {
        "symlink-directory": "directory",
        "mode": "directory_mode",
        "world-socket": "socket_access",
    }.get(fault, "ledger_private")
    assert metadata[expected] is False
