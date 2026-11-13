"""Opt-in actual Linux cancellation, shutdown, timeout and owner-death gate."""

import json
import os
import re
import subprocess
import uuid

from nexus.core.studio_container_recovery import launch_environment


def test_linux_speech_detached_descendants_stop_with_owner(request):
    image = os.environ["NEXUS_ACCEPTANCE_SPEECH_LIFECYCLE_IMAGE"]
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    environment = launch_environment(context)
    command = ["docker", "--context", context]
    name = "nexus-speech-lifetime-" + uuid.uuid4().hex
    try:
        result = subprocess.run(
            [
                *command,
                "run",
                "--rm",
                "--pull",
                "never",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--user",
                "65534:65534",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "512m",
                "--pids-limit",
                "128",
                "--cpus",
                "2",
                "--tmpfs",
                "/tmp:rw,exec,nosuid,nodev,size=64m,mode=1777",
                image,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr[-3000:]
        report = json.loads(result.stdout)
        assert set(report) == {"cancel", "shutdown", "timeout", "owner_death", "finite_input"}
        assert all(
            report[case]["detached_child_removed"]
            for case in ("cancel", "shutdown", "timeout", "owner_death")
        )
        assert report["finite_input"]["bytes_and_errors_preserved"]
        request.node.user_properties.append(("result", json.dumps(report, sort_keys=True)))
    finally:
        subprocess.run(
            [*command, "rm", "--force", name], env=environment, capture_output=True, timeout=10
        )
        result = subprocess.run(
            [*command, "container", "inspect", name],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0 and "no such" in result.stderr.lower()
