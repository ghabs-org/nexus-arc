"""Actual Docker mutual-TLS expiry, renewal and CA trust withdrawal.

Uses installed immutable TLS/worker images, the existing filtering proxy and
real Docker version calls. No worker creation, published ports or personal keys.
"""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from nexus.core.studio_container_recovery import launch_environment


def test_real_tls_expiry_renewal_and_ca_rotation(request):
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    image = os.environ["NEXUS_ACCEPTANCE_TLS_IMAGE"]
    worker = os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"]
    assert all(re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in (image, worker))
    environment = launch_environment(context)
    scope = "nexus-tls-lifecycle-" + uuid.uuid4().hex[:12]
    volumes = {key: scope + "-" + key for key in ["client", "server", "private", "socket"]}
    containers = []
    fixture = (Path(__file__).parent / "fixtures/studio_tls_lifecycle.py").read_text()
    socket_dir = "/var/lib/nexus-studio/docker-proxy"
    private_dir = "/var/lib/nexus-proxy-private"
    examples = "/checks/examples/nexus-bot/src/"

    def docker(*args, check=True, timeout=30, input=None):
        result = subprocess.run(
            ["docker", "--context", context, *args],
            env=environment,
            capture_output=True,
            text=True,
            input=input,
            timeout=timeout,
        )
        if check:
            assert result.returncode == 0, result.stderr[-2000:]
        return result

    def start(role, mounts, *command, detach=True, input=None):
        name = scope + "-" + role + "-" + uuid.uuid4().hex[:6]
        containers.append(name)
        args = [
            "run",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "nexus.studio.validation=" + scope,
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
            "512m",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
            "--network",
            scope if role in {"gateway", "controller"} else "none",
        ]
        args += ["--detach"] if detach else ["--interactive"]
        if role == "gateway":
            args += ["--network-alias", "gateway"]
        if role == "proxy":
            args += [
                "--group-add",
                "0",
                "--mount",
                "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly",
            ]
        for key, target, readonly in mounts:
            args += [
                "--mount",
                "type=volume,source="
                + volumes[key]
                + ",target="
                + target
                + (",readonly" if readonly else ""),
            ]
        result = docker(*args, image, *command, input=input, timeout=90)
        return name, result

    def setup(code, *, initial=False):
        mounts = [("server", "/tls-server", False)]
        if initial:
            mounts += [("client", "/tls-client", False)]
        return start("setup", mounts, "-", detach=False, input=fixture + "\n" + code)

    def logs(name):
        result = docker("logs", name)
        return result.stdout + result.stderr

    def probe(client="old-client", ca="old"):
        return docker(
            "exec",
            controller,
            "docker",
            "--config",
            "/tmp/empty-docker-config",
            "--tlsverify",
            "--tlscacert",
            "/tls-client/" + ca + "-ca.pem",
            "--tlscert",
            "/tls-client/" + client + ".pem",
            "--tlskey",
            "/tls-client/" + client + ".key",
            "--host",
            "tcp://gateway:2376",
            "version",
            "--format",
            "{{.Server.Version}}",
            check=False,
            timeout=12,
        )

    def healthy(client="old-client", ca="old"):
        deadline = time.monotonic() + 15
        while True:
            result = probe(client, ca)
            if result.returncode == 0:
                assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[^\n]*\n?", result.stdout)
                return result.stdout.strip()
            assert time.monotonic() < deadline, result.stderr + logs(gateway)
            time.sleep(0.1)

    def replace(identity, authority):
        docker("stop", "--time", "5", gateway)
        setup("select_server(" + repr(identity) + "," + repr(authority) + ")")
        docker("start", gateway)
        # Require listener readiness independently of whether this identity is trusted.
        deadline = time.monotonic() + 10
        while True:
            result = docker(
                "exec",
                gateway,
                "python",
                "-c",
                "import socket;s=socket.create_connection(('127.0.0.1',2376),1);s.close()",
                check=False,
            )
            if result.returncode == 0:
                return
            assert time.monotonic() < deadline, logs(gateway)
            time.sleep(0.1)

    try:
        docker(
            "network", "create", "--internal", "--label", "nexus.studio.validation=" + scope, scope
        )
        for volume in volumes.values():
            docker("volume", "create", "--label", "nexus.studio.validation=" + scope, volume)
        initializer, _ = setup("initialize()", initial=True)
        # Initializer has exited; CA private keys lived in tmpfs, never mounted volumes.
        assert json.loads(docker("inspect", initializer).stdout)[0]["State"]["ExitCode"] == 0
        proxy, _ = start(
            "proxy",
            [("private", private_dir, False), ("socket", socket_dir, False)],
            examples + "studio_proxy_linux.py",
            "--phase",
            "serve",
            "--image",
            worker,
        )
        gateway, _ = start(
            "gateway",
            [("server", "/tls-server", True), ("socket", socket_dir, True)],
            examples + "studio_proxy_tls.py",
            "--phase",
            "gateway",
        )
        controller, _ = start(
            "controller", [("client", "/tls-client", True)], "-c", "import time;time.sleep(300)"
        )
        for name in [gateway, controller]:
            info = json.loads(docker("inspect", name).stdout)[0]
            assert not info["HostConfig"].get("PortBindings")
            assert all(
                m["Destination"] not in {"/var/run/docker.sock", private_dir}
                for m in info["Mounts"]
            )
            docker(
                "exec",
                name,
                "python",
                "-c",
                "from pathlib import Path;assert not any(Path(p).exists() for p in "
                "['/tmp/old.key','/tmp/new.key','/var/run/docker.sock',"
                "'/var/lib/nexus-proxy-private/ownership.sqlite']);"
                "assert all(p.stat().st_mode & 0o077 == 0 "
                "for d in ['/tls-client','/tls-server'] if Path(d).exists() "
                "for p in Path(d).iterdir())",
            )
        version = healthy()
        rejected = probe("expired-client")
        assert rejected.returncode != 0 and rejected.stderr
        deadline = time.monotonic() + 5
        while "certificate has expired" not in logs(gateway).lower():
            assert time.monotonic() < deadline, "Gateway did not report client certificate expiry"
            time.sleep(0.1)
        replace("expired-server", "old")
        rejected = probe()
        assert rejected.returncode != 0 and "expired" in rejected.stderr.lower()
        replace("renewed-server", "old")
        assert healthy() == version
        replace("new-server", "both")
        assert healthy("old-client", "both") == version
        assert healthy("new-client", "both") == version
        replace("new-server", "new")
        assert healthy("new-client", "new") == version
        rejected = probe("old-client", "new")
        assert rejected.returncode != 0 and rejected.stderr
        deadline = time.monotonic() + 5
        while "unable to get local issuer certificate" not in logs(gateway).lower():
            assert time.monotonic() < deadline, "Removed client CA was not rejected by gateway"
            time.sleep(0.1)
        rejected = probe("new-client", "old")
        assert rejected.returncode != 0 and "unknown authority" in rejected.stderr.lower()
        assert not re.search(r"\bPOST\b", logs(proxy)), (
            "Certificate checks issued a Docker mutation"
        )
        request.node.user_properties.append(
            (
                "result",
                json.dumps(
                    {
                        "server_version": version,
                        "expired_client_rejected": True,
                        "expired_server_rejected": True,
                        "same_ca_leaf_renewal": True,
                        "overlap_accepts_old_and_new_clients": True,
                        "withdrawn_client_ca_rejected": True,
                        "stale_server_ca_rejected": True,
                        "new_ca_client_works": True,
                        "docker_mutations": 0,
                        "raw_socket_only_in_proxy": True,
                        "ca_private_keys_not_in_runtime_volumes": True,
                    },
                    sort_keys=True,
                ),
            )
        )
    finally:
        for name in reversed(containers):
            docker("rm", "--force", "--volumes", name, check=False)
        for volume in volumes.values():
            docker("volume", "rm", volume, check=False)
        docker("network", "rm", scope, check=False)
        assert not docker(
            "ps", "--all", "--quiet", "--filter", "label=nexus.studio.validation=" + scope
        ).stdout.strip()
        for volume in volumes.values():
            result = docker("volume", "inspect", volume, check=False)
            assert result.returncode != 0 and "no such volume" in result.stderr.lower()
        result = docker("network", "inspect", scope, check=False)
        assert result.returncode != 0 and "not found" in result.stderr.lower()
        request.node.user_properties.append(("scope_resources_removed", "true"))
