"""Host preflight reports facts without exposing Docker/configuration data."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from nexus.core.studio_host_checks import INSPECT_FORMAT, verify_proxy_layout


@pytest.fixture
def deployment(tmp_path):
    private, public = tmp_path / "private", tmp_path / "public"
    private.mkdir(mode=0o700)
    public.mkdir()
    raw_socket = tmp_path / "daemon.sock"
    raw_socket.touch()
    ledger = private / "ownership.sqlite"
    ledger.write_text("LEDGER_SECRET_SENTINEL")
    ledger.chmod(0o600)

    def mount(source, target, *, writable=True):
        return {"Type": "bind", "Source": str(source), "Destination": target, "RW": writable}

    studio = {
        "id": "a" * 64,
        "running": True,
        "privileged": False,
        "mounts": [mount(public, "/var/lib/nexus-studio")],
    }
    proxy = {
        "id": "b" * 64,
        "running": True,
        "privileged": False,
        "mounts": [
            mount(private, "/var/lib/nexus-proxy-private"),
            mount(raw_socket, "/var/run/docker.sock", writable=False),
        ],
    }
    metadata = {
        "uid": 1001,
        "gid": 1001,
        "directory": True,
        "directory_owner": True,
        "directory_mode": True,
        "ledger": "present",
        "ledger_private": True,
        "socket_access": True,
    }
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-2:] == ["context", "inspect"]:
            value = [
                {"Name": "default", "Endpoints": {"docker": {"Host": "unix:///test-only.sock"}}}
            ]
        elif "container" in command:
            value = studio if command[-1] == "studio-fixture" else proxy
            assert command[-3] == "--format" and command[-2] == INSPECT_FORMAT
        else:
            assert command[3:5] == ["exec", proxy["id"]]
            value = metadata
        return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())

    def verify():
        return verify_proxy_layout(
            "studio-fixture", "proxy-fixture", docker_context="default", run=run
        )

    return SimpleNamespace(
        private=private,
        public=public,
        raw_socket=raw_socket,
        studio=studio,
        proxy=proxy,
        metadata=metadata,
        calls=calls,
        run=run,
        verify=verify,
        mount=mount,
    )


def statuses(report):
    return {item["check"]: item["status"] for item in report["checks"]}


def test_safe_pair_passes_with_no_environment_logs_paths_or_ledger_contents(
    deployment, monkeypatch
):
    monkeypatch.setenv("DOCKER_HOST", "tcp://secret-ambient-host:2375")
    report = deployment.verify()
    assert report["passed"] and report["ledger"] == "present"
    rendered = json.dumps(report)
    for sentinel in (
        str(deployment.private.parent),
        "studio-fixture",
        "proxy-fixture",
        "LEDGER_SECRET_SENTINEL",
        "secret-ambient-host",
    ):
        assert sentinel not in rendered
    assert report["targets"]["studio"] != report["targets"]["proxy"]
    assert all("DOCKER_HOST" not in kwargs["env"] for _, kwargs in deployment.calls)
    assert all(
        ".Env" not in str(command) and "logs" not in command for command, _ in deployment.calls
    )
    assert deployment.calls[-1][0][3:5] == ["exec", "b" * 64]


@pytest.mark.parametrize(
    "exposure", ["private", "parent", "child", "symlink", "socket", "hardlink"]
)
def test_alias_parent_child_and_readonly_exposures_fail(deployment, exposure):
    source = deployment.private
    if exposure == "parent":
        source = source.parent
    elif exposure == "child":
        source = source / "nested"
        source.mkdir()
    elif exposure == "symlink":
        source = deployment.private.parent / "alias"
        source.symlink_to(deployment.private, target_is_directory=True)
    elif exposure == "socket":
        source = deployment.raw_socket
    elif exposure == "hardlink":
        source = deployment.raw_socket.with_name("socket-alias")
        os.link(deployment.raw_socket, source)
    deployment.studio["mounts"].append(deployment.mount(source, "/exposed", writable=False))
    report = deployment.verify()
    assert not report["passed"]
    key = (
        "raw-socket-isolation" if exposure in {"socket", "hardlink"} else "private-source-isolation"
    )
    assert statuses(report)[key] == "failed"


def test_missing_host_source_is_unavailable_not_string_based_success(deployment):
    deployment.studio["mounts"][0]["Source"] = "/host-not-visible-on-this-machine/data"
    report = deployment.verify()
    assert not report["passed"]
    assert all(
        statuses(report)[key] == "unavailable"
        for key in (
            "host-source-identity",
            "private-source-isolation",
            "raw-socket-isolation",
        )
    )


@pytest.mark.parametrize("missing", ["private", "socket"])
def test_unconfigured_optional_store_does_not_hide_other_source_checks(deployment, missing):
    deployment.proxy["mounts"].pop(0 if missing == "private" else 1)
    if missing == "private":
        deployment.metadata.update(
            directory=False,
            directory_owner=False,
            directory_mode=False,
            ledger="unavailable",
            ledger_private=False,
        )
    result = deployment.verify()
    values = statuses(result)
    assert not result["passed"]
    assert values["host-source-identity"] == "unavailable"
    verified = "raw-socket-isolation" if missing == "private" else "private-source-isolation"
    unknown = "private-source-isolation" if missing == "private" else "raw-socket-isolation"
    assert values[verified] == "passed" and values[unknown] == "unavailable"
    assert "runtime-inspection" not in values


@pytest.mark.parametrize(
    "change",
    [
        "same-container",
        "stopped",
        "privileged",
        "readonly-state",
        "parent-state",
        "shadowed-ledger",
        "root-user",
        "directory",
        "mode",
        "ledger",
        "socket",
    ],
)
def test_unsafe_layout_or_actual_proxy_permissions_never_pass(deployment, change):
    if change == "same-container":
        deployment.studio["id"] = deployment.proxy["id"]
    elif change == "stopped":
        deployment.proxy["running"] = False
    elif change == "privileged":
        deployment.studio["privileged"] = True
    elif change == "readonly-state":
        deployment.proxy["mounts"][0]["RW"] = False
    elif change == "parent-state":
        deployment.proxy["mounts"][0]["Destination"] = "/var/lib"
    elif change == "shadowed-ledger":
        exposed = deployment.public / "replacement.sqlite"
        exposed.touch()
        deployment.proxy["mounts"].append(
            deployment.mount(exposed, "/var/lib/nexus-proxy-private/ownership.sqlite")
        )
    else:
        key = {
            "root-user": "uid",
            "directory": "directory_owner",
            "mode": "directory_mode",
            "ledger": "ledger_private",
            "socket": "socket_access",
        }[change]
        deployment.metadata[key] = 0 if change == "root-user" else False
    assert not deployment.verify()["passed"]


def test_absent_ledger_is_explicitly_configured_without_live_recovery_claim(deployment):
    deployment.metadata["ledger"] = "absent"
    report = deployment.verify()
    assert report["passed"] and report["ledger"] == "absent"
    assert "not recovery proof" in next(
        item["detail"] for item in report["checks"] if item["check"] == "private-ledger"
    )


@pytest.mark.parametrize("fault", ["timeout", "response", "large", "stderr", "metadata"])
def test_failure_details_never_escape_into_report(deployment, fault):
    def run(command, **kwargs):
        if "container" not in command:
            return deployment.run(command, **kwargs)
        if fault == "timeout":
            raise subprocess.TimeoutExpired("SECRET_COMMAND", 5, stderr=b"SECRET_STDERR")
        payload = (
            {"SECRET_RESPONSE": "not a container"} if fault == "metadata" else "SECRET_RESPONSE"
        )
        if fault == "large":
            payload *= 100000
        return SimpleNamespace(
            returncode=1 if fault == "stderr" else 0,
            stdout=json.dumps(payload).encode(),
            stderr=b"SECRET_STDERR",
        )

    report = verify_proxy_layout(
        "studio-fixture", "proxy-fixture", docker_context="default", run=run
    )
    assert not report["passed"] and statuses(report)["runtime-inspection"] == "unavailable"
    assert "SECRET_" not in json.dumps(report)


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("studio_container", "--help"),
        ("proxy_container", "name; touch bad"),
        ("docker_context", ""),
        ("private_path", "/private/../elsewhere"),
        ("private_path", "//private"),
        ("upstream_socket", "relative"),
    ],
)
def test_bad_inputs_fail_before_inspection(keyword, value):
    args = {
        "studio_container": "studio",
        "proxy_container": "proxy",
        "docker_context": "default",
        keyword: value,
    }
    with pytest.raises(ValueError):
        verify_proxy_layout(**args, run=lambda *a, **k: pytest.fail("Bad input reached Docker"))


def test_cli_entry_point_uses_framework_and_reports_exit_status(monkeypatch, capsys):
    from nexus import cli

    received = []

    def verify(*args, **kwargs):
        received.append((args, kwargs))
        return {"passed": False, "checks": [{"status": "unavailable"}]}

    monkeypatch.setattr("nexus.core.studio_host_checks.verify_proxy_layout", verify)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nexus",
            "studio-host-check",
            "--studio-container",
            "studio",
            "--proxy-container",
            "proxy",
            "--docker-context",
            "default",
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1 and received[0][0] == ("studio", "proxy")
    assert json.loads(capsys.readouterr().out)["passed"] is False
