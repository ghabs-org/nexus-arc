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


@pytest.mark.parametrize("context", ["", "../remote", "tcp://host:2375", "--host=remote", 42])
def test_worker_rejects_invalid_docker_contexts(context):
    with pytest.raises(ValueError, match="context"):
        StudioSandbox({"data": {"image": IMAGE, "docker_context": context}})


def test_selected_context_routes_inspection_and_execution(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr("nexus.core.studio_sandbox.shutil.which", lambda _: "/bin/docker")

    def inspect(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1)

    sandbox = StudioSandbox(
        {"data": {"image": IMAGE, "docker_context": "desktop-linux"}}, run=inspect
    )
    assert sandbox.arguments("data", "owned")[:4] == ["docker", "--context", "desktop-linux", "run"]
    with pytest.raises(ValueError, match="not installed"):
        sandbox.execute("print(1)", profile="data")
    assert calls == [["docker", "--context", "desktop-linux", "image", "inspect", IMAGE]]


@pytest.mark.parametrize(
    "key,value",
    [
        ("pids_limit", 0),
        ("pids_limit", 513),
        ("pids_limit", True),
        ("open_files", 63),
        ("open_files", 1025),
        ("open_files", False),
    ],
)
def test_worker_resource_overrides_stay_bounded(key, value):
    with pytest.raises(ValueError, match="limit"):
        StudioSandbox({"data": {"image": IMAGE, key: value}})


def test_chart_profile_preserves_isolation_with_explicit_bounded_resources():
    sandbox = StudioSandbox({"data": {"image": IMAGE, "pids_limit": 256, "open_files": 256}})
    args = sandbox.arguments("data", "chart")
    assert args[args.index("--pids-limit") + 1] == "256"
    assert args[args.index("--network") + 1] == "none"
    assert "--read-only" in args and "--mount" not in args


def test_model_mount_rechecks_replaced_symlink(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"selected")
    sandbox = StudioSandbox({"vision": {"image": IMAGE, "model_path": str(model)}})
    args = sandbox.arguments("vision", "owned")
    assert args[args.index("--mount") + 1].endswith("destination=/models/model.pt,readonly")
    model.unlink()
    model.symlink_to(tmp_path / "other.pt")
    with pytest.raises(ValueError, match="ordinary"):
        sandbox.arguments("vision", "owned")
