"""Opt-in local Linux fixture for the deployed restart consumer; no pulls/skips.

Requires NEXUS_ACCEPTANCE_RESTART_IMAGE, NEXUS_ACCEPTANCE_WORKER_IMAGE and
NEXUS_ACCEPTANCE_DOCKER_CONTEXT. Only the trusted disposable proxy receives the
real daemon socket. The controller and workers never receive its private ledger.
"""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from nexus.core.studio_container_recovery import launch_environment


@pytest.mark.parametrize("mode", ["restart", "reply-loss"])
def test_worker_recovery_over_real_filtered_transport(tmp_path, mode, request):
    image = os.environ["NEXUS_ACCEPTANCE_RESTART_IMAGE"]
    worker = os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"]
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    assert all(re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in (image, worker))
    environment = launch_environment(context)
    prefix = "nexus-controlled-restart-" + uuid.uuid4().hex[:12]
    volumes = {kind: prefix + "-" + kind for kind in ["private", "socket", "data", "config"]}
    containers = []
    owner = None
    source = (Path(__file__).parent / "studio_deployed_restart_acceptance.py").read_text()

    def docker(*args, timeout=20, check=True):
        result = subprocess.run(
            ["docker", "--context", context, *args],
            capture_output=True,
            text=True,
            env=environment,
            timeout=timeout,
        )
        if check:
            assert result.returncode == 0, "Disposable Docker operation failed"
        return result

    def live(identity):
        result = docker(
            "container", "inspect", "--format", "{{.State.Running}}", identity, check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def start(role):
        name = prefix + "-" + role
        containers.append(name)
        command = [
            "run",
            "--detach",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "nexus.studio.validation=" + prefix,
            "--read-only",
            "--network",
            "none",
            "--user",
            "1001:1001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "1024m",
            "--pids-limit",
            "512",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=128m",
            "--mount",
            "type=volume,source="
            + volumes["socket"]
            + ",target=/var/lib/nexus-studio/docker-proxy",
            "--mount",
            "type=volume,source=" + volumes["config"] + ",target=/etc/nexus-studio,readonly",
        ]
        if role == "proxy":
            command += [
                "--group-add",
                "0",
                "--env",
                "PROXY_STATE_DIR=/var/lib/nexus-proxy-private",
                "--mount",
                "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly",
                "--mount",
                "type=volume,source=" + volumes["private"] + ",target=/var/lib/nexus-proxy-private",
                "--entrypoint",
                "python",
                image,
                "-I",
                "/checks/docker_proxy.py",
            ]
        else:
            command += [
                "--env",
                "DOCKER_HOST=unix:///var/lib/nexus-studio/docker-proxy/docker.sock",
                "--env",
                "DOCKER_CONFIG=/tmp/docker-config",
                "--env",
                "TMPDIR=/validation/tmp",
                "--env",
                "NEXUS_ACCEPTANCE_WORKER_IMAGE=" + worker,
                "--env",
                "NEXUS_VALIDATION_SCOPE=" + prefix,
                "--mount",
                "type=volume,source=" + volumes["data"] + ",target=/validation",
                image,
                "--dir",
                "/validation/project",
                "--state-dir",
                "/validation/private",
                "--runtime-config",
                "/etc/nexus-studio/studio-runtime.json",
            ]
        docker(*command)
        return name

    try:
        for volume in volumes.values():
            docker("volume", "create", "--label", "nexus.studio.validation=" + prefix, volume)
        runtime = {
            "analysis_workers": {
                name: {"image": worker, "timeout": 60, "pids_limit": 256, "open_files": 256}
                for name in ["data", "python", "media"]
            }
        }
        setup = (
            "import json\nfrom pathlib import Path\n"
            "for name,value in "
            + repr([("runtime", runtime), ("team", {"project_key": prefix})])
            + ":\n p=Path('/etc/nexus-studio/studio-'+name+'.json'); "
            "p.write_text(json.dumps(value)); p.chmod(0o600)\n"
        )
        docker(
            "run",
            "--rm",
            "--pull",
            "never",
            "--name",
            prefix + "-config",
            "--label",
            "nexus.studio.validation=" + prefix,
            "--read-only",
            "--network",
            "none",
            "--user",
            "1001:1001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            "type=volume,source=" + volumes["config"] + ",target=/etc/nexus-studio",
            "--entrypoint",
            "python",
            image,
            "-c",
            setup,
        )
        proxy, controller = start("proxy"), start("controller")
        deadline = time.monotonic() + 35
        health = "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:5003/api/auth/status',timeout=2).status==200"
        while docker("exec", controller, "python", "-c", health, check=False).returncode:
            assert live(controller) and live(proxy), "Disposable services exited early"
            assert time.monotonic() < deadline, "Disposable Studio did not become ready"
            time.sleep(0.1)
        if mode == "reply-loss":
            gateway = (Path(__file__).parent / "fixtures/studio_docker_reply_loss.py").read_text()
            consumer = (
                Path(__file__).parent / "studio_deployed_reply_loss_acceptance.py"
            ).read_text()
            program = (
                'import json\ngateway={"__name__":"reply_gateway"}\nexec('
                + repr(gateway)
                + ',gateway)\nconsumer={"__name__":"reply_consumer"}\nexec('
                + repr(consumer)
                + ',consumer)\nprint(json.dumps(consumer["exercise"](gateway["ReplyLossProxy"])))\n'
            )
            result = subprocess.run(
                ["docker", "--context", context, "exec", "-i", controller, "python", "-"],
                input=program,
                capture_output=True,
                text=True,
                env=environment,
                timeout=60,
            )
            assert result.returncode == 0, result.stderr
            report = json.loads(result.stdout)
            assert report["lost_create_replies"] == report["lost_delete_replies"] == 1
            assert report["final_absence_confirmed"] and not report["generated_program_started"]
            request.node.user_properties.append(("result", json.dumps(report, sort_keys=True)))
            return
        docker("exec", controller, "mkdir", "/validation/case")
        prepare = (
            'namespace={"__name__":"restart_fixture"}\nexec('
            + repr(source)
            + ',namespace)\nnamespace["prepare"]("/validation/case")\n'
        )
        with (tmp_path / "owner.log").open("w") as log:
            owner = subprocess.Popen(
                ["docker", "--context", context, "exec", "-i", controller, "python", "-"],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                text=True,
                env=environment,
            )
            owner.stdin.write(prepare)
            owner.stdin.close()
            deadline = time.monotonic() + 40
            receipt = None
            read = (
                "from pathlib import Path; print(Path('/validation/case/ready.json').read_text())"
            )
            while time.monotonic() < deadline:
                assert owner.poll() is None, "Disposable owner exited before restart"
                ready = docker("exec", controller, "python", "-c", read, check=False)
                if ready.returncode == 0:
                    candidate = json.loads(ready.stdout)
                    if candidate.get("running_id"):
                        marker = docker(
                            "exec",
                            candidate["running_id"],
                            "/bin/sh",
                            "-c",
                            "test -f /work/outputs/ready",
                            check=False,
                        )
                        if marker.returncode == 0:
                            receipt = candidate
                            break
                time.sleep(0.1)
            assert receipt and not receipt["owner_finished"]
            assert live(receipt["running_id"])
            docker("stop", "--time", "30", controller, timeout=40)
            assert (
                docker(
                    "container", "inspect", "--format", "{{.State.ExitCode}}", controller
                ).stdout.strip()
                == "0"
            )
            deadline = time.monotonic() + 10
            while live(receipt["running_id"]):
                assert time.monotonic() < deadline, "Worker namespace survived controller exit"
                time.sleep(0.05)
            docker("stop", "--time", "10", proxy)
            assert (
                docker(
                    "container", "inspect", "--format", "{{.State.ExitCode}}", proxy
                ).stdout.strip()
                == "0"
            )
            docker("start", proxy)
            docker("start", controller)
            verify = (
                'import json\nnamespace={"__name__":"restart_fixture"}\nexec('
                + repr(source)
                + ',namespace)\nprint(json.dumps(namespace["verify"]("/validation/case")))\n'
            )
            result = subprocess.run(
                ["docker", "--context", context, "exec", "-i", controller, "python", "-"],
                input=verify,
                capture_output=True,
                text=True,
                env=environment,
                timeout=40,
            )
            assert result.returncode == 0, result.stderr
            report = json.loads(result.stdout)
            assert report["restored_start_denied"] and report["running_and_stopped_records_removed"]
            assert not report["recovery_replayed_create_or_start"]
            assert live(controller) and live(proxy)
            request.node.user_properties.append(("result", json.dumps(report, sort_keys=True)))
    finally:
        # The fixture's scope label exists on every synthetic worker and service.
        # Remove only those exact IDs; unrelated Docker resources are untouched.
        found = docker(
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            "label=nexus.studio.validation=" + prefix,
            check=False,
        )
        for identity in found.stdout.splitlines():
            if re.fullmatch(r"[a-f0-9]{64}", identity):
                docker("rm", "--force", identity, check=False)
        for name in containers:
            docker("rm", "--force", name, check=False)
        if owner:
            try:
                owner.wait(10)
            except subprocess.TimeoutExpired:
                owner.kill()
                owner.wait(5)
        for volume in volumes.values():
            docker("volume", "rm", volume, check=False)
        remaining = docker(
            "ps", "--all", "--quiet", "--filter", "label=nexus.studio.validation=" + prefix
        )
        assert not remaining.stdout.strip(), "Fixture container cleanup remains unconfirmed"
        for volume in volumes.values():
            inspected = docker("volume", "inspect", volume, check=False)
            assert inspected.returncode != 0 and "no such volume" in inspected.stderr.lower(), (
                "Fixture volume cleanup remains unconfirmed"
            )
        request.node.user_properties.append(("scope_resources_removed", "true"))
