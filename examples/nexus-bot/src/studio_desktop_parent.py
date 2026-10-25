"""Demonstrate native backend shutdown when its parent closes the private pipe.

Starts only a disposable local workspace with no jobs, providers or live accounts.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from nexus.core.studio_desktop import DesktopLaunchCredential


def wait_for_native(process, log, credential, workspace, timeout=15):
    deadline = time.monotonic() + timeout
    with requests.Session() as client:
        client.trust_env = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("Disposable native backend exited before readiness")
            lines = log.read_text().splitlines() if log.exists() else []
            address = next(
                (
                    s.removeprefix("Nexus Studio: ")
                    for s in lines
                    if s.startswith("Nexus Studio: http://127.0.0.1:")
                ),
                None,
            )
            if address:
                try:
                    challenge = "ab" * 32
                    port = urlsplit(address).port
                    response = client.get(
                        address + "/api/desktop/handshake",
                        headers={"X-Nexus-Desktop-Challenge": challenge},
                        timeout=1,
                    )
                    if response.status_code == 200 and response.json()["proof"] == credential.proof(
                        challenge, str(workspace.resolve()), port
                    ):
                        assert client.get(address + "/api/bootstrap", timeout=1).status_code == 401
                        return address
                except (requests.RequestException, ValueError, KeyError):
                    pass
            time.sleep(0.05)
    raise AssertionError("Disposable native backend readiness timed out")


def exercise():
    with tempfile.TemporaryDirectory(prefix="nexus-parent-lifetime-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        log = root / "backend.log"
        credential = DesktopLaunchCredential.generate()
        with log.open("w") as output:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "nexus.cli",
                    "studio",
                    "--dir",
                    str(workspace),
                    "--port",
                    "0",
                    "--desktop-launch-stdin",
                ],
                cwd=Path(__file__).resolve().parents[3],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=output,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            try:
                child.stdin.write(credential.line())
                child.stdin.flush()
                address = wait_for_native(child, log, credential, workspace)
                child.stdin.close()
                assert child.wait(timeout=10) == 0
                with requests.Session() as client:
                    client.trust_env = False
                    try:
                        client.get(address + "/api/bootstrap", timeout=0.5)
                    except requests.ConnectionError:
                        pass
                    else:
                        raise AssertionError("Native HTTP listener survived parent EOF")
                assert credential.line().decode().strip() not in log.read_text()
                return {
                    "parent_eof_stops_backend": True,
                    "listener_closed": True,
                    "launch_secret_logged": False,
                    "live_jobs_started": 0,
                }
            finally:
                if child.stdin and not child.stdin.closed:
                    child.stdin.close()
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(3)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(exercise(), indent=2))
