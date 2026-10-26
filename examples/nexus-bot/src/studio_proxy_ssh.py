"""Exercise shared Docker routing/recovery through disposable, key-only SSH.

The SSH endpoint receives only the filtered socket. Generated client/server keys
live in separate disposable volumes; no operator SSH configuration is read.
"""

import argparse
import json
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

SOCKET = "unix:///var/lib/nexus-studio/docker-proxy/docker.sock"
CLIENT, SERVER = Path("/ssh-client"), Path("/ssh-server")
CONTEXT = "nexus-ssh-check"


def initialize():
    for directory, filename in ((CLIENT, "id_ed25519"), (SERVER, "host_key")):
        directory.chmod(0o700)
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(directory / filename)],
            check=True,
        )
    (SERVER / "authorized_keys").write_text((CLIENT / "id_ed25519.pub").read_text())
    (CLIENT / "known_hosts").write_text("[gateway]:2222 " + (SERVER / "host_key.pub").read_text())
    (CLIENT / "config").write_text(
        "Host gateway\n"
        "  IdentityFile /home/nexus/.ssh/id_ed25519\n"
        "  UserKnownHostsFile /home/nexus/.ssh/known_hosts\n"
        "  IdentitiesOnly yes\n  BatchMode yes\n  StrictHostKeyChecking yes\n"
        "  ControlMaster no\n  ConnectTimeout 2\n  ConnectionAttempts 1\n"
        "  ServerAliveInterval 1\n  ServerAliveCountMax 2\n"
    )
    (SERVER / "sshd_config").write_text(
        "Port 2222\nListenAddress 0.0.0.0\n"
        "HostKey /ssh-server/host_key\nAuthorizedKeysFile /ssh-server/authorized_keys\n"
        "PidFile /tmp/sshd.pid\nUsePAM no\nAllowUsers nexus\n"
        "PasswordAuthentication no\nKbdInteractiveAuthentication no\n"
        "AuthenticationMethods publickey\nPermitRootLogin no\nStrictModes yes\n"
        "AllowTcpForwarding no\nAllowStreamLocalForwarding no\n"
        "AllowAgentForwarding no\nX11Forwarding no\nPermitTunnel no\nPermitTTY no\n"
        "ForceCommand /usr/local/bin/docker --host " + SOCKET + " system dial-stdio\n"
    )
    for directory in (CLIENT, SERVER):
        for path in directory.iterdir():
            path.chmod(0o600)


def gateway():
    assert not Path("/var/run/docker.sock").exists()
    assert not Path("/var/lib/nexus-proxy-private/ownership.sqlite").exists()
    os.execv("/usr/sbin/sshd", ["/usr/sbin/sshd", "-D", "-e", "-f", "/ssh-server/sshd_config"])


def exercise(image, *, context=CONTEXT, endpoint="ssh://nexus@gateway:2222", preflight=None):
    from nexus.core.studio import StudioService

    assert platform.system() == "Linux" and os.getuid() == 1001
    assert not Path("/var/run/docker.sock").exists()
    assert not Path(SOCKET.removeprefix("unix://")).exists()
    assert not Path("/var/lib/nexus-proxy-private/ownership.sqlite").exists()
    for key in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        os.environ.pop(key, None)
    os.environ["DOCKER_CONFIG"] = "/tmp/docker-config"
    subprocess.run(
        ["docker", "context", "create", context, "--docker", "host=" + endpoint],
        check=True,
        capture_output=True,
    )
    docker = ["docker", "--context", context]

    def run(*args):
        return subprocess.run([*docker, *args], capture_output=True, timeout=8)

    deadline = time.monotonic() + 30
    while True:
        checked = run("version", "--format", "{{.Server.Version}}")
        if checked.returncode == 0:
            break
        assert time.monotonic() < deadline, checked.stderr.decode()
        time.sleep(0.1)
    if preflight:
        preflight()
    studio = StudioService(
        "/var/lib/nexus-studio/project",
        data_tool_options={
            "profiles": {
                "python": {
                    "image": image,
                    "docker_context": context,
                    "timeout": 120,
                }
            }
        },
    )
    thread, outcome = None, {}
    try:
        sandbox = studio.data_tools.sandbox
        assert sandbox.execute("print('ssh-filtered-proxy')") == {
            "stdout": "ssh-filtered-proxy\n",
            "files": [],
        }
        assert sandbox.recovery.pending() == 0

        def execute():
            try:
                outcome["result"] = sandbox.execute("import time; time.sleep(120)")
            except Exception as error:
                outcome["error"] = str(error)

        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while True:
            assert thread.is_alive(), outcome
            records = list(sandbox.recovery.directory.glob("*/record.json"))
            if records:
                record = studio._read(records[0])
                identity = record.get("container_id")
                if identity:
                    checked = run("inspect", identity)
                    if (
                        checked.returncode == 0
                        and json.loads(checked.stdout)[0]["State"]["Running"]
                    ):
                        break
            assert time.monotonic() < deadline, "Worker never reached acknowledged start"
            time.sleep(0.1)
        Path("/tmp/ready.json").write_text(
            json.dumps(
                {
                    "container_id": identity,
                    "name": record["name"],
                }
            )
        )
        thread.join(35)
        assert not thread.is_alive(), "Disconnect did not terminate the Docker client"
        assert "error" in outcome and "result" not in outcome, outcome
        assert sandbox.recovery.pending() == 1
        unavailable = sandbox.recover_cleanup(timeout=5)
        assert unavailable == {
            "items": [{"name": record["name"], "status": "unconfirmed"}],
            "pending": 1,
        }, unavailable
        Path("/tmp/disconnected.json").write_text(json.dumps({"error": outcome["error"]}))
        deadline = time.monotonic() + 45
        while not Path("/tmp/recover-now").exists():
            assert time.monotonic() < deadline, "Reconnect gate not delivered"
            time.sleep(0.1)
        recovered = sandbox.recover_cleanup(timeout=15)
        assert recovered == {
            "items": [{"name": record["name"], "status": "removed"}],
            "pending": 0,
        }, recovered
        assert not studio.records("runs") and not studio.catalog.list("artifacts")
        return {
            "platform": platform.system(),
            "controller_uid": os.getuid(),
            "named_ssh_context": endpoint.startswith("ssh://"),
            "named_context": context,
            "healthy_execution": True,
            "disconnect_reported": True,
            "unreachable_cleanup_pending": True,
            "reconnected_cleanup": True,
            "extra_runs": 0,
            "raw_socket_visible": False,
            "filtered_socket_visible": False,
            "private_ledger_visible": False,
            "client_error": outcome["error"],
        }
    finally:
        studio.close()
        if thread:
            thread.join(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["initialize", "gateway", "exercise"], required=True)
    parser.add_argument("--image")
    args = parser.parse_args()
    if args.phase == "initialize":
        initialize()
    elif args.phase == "gateway":
        gateway()
    else:
        print("NEXUS_SSH_ACCEPTANCE " + json.dumps(exercise(args.image)), flush=True)
