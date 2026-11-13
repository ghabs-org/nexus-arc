"""Actual native CLI and parent-process termination acceptance; no webview required."""

import json
import os
import runpy
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from nexus.core.studio_desktop import DesktopLaunchCredential, is_process_alive

ROOT = Path(__file__).resolve().parents[1]


def test_closing_parent_pipe_shuts_down_real_native_cli():
    example = runpy.run_path(str(ROOT / "examples/nexus-bot/src/studio_desktop_parent.py"))
    assert example["exercise"]() == {
        "parent_eof_stops_backend": True,
        "listener_closed": True,
        "launch_secret_logged": False,
        "live_jobs_started": 0,
    }


PARENT = r"""
import json,os,subprocess,sys,threading
secret=sys.stdin.buffer.readline(65)
child=subprocess.Popen([sys.executable,'-m','nexus.cli','studio','--dir',sys.argv[1],'--port','0','--desktop-launch-stdin'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr,text=False,start_new_session=os.name=='posix',env={**os.environ,'PYTHONUNBUFFERED':'1'})
print(json.dumps({'pid':child.pid}),flush=True)
child.stdin.write(secret);child.stdin.flush()
for line in iter(child.stdout.readline,b''):
    text=line.decode().strip()
    if text.startswith('Nexus Studio: http://127.0.0.1:'):
        print(json.dumps({'address':text.removeprefix('Nexus Studio: ')}),flush=True)
        break
threading.Event().wait()
"""


@pytest.mark.parametrize("abrupt", ["terminate", "kill"])
def test_abnormally_terminated_parent_does_not_leave_native_listener(tmp_path, abrupt):
    credential = DesktopLaunchCredential.generate()
    workspace = tmp_path / "workspace"
    log = tmp_path / "parent.ndjson"
    error = tmp_path / "parent.err"
    parent = None
    backend_pid = None
    address = None
    with requests.Session() as client, log.open("w") as output, error.open("w") as stderr:
        client.trust_env = False
        try:
            parent = subprocess.Popen(
                [sys.executable, "-u", "-c", PARENT, str(workspace)],
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=stderr,
            )
            parent.stdin.write(credential.line())
            parent.stdin.flush()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                records = [
                    json.loads(line)
                    for line in log.read_text().splitlines(keepends=True)
                    if line.endswith("\n")
                ]
                backend_pid = next((r["pid"] for r in records if "pid" in r), backend_pid)
                address = next((r["address"] for r in records if "address" in r), address)
                if address:
                    try:
                        response = client.get(
                            address + "/api/desktop/handshake",
                            headers={"X-Nexus-Desktop-Challenge": "ab" * 32},
                            timeout=0.5,
                        )
                        if response.status_code == 200:
                            break
                    except requests.RequestException:
                        pass
                assert parent.poll() is None, "Synthetic shell exited before startup"
                time.sleep(0.05)
            assert address and backend_pid, "Synthetic shell did not start the native backend"
            port = int(address.rsplit(":", 1)[1])
            assert response.json()["proof"] == credential.proof(
                "ab" * 32, str(workspace.resolve()), port
            )
            getattr(parent, abrupt)()
            parent.wait(5)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    client.get(address + "/api/bootstrap", timeout=0.3)
                except requests.ConnectionError:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("Native backend listener survived abnormal parent exit")
            while time.monotonic() < deadline and is_process_alive(backend_pid):
                time.sleep(0.05)
            assert not is_process_alive(backend_pid), (
                "Native backend process survived parent termination"
            )
            assert credential.line().decode().strip() not in log.read_text() + error.read_text()
        finally:
            if parent and parent.poll() is None:
                parent.kill()
                parent.wait(5)
            if parent and parent.stdin:
                parent.stdin.close()
            # Failure cleanup is limited to this authenticated disposable child.
            if address and backend_pid:
                try:
                    response = client.get(
                        address + "/api/desktop/handshake",
                        headers={"X-Nexus-Desktop-Challenge": "ab" * 32},
                        timeout=0.3,
                    )
                    if response.status_code == 200 and response.json().get(
                        "proof"
                    ) == credential.proof(
                        "ab" * 32, str(workspace.resolve()), int(address.rsplit(":", 1)[1])
                    ):
                        if os.name == "posix" and os.getpgid(backend_pid) == backend_pid:
                            os.killpg(backend_pid, signal.SIGKILL)
                        elif os.name == "nt":
                            os.kill(backend_pid, signal.SIGTERM)
                except (requests.RequestException, ProcessLookupError, KeyError, ValueError):
                    pass


def test_process_can_exit_while_parent_input_is_still_open():
    code = "from nexus.core.studio_desktop_parent import watch_parent_input; import sys,time; watch_parent_input(sys.stdin.buffer,lambda:None); print('watching',flush=True); time.sleep(.1)"
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert child.wait(timeout=5) == 0
        assert child.stdout.read() == b"watching\n"
        assert b"Fatal Python error" not in child.stderr.read()
    finally:
        child.stdin.close()
        if child.poll() is None:
            child.kill()
            child.wait(3)
        child.stdout.close()
        child.stderr.close()


@pytest.mark.parametrize("extra", [b"", b"unexpected-parent-data"])
def test_parent_disconnect_before_readiness_does_not_hang(tmp_path, extra):
    credential = DesktopLaunchCredential.generate()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nexus.cli",
            "studio",
            "--dir",
            str(tmp_path),
            "--port",
            "0",
            "--desktop-launch-stdin",
        ],
        cwd=ROOT,
        input=credential.line() + extra,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert credential.line().strip() not in result.stdout + result.stderr
    assert b"unexpected-parent-data" not in result.stdout + result.stderr
