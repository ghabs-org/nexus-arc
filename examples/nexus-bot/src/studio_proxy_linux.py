"""Consume Studio recovery in isolated Linux proxy/controller containers.

Used by tests/studio_linux_proxy_acceptance.py. All mounts and identities are
disposable test resources. The controller never receives the real Docker socket
or private proxy ledger; both containers deliberately use uid/gid 1001.
"""

import argparse
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

SOCKET = "unix:///var/lib/nexus-studio/docker-proxy/docker.sock"
PRIVATE = Path("/var/lib/nexus-proxy-private/ownership.sqlite")


def serve(image):
    config = Path("/tmp/proxy-config")
    config.mkdir(mode=0o700)
    for name, value in (
        ("runtime", {"analysis_workers": {"python": {"image": image}}}),
        ("team", {"project_key": "linux-private-proxy"}),
    ):
        path = config / (name + ".json")
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    os.environ.update(
        PROXY_LISTEN=SOCKET.removeprefix("unix://"),
        PROXY_UPSTREAM="/var/run/docker.sock",
        PROXY_STATE_DIR=str(PRIVATE.parent),
        STUDIO_RUNTIME_CONFIG=str(config / "runtime.json"),
        STUDIO_TEAM_CONFIG=str(config / "team.json"),
    )
    os.execv(sys.executable, [sys.executable, "-I", "/app/docker_proxy.py"])


def exercise(image, private_volume):
    from nexus.core.studio import StudioService
    from nexus.core.studio_container_recovery import LABEL

    assert platform.system() == "Linux" and os.getuid() == 1001
    assert not Path("/var/run/docker.sock").exists()
    assert not PRIVATE.exists()
    # A compromised app may write its own filesystem. A fake file at the same
    # path must never replace the proxy's separately mounted authority.
    PRIVATE.write_bytes(b"forged ownership in controller namespace")
    PRIVATE.chmod(0o600)
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        os.environ.pop(key, None)
    os.environ["DOCKER_HOST"] = SOCKET
    docker = ["docker", "--host", SOCKET]

    def run(args):
        return subprocess.run(args, capture_output=True, timeout=15)

    deadline = time.monotonic() + 30
    while run([*docker, "version", "--format", "{{.Server.Version}}"]).returncode:
        assert time.monotonic() < deadline, "Linux proxy did not become ready"
        time.sleep(0.05)
    studio = StudioService(
        "/var/lib/nexus-studio/project",
        data_tool_options={"profiles": {"python": {"image": image}}},
    )
    try:
        sandbox = studio.data_tools.sandbox
        assert sandbox.execute("print('linux-private-proxy')") == {
            "stdout": "linux-private-proxy\n",
            "files": [],
        }
        assert sandbox.recovery.pending() == 0
        denied_name = "nexus-analysis-" + uuid.uuid4().hex
        command = sandbox.arguments("python", denied_name)
        command[1] = "create"
        index = command.index("--entrypoint")
        command[index:index] = [
            "--mount",
            f"type=volume,source={private_volume},destination=/private",
        ]
        refused = run(command)
        assert refused.returncode != 0 and b"nexus docker proxy" in refused.stderr
        name = "nexus-analysis-" + uuid.uuid4().hex
        lease = sandbox.recovery.register(name, ["docker"], dict(os.environ), subprocess.run)
        try:
            command = sandbox.arguments("python", name)
            command[1] = "create"
            command[2:2] = [
                "--label",
                LABEL + "=" + name,
                "--label",
                "nexus.studio.validation=" + os.environ["NEXUS_VALIDATION_SCOPE"],
            ]
            created = run(command)
            assert created.returncode == 0, created.stderr.decode()
            identity = created.stdout.decode().strip()
            lease.created(identity)
        finally:
            lease.finish(False)
        ready = Path("/tmp/ready.json")
        ready.write_text(json.dumps({"container_id": identity, "denied_name": denied_name}))
        ready.chmod(0o600)
        deadline = time.monotonic() + 60
        while not Path("/tmp/recover-now").exists():
            assert time.monotonic() < deadline, "Proxy restart signal was not delivered"
            time.sleep(0.05)
        refused = run([*docker, "start", identity])
        assert refused.returncode != 0 and b"cleanup only" in refused.stderr
        inspected = run([*docker, "inspect", identity])
        assert inspected.returncode == 0, inspected.stderr.decode()
        assert json.loads(inspected.stdout)[0]["State"]["Status"] == "created"
        recovered = sandbox.recover_cleanup()
        assert recovered == {"items": [{"name": name, "status": "removed"}], "pending": 0}, (
            recovered
        )
        assert not studio.records("runs") and not studio.catalog.list("artifacts")
        return {
            "platform": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "controller_uid": os.getuid(),
            "raw_socket_visible": False,
            "private_ledger_visible": False,
            "controller_local_forgery_isolated": True,
            "private_volume_injection_blocked": True,
            "restart_cleanup": True,
            "old_work_started": False,
            "extra_runs": 0,
        }
    finally:
        studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["serve", "exercise"], required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--private-volume")
    args = parser.parse_args()
    if args.phase == "serve":
        serve(args.image)
    else:
        print(
            "NEXUS_PROXY_ACCEPTANCE " + json.dumps(exercise(args.image, args.private_volume)),
            flush=True,
        )
