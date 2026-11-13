"""Explicit Docker/Linux private-mount acceptance. No implicit pulls or skips.

Needs NEXUS_ACCEPTANCE_PROXY_IMAGE, NEXUS_ACCEPTANCE_WORKER_IMAGE (image IDs),
and NEXUS_ACCEPTANCE_DOCKER_CONTEXT. The trusted proxy alone receives the real
daemon socket; controller has neither that socket nor private ledger storage.
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from nexus.core.studio_container_recovery import launch_environment

SCRIPT = "/checks/examples/nexus-bot/src/studio_proxy_linux.py"
SOCKET_DIR = "/var/lib/nexus-studio/docker-proxy"
PRIVATE_DIR = "/var/lib/nexus-proxy-private"


@pytest.mark.parametrize("mode", ["restart", "kill"])
def test_linux_private_mounts_and_proxy_restart(tmp_path, mode):
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    image, worker = (
        os.environ[key]
        for key in (
            "NEXUS_ACCEPTANCE_PROXY_IMAGE",
            "NEXUS_ACCEPTANCE_WORKER_IMAGE",
        )
    )
    assert all(re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in (image, worker))
    environment = launch_environment(context)
    prefix = "nexus-private-check-" + uuid.uuid4().hex[:12]
    volumes = {kind: prefix + "-" + kind for kind in ("private", "socket", "studio")}
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

    def start(role, mounts, *command):
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
            "--env",
            "NEXUS_VALIDATION_SCOPE=" + prefix,
            "--network",
            "none",
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
        ]
        if role == "proxy":
            # The proxy is the trusted boundary, exactly as in the deployment.
            args += [
                "--read-only",
                "--group-add",
                "0",
                "--mount",
                "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly",
            ]
        for source, target in mounts:
            args += ["--mount", f"type=volume,source={source},target={target}"]
        docker(*args, image, SCRIPT, *command)
        return name

    def live(name):
        state = json.loads(docker("inspect", name).stdout)[0]["State"]
        assert state["Running"], docker("logs", name).stdout + docker("logs", name).stderr

    try:
        for volume in volumes.values():
            docker("volume", "create", "--label", "nexus.studio.validation=" + prefix, volume)
        proxy = start(
            "proxy",
            [(volumes["private"], PRIVATE_DIR), (volumes["socket"], SOCKET_DIR)],
            "--phase",
            "serve",
            "--image",
            worker,
        )
        controller = start(
            "controller",
            [(volumes["studio"], "/var/lib/nexus-studio"), (volumes["socket"], SOCKET_DIR)],
            "--phase",
            "exercise",
            "--image",
            worker,
            "--private-volume",
            volumes["private"],
        )
        deadline = time.monotonic() + 45
        while True:
            live(proxy)
            live(controller)
            ready = docker(
                "exec",
                controller,
                "python",
                "-c",
                "from pathlib import Path; print(Path('/tmp/ready.json').read_text())",
                check=False,
            )
            if ready.returncode == 0:
                receipt = json.loads(ready.stdout)
                worker_id = receipt["container_id"]
                break
            assert time.monotonic() < deadline, "Linux controller did not reach restart gate"
            time.sleep(0.1)
        assert docker("inspect", receipt["denied_name"], check=False).returncode != 0
        proxy_info = json.loads(docker("inspect", proxy).stdout)[0]
        controller_info = json.loads(docker("inspect", controller).stdout)[0]
        assert proxy_info["Config"]["User"] == controller_info["Config"]["User"] == "1001:1001"
        assert proxy_info["HostConfig"]["ReadonlyRootfs"]
        assert not controller_info["HostConfig"]["ReadonlyRootfs"]
        assert all(
            info["HostConfig"]["NetworkMode"] == "none" for info in (proxy_info, controller_info)
        )
        assert any(m.get("Name") == volumes["private"] for m in proxy_info["Mounts"])
        assert all(m.get("Name") != volumes["private"] for m in controller_info["Mounts"])
        assert all(m["Destination"] != "/var/run/docker.sock" for m in controller_info["Mounts"])
        root = Path(__file__).resolve().parents[1]
        if mode == "restart":
            command = [
                sys.executable,
                "-m",
                "nexus.cli",
                "studio-host-check",
                "--studio-container",
                controller,
                "--proxy-container",
                proxy,
            ]
        else:
            command = [
                sys.executable,
                str(root / "examples/nexus-bot/src/studio_host_checks.py"),
                controller,
                proxy,
            ]
        checked = subprocess.run(
            [*command, "--docker-context", context],
            capture_output=True,
            text=True,
            timeout=20,
            env=environment,
        )
        assert checked.returncode in {0, 1}, checked.stderr
        layout = json.loads(checked.stdout)
        status = {item["check"]: item["status"] for item in layout["checks"]}
        assert all(
            status.get(key) == "passed"
            for key in (
                "distinct-running-containers",
                "studio-unprivileged",
                "dedicated-private-mount",
                "private-mount-unshadowed",
                "upstream-socket-mount",
                "proxy-nonroot-user",
                "upstream-socket-access",
                "private-directory",
                "private-ledger",
            )
        ), layout
        assert layout["ledger"] == "present"
        # A Docker Desktop client cannot resolve the Linux daemon's volume paths.
        # Unavailable host identity must stay explicit, not become a guessed pass.
        assert all(
            status.get(key) in {"passed", "unavailable"}
            for key in (
                "host-source-identity",
                "private-source-isolation",
                "raw-socket-isolation",
            )
        ), layout
        assert layout["passed"] == (checked.returncode == 0)
        assert controller not in checked.stdout and proxy not in checked.stdout
        (tmp_path / "host-layout.json").write_text(checked.stdout)
        if mode == "kill":
            docker("kill", "--signal", "KILL", proxy)
        else:
            docker("stop", "--time", "5", proxy)
        stopped = json.loads(docker("inspect", proxy).stdout)[0]["State"]
        assert stopped["ExitCode"] == (137 if mode == "kill" else 0), (
            "Proxy did not exit with the requested shutdown outcome",
            stopped["ExitCode"],
        )
        docker("start", proxy)
        # Readiness through the same authorized shared socket, not the host daemon.
        deadline = time.monotonic() + 20
        while True:
            live(proxy)
            checked = docker(
                "exec",
                controller,
                "docker",
                "--host",
                "unix://" + SOCKET_DIR + "/docker.sock",
                "version",
                "--format",
                "{{.Server.Version}}",
                check=False,
            )
            if checked.returncode == 0:
                break
            assert time.monotonic() < deadline, checked.stderr
            time.sleep(0.1)
        docker(
            "exec",
            controller,
            "python",
            "-c",
            "from pathlib import Path; Path('/tmp/recover-now').touch()",
        )
        assert docker("wait", controller, timeout=25).stdout.strip() == "0", docker(
            "logs", controller
        ).stderr
        output = docker("logs", controller).stdout
        line = next(
            line for line in output.splitlines() if line.startswith("NEXUS_PROXY_ACCEPTANCE ")
        )
        result = json.loads(line.removeprefix("NEXUS_PROXY_ACCEPTANCE "))
        assert result["platform"] == "Linux" and result["controller_uid"] == 1001
        assert not result["raw_socket_visible"] and not result["private_ledger_visible"]
        assert (
            result["controller_local_forgery_isolated"]
            and result["private_volume_injection_blocked"]
        )
        assert (
            result["restart_cleanup"]
            and not result["old_work_started"]
            and result["extra_runs"] == 0
        )
        assert docker("inspect", worker_id, check=False).returncode != 0
        (tmp_path / "linux-proxy-result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"mode": mode, **result}))
        print(json.dumps({"mode": mode, "host_layout": layout}))
    finally:
        if worker_id:
            docker("rm", "--force", worker_id, check=False)
        for name in reversed(containers):
            docker("rm", "--force", name, check=False)
        remaining = docker(
            "ps",
            "--all",
            "--filter",
            "label=nexus.studio.validation=" + prefix,
            "--format",
            "{{.ID}}",
            check=False,
        )
        for identity in remaining.stdout.split():
            docker("rm", "--force", identity, check=False)
        for volume in volumes.values():
            docker("volume", "rm", volume, check=False)
