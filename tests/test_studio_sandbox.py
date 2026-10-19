import json

import pytest

from nexus.core.studio_data_tools import DATA_PROGRAM, IMAGE_PROGRAM
from nexus.core.studio_sandbox import WRAPPER, StudioSandbox

IMAGE = "example.test/worker@sha256:" + "a" * 64


def test_sandbox_rejects_mutable_images_and_has_no_host_workspace_or_network_mounts():
    with pytest.raises(ValueError, match="immutable"):
        StudioSandbox({"python": {"image": "python:latest"}})
    sandbox = StudioSandbox({"python": {"image": IMAGE}})
    args = sandbox.arguments("python", "nexus-analysis-fixture")
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--user") + 1] == "65534:65534"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in args and "--pull=never" in args
    assert "--mount" not in args and "--env" not in args and "--volume" not in args
    assert "--log-driver" in args and "--pids-limit" in args


def test_worker_programs_compile_without_executing_user_content():
    compile(WRAPPER, "sandbox-wrapper", "exec")
    for program in (DATA_PROGRAM, IMAGE_PROGRAM):
        compile(
            program.replace(
                "PARAMETERS", repr(json.dumps({"filename": "input.csv", "action": "query_sql"}))
            ),
            "worker",
            "exec",
        )


def test_sandbox_has_no_implicit_host_execution_when_not_configured():
    sandbox = StudioSandbox()
    with pytest.raises(ValueError, match="Configure"):
        sandbox.arguments("python", "nexus-analysis-fixture")


def test_local_image_ids_are_immutable_and_vision_memory_stays_bounded():
    sandbox = StudioSandbox({"vision": {"image": "sha256:" + "b" * 64, "memory_mb": 2048}})
    args = sandbox.arguments("vision", "nexus-analysis-fixture")
    assert args[args.index("--memory") + 1] == "2048m"
    assert args[args.index("--memory-swap") + 1] == "2048m"
    with pytest.raises(ValueError, match="memory"):
        StudioSandbox({"vision": {"image": IMAGE, "memory_mb": 8192}})
