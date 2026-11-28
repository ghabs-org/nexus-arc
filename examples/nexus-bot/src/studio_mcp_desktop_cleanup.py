"""Verify a frozen backend closes real MCP process trees when its launcher exits."""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

from nexus.core.studio_desktop import DesktopLaunchCredential, WorkspaceLock, is_process_alive

LAUNCHER = r"""
import json,os,subprocess,sys
secret=sys.stdin.buffer.readline(65)
child=subprocess.Popen([sys.argv[1],'--dir',sys.argv[2],'--port','0','--desktop-launch-stdin'],
 stdin=subprocess.PIPE,start_new_session=True)
print('Fixture backend: '+str(child.pid),flush=True)
child.stdin.write(secret);child.stdin.flush()
try:
 for line in sys.stdin.buffer:
  child.stdin.write(line);child.stdin.flush();break
finally:
 child.stdin.close()
 child.wait(15)
"""


def exercise(executable, persistent=False, shutdown="stop"):
    if os.name != "posix":
        raise RuntimeError(
            "Process-group acceptance requires a POSIX host; Windows cleanup remains separate"
        )
    executable = Path(executable).resolve()
    fixture = Path(__file__).with_name("fixtures") / "mcp_lifecycle_server.py"
    with tempfile.TemporaryDirectory(prefix="nexus-mcp-native-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        log = root / "launcher.log"
        credential = DesktopLaunchCredential.generate()
        parent, backend_pid, pids = None, None, {}
        responses = []
        with log.open("w") as output, requests.Session() as client:
            client.trust_env = False
            try:
                parent = subprocess.Popen(
                    [sys.executable, "-u", "-c", LAUNCHER, str(executable), str(workspace)],
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=output,
                    cwd=root,
                    env={"PATH": os.defpath, "HOME": str(root), "TMPDIR": str(root)},
                )
                parent.stdin.write(credential.line())
                parent.stdin.flush()
                deadline = time.monotonic() + 30
                address = None
                while time.monotonic() < deadline:
                    assert parent.poll() is None, log.read_text()[-2000:]
                    lines = log.read_text().splitlines()
                    backend_pid = next(
                        (
                            int(line.split(": ", 1)[1])
                            for line in lines
                            if line.startswith("Fixture backend: ")
                        ),
                        backend_pid,
                    )
                    address = next(
                        (
                            line.removeprefix("Nexus Studio: ")
                            for line in lines
                            if line.startswith("Nexus Studio: http://127.0.0.1:")
                        ),
                        None,
                    )
                    if address:
                        break
                    time.sleep(0.05)
                assert address and backend_pid
                port = int(address.rsplit(":", 1)[1])
                challenge = os.urandom(32).hex()
                proof = client.get(
                    address + "/api/desktop/handshake",
                    headers={"X-Nexus-Desktop-Challenge": challenge},
                    timeout=5,
                ).json()
                assert proof["proof"] == credential.proof(challenge, str(workspace.resolve()), port)
                client.cookies.set(
                    credential.COOKIE, credential.session(), domain="127.0.0.1", path="/"
                )
                bootstrap = client.get(address + "/api/bootstrap", timeout=5)
                bootstrap.raise_for_status()
                csrf = bootstrap.json()["token"]
                connector = client.post(
                    address + "/api/catalog/connectors",
                    headers={"X-Nexus-Studio-Token": csrf},
                    json={
                        "name": "Owned MCP cleanup fixture",
                        "data": {
                            "type": "mcp",
                            "command": sys.executable,
                            "args": [str(fixture), str(root)],
                            "persistent": persistent,
                            "enabled": True,
                            "actions": ["discover", "call"],
                            "tools": ["wait"],
                            "read_only_tools": ["wait"],
                        },
                    },
                    timeout=5,
                )
                connector.raise_for_status()

                def call():
                    try:
                        result = client.post(
                            address + "/api/connectors/" + connector.json()["id"] + "/test",
                            headers={"X-Nexus-Studio-Token": csrf},
                            json={"action": "call", "tool": "wait"},
                            timeout=25,
                        )
                        responses.append(result.status_code)
                    except requests.RequestException:
                        responses.append("closed")

                caller = threading.Thread(target=call)
                caller.start()
                deadline = time.monotonic() + 15
                while not (root / "called").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert (root / "called").exists(), (responses, log.read_text()[-2000:])
                pids = json.loads((root / "pids.json").read_text())
                assert pids["server"] == pids["group"] and pids["group"] != os.getpgid(backend_pid)
                started = time.monotonic()
                if shutdown == "kill":
                    parent.kill()
                else:
                    parent.stdin.write(b"close\n")
                    parent.stdin.flush()
                parent.wait(15)
                deadline = started + 10
                while (
                    any(
                        is_process_alive(pid)
                        for pid in [backend_pid, pids["server"], pids["child"]]
                    )
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                assert not any(
                    is_process_alive(pid) for pid in [backend_pid, pids["server"], pids["child"]]
                )
                caller.join(2)
                assert not caller.is_alive() and len(responses) == 1
                assert (root / "calls.log").read_text().splitlines() == ["wait"]
                lock = WorkspaceLock(workspace)
                assert lock.acquire(0)
                lock.release()
                assert credential.line().decode().strip() not in log.read_text()
                return {
                    "persistent": persistent,
                    "shutdown": shutdown,
                    "backend_stopped": True,
                    "server_stopped": True,
                    "child_stopped": True,
                    "workspace_released": True,
                    "calls": 1,
                    "close_seconds": round(time.monotonic() - started, 3),
                    "live_accounts_used": False,
                }
            finally:
                if parent and parent.poll() is None:
                    parent.kill()
                    parent.wait(5)
                if parent and parent.stdin:
                    parent.stdin.close()
                for pid in [backend_pid, pids.get("server")]:
                    if pid and is_process_alive(pid) and os.getpgid(pid) == pid:
                        os.killpg(pid, signal.SIGKILL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--persistent", action="store_true")
    parser.add_argument("--shutdown", choices=["stop", "kill"], default="stop")
    args = parser.parse_args()
    print(json.dumps(exercise(args.executable, args.persistent, args.shutdown), indent=2))
