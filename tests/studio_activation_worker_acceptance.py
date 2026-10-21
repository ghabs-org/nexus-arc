"""Opt-in real activation worker probes; no user workflow action executes."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from nexus.core.studio import StudioService
from tests.test_studio_activation_setup import worker_build
from tests.test_studio_build_lifecycle import apply, plan


@pytest.mark.parametrize(
    "action",
    [
        "python",
        "query_sql",
        "create_database",
        "transform_jq",
        "chart",
        "imagemagick",
        "detect_objects",
    ],
)
def test_real_activation_checks_each_worker_without_running_actions(tmp_path, action):
    settings = {
        "image": os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"],
        "docker_context": os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"],
        "pids_limit": 256,
        "open_files": 256,
    }
    profiles = {name: dict(settings) for name in ["python", "data", "media"]}
    profiles["vision"] = {
        **settings,
        "image": os.environ["NEXUS_ACCEPTANCE_VISION_IMAGE"],
        "model_path": os.environ["NEXUS_ACCEPTANCE_VISION_MODEL"],
        "memory_mb": 2048,
    }
    studio = StudioService(tmp_path, data_tool_options={"profiles": profiles})
    try:
        build = worker_build(studio, action)
        passive = studio.resource_lifecycle.inspect_build(build["id"])
        assert any(c["prerequisite_checks"] for c in passive["components"])
        activation = plan(studio, build, "activate")
        assert not activation["errors"], activation
        assert apply(studio, build, activation)["status"] == "active"
        assert not studio.records("runs")
    finally:
        studio.close()


@pytest.mark.parametrize("case,action", [("absent", "python"), ("software", "chart")])
def test_absent_image_or_worker_software_blocks_activation(tmp_path, case, action):
    image = "sha256:" + "f" * 64 if case == "absent" else os.environ["NEXUS_ACCEPTANCE_BARE_IMAGE"]
    profiles = {
        name: {"image": image, "docker_context": os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]}
        for name in ["python", "data"]
    }
    studio = StudioService(tmp_path, data_tool_options={"profiles": profiles})
    try:
        if case == "software":
            sandbox = studio.data_tools.sandbox
            result = sandbox.run_command(
                [*sandbox.docker_command(profiles["data"]), "image", "inspect", image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            assert result.returncode == 0, "Install the explicitly selected bare test image first"
        build = worker_build(studio, action)
        activation = plan(studio, build, "activate")
        assert any(
            "worker_prerequisites_missing:" in item["reason"] for item in activation["errors"]
        )
        with pytest.raises(ValueError, match="readiness"):
            apply(studio, build, activation)
        assert studio.builder.get(build["id"]) == build
    finally:
        studio.close()


def test_framework_example_uses_real_worker_preflight():
    path = Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_activation_setup.py"
    spec = importlib.util.spec_from_file_location("activation_example", path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    assert (
        example.run_example(
            os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"],
            os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"],
        )["status"]
        == "active"
    )
