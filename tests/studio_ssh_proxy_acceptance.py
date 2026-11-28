"""Explicit real SSH disconnect gate; no host ports, SSH keys or remote accounts.

Requires pinned NEXUS_ACCEPTANCE_SSH_IMAGE / NEXUS_ACCEPTANCE_WORKER_IMAGE IDs
and NEXUS_ACCEPTANCE_DOCKER_CONTEXT. Only the trusted proxy mounts the raw socket.
"""

import json
import os
import re
import subprocess
import time
import uuid

from nexus.core.studio_container_recovery import launch_environment

SCRIPT = "/checks/examples/nexus-bot/src/studio_proxy_ssh.py"
SOCKET_DIR = "/var/lib/nexus-studio/docker-proxy"
PRIVATE_DIR = "/var/lib/nexus-proxy-private"


def test_real_ssh_disconnect_and_cleanup(tmp_path):
    exercise_transport(tmp_path, transport="ssh")


def exercise_transport(tmp_path, *, transport):
    """Use the same real-daemon isolation/disconnect gates for SSH and TLS."""
    assert transport in {"ssh", "tls"}
    script = SCRIPT.replace("_ssh.py", "_tls.py") if transport == "tls" else SCRIPT
    context_name = "nexus-" + transport + "-check"
    client_target = "/tls-client" if transport == "tls" else "/home/nexus/.ssh"
    client_init = "/tls-client" if transport == "tls" else "/ssh-client"
    server_target = "/tls-server" if transport == "tls" else "/ssh-server"
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    image, worker = (
        os.environ[key]
        for key in (
            "NEXUS_ACCEPTANCE_" + transport.upper() + "_IMAGE",
            "NEXUS_ACCEPTANCE_WORKER_IMAGE",
        )
    )
    assert all(re.fullmatch(r"sha256:[a-f0-9]{64}", item) for item in (image, worker))
    environment = launch_environment(context)
    prefix = "nexus-" + transport + "-check-" + uuid.uuid4().hex[:12]
    volumes = {
        kind: prefix + "-" + kind
        for kind in (
            "private",
            "socket",
            "studio",
            "client",
            "server",
        )
    }
    containers, worker_id = [], None

    def docker(*args, check=True, timeout=30):
        return subprocess.run(
            ["docker", "--context", context, *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )

    def start(role, mounts, script, *command):
        name = prefix + "-" + role
        containers.append(name)
        args = [
            "run",
            "--detach",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "nexus.studio.validation=" + prefix,
            "--read-only",
            "--user",
            "1001:1001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "768m",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
            "--network",
            prefix if role in {"gateway", "controller"} else "none",
        ]
        if role == "gateway":
            args += ["--network-alias", "gateway"]
            if transport == "tls":
                args += ["--network-alias", "wrong-name"]
        if role == "proxy":
            args += [
                "--group-add",
                "0",
                "--mount",
                "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly",
            ]
        for source, target, readonly in mounts:
            args += [
                "--mount",
                f"type=volume,source={source},target={target}" + (",readonly" if readonly else ""),
            ]
        docker(*args, image, script, *command)
        return name

    def logs(name):
        result = docker("logs", name, check=False)
        return result.stdout + result.stderr

    def wait_file(name, filename, timeout=45):
        deadline = time.monotonic() + timeout
        while True:
            state = json.loads(docker("inspect", name).stdout)[0]["State"]
            assert state["Running"], logs(name)
            result = docker(
                "exec",
                name,
                "python",
                "-c",
                "from pathlib import Path; print(Path(" + repr(filename) + ").read_text())",
                check=False,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
            assert time.monotonic() < deadline, logs(name)
            time.sleep(0.1)

    try:
        docker(
            "network",
            "create",
            "--internal",
            "--label",
            "nexus.studio.validation=" + prefix,
            prefix,
        )
        for volume in volumes.values():
            docker("volume", "create", "--label", "nexus.studio.validation=" + prefix, volume)
        initializer = start(
            "initialize",
            [
                (volumes["client"], client_init, False),
                (volumes["server"], server_target, False),
            ],
            script,
            "--phase",
            "initialize",
        )
        assert docker("wait", initializer).stdout.strip() == "0", logs(initializer)
        proxy = start(
            "proxy",
            [
                (volumes["private"], PRIVATE_DIR, False),
                (volumes["socket"], SOCKET_DIR, False),
            ],
            "/checks/examples/nexus-bot/src/studio_proxy_linux.py",
            "--phase",
            "serve",
            "--image",
            worker,
        )
        gateway = start(
            "gateway",
            [
                (volumes["server"], server_target, True),
                (volumes["socket"], SOCKET_DIR, True),
            ],
            script,
            "--phase",
            "gateway",
        )
        controller = start(
            "controller",
            [
                (volumes["client"], client_target, True),
                (volumes["studio"], "/var/lib/nexus-studio", False),
            ],
            script,
            "--phase",
            "exercise",
            "--image",
            worker,
        )
        try:
            ready = wait_file(controller, "/tmp/ready.json")
        except AssertionError as error:
            raise AssertionError(
                str(error) + "\nGATEWAY\n" + logs(gateway) + "\nPROXY\n" + logs(proxy)
            ) from error
        worker_id = ready["container_id"]
        assert json.loads(docker("inspect", worker_id).stdout)[0]["State"]["Running"]
        for name in (controller, gateway):
            info = json.loads(docker("inspect", name).stdout)[0]
            assert info["Config"]["User"] == "1001:1001"
            assert not info["HostConfig"].get("PortBindings")
            assert all(
                m["Destination"] not in {"/var/run/docker.sock", PRIVATE_DIR}
                for m in info["Mounts"]
            )
        controller_info = json.loads(docker("inspect", controller).stdout)[0]
        assert all(m["Destination"] != SOCKET_DIR for m in controller_info["Mounts"])
        docker("kill", "--signal", "KILL", gateway)
        disconnected = wait_file(controller, "/tmp/disconnected.json", timeout=45)
        assert disconnected["error"]
        # Test actual owner-stdin EOF before reconnect or explicit removal.
        deadline = time.monotonic() + 15
        while docker("inspect", worker_id, check=False).returncode == 0:
            assert time.monotonic() < deadline, "Analysis worker survived SSH owner loss"
            time.sleep(0.1)
        docker("start", gateway)
        # Separate readiness probe cannot launch or replay analysis.
        deadline = time.monotonic() + 20
        while True:
            checked = docker(
                "exec",
                controller,
                "docker",
                "--config",
                "/tmp/docker-config",
                "--context",
                context_name,
                "version",
                "--format",
                "{{.Server.Version}}",
                check=False,
            )
            if checked.returncode == 0:
                break
            assert time.monotonic() < deadline, checked.stderr + logs(gateway)
            time.sleep(0.1)
        docker(
            "exec",
            controller,
            "python",
            "-c",
            "from pathlib import Path; Path('/tmp/recover-now').touch()",
        )
        assert docker("wait", controller, timeout=30).stdout.strip() == "0", logs(controller)
        receipt_prefix = "NEXUS_" + transport.upper() + "_ACCEPTANCE "
        line = next(
            line for line in logs(controller).splitlines() if line.startswith(receipt_prefix)
        )
        result = json.loads(line.removeprefix(receipt_prefix))
        assert result["healthy_execution"] and result["reconnected_cleanup"]
        assert result["unreachable_cleanup_pending"] and result["extra_runs"] == 0
        proxy_log = logs(proxy)
        assert (
            len(re.findall(r"^allow POST /(?:v[\d.]+/)?containers/create$", proxy_log, re.M)) == 2
        )
        assert (
            len(
                re.findall(
                    r"^allow POST /(?:v[\d.]+/)?containers/[a-f0-9]+/start$", proxy_log, re.M
                )
            )
            == 2
        )
        result.update(worker_stopped_before_reconnect=True, analysis_creations=2, analysis_starts=2)
        if transport == "tls":
            assert result["rejected_certificates"] == [
                "missing-client",
                "untrusted-client",
                "untrusted-server",
                "wrong-hostname",
            ]
            gateway_log = logs(gateway)
            assert "PEER_DID_NOT_RETURN_A_CERTIFICATE" in gateway_log, gateway_log
            assert "CERTIFICATE_VERIFY_FAILED" in gateway_log, gateway_log
        (tmp_path / (transport + "-proxy-result.json")).write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        if worker_id:
            docker("rm", "--force", worker_id, check=False)
        for name in reversed(containers):
            docker("rm", "--force", name, check=False)
        for volume in volumes.values():
            docker("volume", "rm", volume, check=False)
        docker("network", "rm", prefix, check=False)
