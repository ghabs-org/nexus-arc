"""Explicit real Docker process-lifetime acceptance; no prerequisite skips or pulls."""

import os
import runpy
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from nexus.core.studio_container_recovery import launch_environment
from nexus.core.studio_sandbox import StudioSandbox


@pytest.mark.parametrize("mode", ["close", "kill", "group-kill"])
def test_owned_container_stops_with_studio_or_its_process_group(mode):
    root = Path(__file__).resolve().parents[1]
    example = runpy.run_path(str(root / "examples/nexus-bot/src/studio_docker_lifecycle.py"))
    result = example["exercise"](
        os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"],
        os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"],
        mode,
    )
    assert result["container_removed"] and result["child_in_container_stopped"]
    assert result["restart_cleanup_confirmed"]
    assert result["seconds"] < 8 and not result["image_pulled"] and not result["live_accounts_used"]


def test_durable_recovery_handles_unreachable_daemon_and_late_create():
    root = Path(__file__).resolve().parents[1]
    example = runpy.run_path(str(root / "examples/nexus-bot/src/studio_docker_lifecycle.py"))
    result = example["exercise_recovery"](
        os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"],
        os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"],
    )
    assert result["normal_execution"] and result["late_create_never_started"]
    assert result["restart_recovery"] and result["actual_unreachable_transport"]
    assert result["container_removed"] and not result["replayed_work"]
    assert not result["image_pulled_by_worker"]


def test_started_container_without_owner_input_exits_within_initial_deadline():
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    sandbox = StudioSandbox(
        {
            "python": {
                "image": os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"],
                "docker_context": context,
            }
        }
    )
    docker = ["docker", "--context", context]
    environment = launch_environment(context)
    name = "nexus-analysis-" + uuid.uuid4().hex
    command = sandbox.arguments("python", name)
    command[len(docker)] = "create"
    client = None
    try:
        created = subprocess.run(command, capture_output=True, env=environment, timeout=15)
        assert created.returncode == 0, created.stderr.decode()[-1000:]
        started = time.monotonic()
        client = subprocess.Popen(
            [*docker, "start", "--attach", "--interactive", name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        # Keep the owner's pipe open but send no frame, like a broken late attach.
        assert client.wait(22) == 124
        assert time.monotonic() - started < 22
        absent = subprocess.run(
            [*docker, "ps", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"],
            capture_output=True,
            env=environment,
            timeout=2,
        )
        assert absent.returncode == 0 and not absent.stdout.strip()
    finally:
        if client:
            if client.poll() is None:
                client.kill()
            client.communicate(timeout=3)
        subprocess.run(
            [*docker, "rm", "--force", name],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
