import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nexus.core.studio_data_tools import DATA_PROGRAM, IMAGE_PROGRAM
from nexus.core.studio_sandbox import WRAPPER, StudioSandbox, _OwnedContainer

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
        if "context" in command and "inspect" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "desktop-linux",
                            "Endpoints": {"docker": {"Host": "unix:///fixture.sock"}},
                        }
                    ]
                ).encode(),
            )
        if "export" in command:
            return SimpleNamespace(returncode=0, stdout=b"context archive")
        return SimpleNamespace(returncode=int("image" in command))

    sandbox = StudioSandbox(
        {"data": {"image": IMAGE, "docker_context": "desktop-linux"}}, run=inspect
    )
    assert sandbox.arguments("data", "owned")[:4] == ["docker", "--context", "desktop-linux", "run"]
    with pytest.raises(ValueError, match="not installed"):
        sandbox.execute("print(1)", profile="data")
    assert calls[0] == ["docker", "--context", "desktop-linux", "context", "inspect"]
    image = next(command for command in calls if "image" in command)
    assert image[:2] == ["docker", "--config"]
    assert image[3:] == ["--context", "desktop-linux", "image", "inspect", IMAGE]


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


def test_closed_worker_rejects_dispatch_before_any_docker_command(monkeypatch):
    command = Mock(side_effect=AssertionError("Must not call Docker after close"))
    sandbox = StudioSandbox({"python": {"image": IMAGE}}, run=command)
    assert sandbox.close() and sandbox.close()
    with pytest.raises(RuntimeError, match="shutting down"):
        sandbox.execute("print(1)")
    assert not command.called


def test_close_during_image_inspection_prevents_late_container_launch(monkeypatch):
    monkeypatch.setattr("nexus.core.studio_sandbox.shutil.which", lambda _: "/bin/docker")
    launch = Mock(side_effect=AssertionError("Must not start a late container"))
    monkeypatch.setattr("nexus.core.studio_sandbox.subprocess.Popen", launch)

    def inspect(command, **kwargs):
        if "image" in command:
            sandbox.close(wait=False)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Name": "default",
                        "Endpoints": {"docker": {"Host": "unix:///fixture.sock"}},
                    }
                ]
            ).encode(),
        )

    sandbox = StudioSandbox({"python": {"image": IMAGE}}, run=inspect)
    with pytest.raises(RuntimeError, match="shutting down"):
        sandbox.execute("print(1)")
    assert not launch.called


@pytest.mark.parametrize("daemon_available", [False, True])
def test_cleanup_confirms_absence_on_the_launch_context_without_sweeping_others(daemon_available):
    calls = []
    sandbox = StudioSandbox({"python": {"image": IMAGE, "docker_context": "changed"}})
    process = SimpleNamespace(poll=lambda: None, kill=Mock())

    def command(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] <= 5
        if "rm" in args:
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0 if daemon_available else 1, stdout=b"")

    worker = _OwnedContainer(
        process,
        ["docker", "--context", "original"],
        "nexus-analysis-owned",
        command,
        sandbox._report_cleanup,
    )
    sandbox._active[worker.name] = worker
    assert sandbox.close() is daemon_available
    assert process.kill.call_count == 1 and worker.cancelled.is_set()
    assert calls == [
        ["docker", "--context", "original", "rm", "--force", worker.name],
        [
            "docker",
            "--context",
            "original",
            "ps",
            "--all",
            "--filter",
            "name=^/nexus-analysis-owned$",
            "--format",
            "{{.ID}}",
        ],
    ]
    assert bool(sandbox.cleanup_errors) is not daemon_available
    sandbox.close()
    assert process.kill.call_count == 1


def test_cleanup_timeout_is_unconfirmed_and_never_replays_work():
    sandbox = StudioSandbox()
    process = SimpleNamespace(poll=lambda: None, kill=Mock())
    command = Mock(side_effect=subprocess.TimeoutExpired("docker", 5))
    worker = _OwnedContainer(
        process, ["docker"], "nexus-analysis-owned", command, sandbox._report_cleanup
    )
    sandbox._active[worker.name] = worker
    assert not sandbox.close()
    assert worker.done.is_set() and sandbox.cleanup_errors == {worker.name}
    assert command.call_count == 1


@pytest.mark.parametrize("explicit", [False, True])
def test_explicit_default_context_cannot_be_redirected_by_ambient_host(monkeypatch, explicit):
    monkeypatch.setenv("DOCKER_HOST", "tcp://unselected.invalid:2375")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    monkeypatch.setattr("nexus.core.studio_sandbox.shutil.which", lambda _: "/bin/docker")
    seen = []

    def inspect(command, **kwargs):
        seen.append((command, kwargs["env"]))
        if "context" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "Name": "default",
                            "Endpoints": {
                                "docker": {
                                    "Host": kwargs["env"].get(
                                        "DOCKER_HOST", "unix:///fixture.sock"
                                    ),
                                }
                            },
                        }
                    ]
                ).encode(),
            )
        return SimpleNamespace(returncode=1)

    profile = {"image": IMAGE, **({"docker_context": "default"} if explicit else {})}
    sandbox = StudioSandbox({"python": profile}, run=inspect)
    with pytest.raises(ValueError, match="not installed"):
        sandbox.execute("print(1)")
    assert len(seen) == 2
    assert seen[1][0][1] == "--host"
    environment = seen[1][1]
    if explicit:
        assert seen[0][0][:3] == ["docker", "--context", "default"]
        assert "DOCKER_HOST" not in environment and "DOCKER_TLS_VERIFY" not in environment
    else:
        assert environment["DOCKER_HOST"] == "tcp://unselected.invalid:2375"
        assert environment["DOCKER_TLS_VERIFY"] == "1"
    assert os.environ["DOCKER_HOST"] == "tcp://unselected.invalid:2375"
