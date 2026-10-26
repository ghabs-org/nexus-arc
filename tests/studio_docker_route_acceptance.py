"""Explicit actual local context-reconfiguration races; no implicit pulls/skips."""

import hashlib
import json
import os
import runpy
import subprocess
from pathlib import Path

import pytest

from nexus.core.studio_container_recovery import launch_environment
from nexus.core.studio_docker_route import pin_route


@pytest.mark.parametrize("mode", ["dispatch", "selection", "recovery"])
def test_context_change_cannot_redirect_dispatch_or_cleanup(mode):
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_docker_route.py")
    )
    assert example["exercise"](
        os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"],
        os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"],
        mode,
    ) == {
        "mode": mode,
        "context_changed_once": True,
        "original_daemon_used": True,
        "pending_cleanup": 0,
        "external_accounts_used": False,
        "images_pulled": False,
    }


@pytest.mark.parametrize("transport", ["tls", "ssh"])
def test_actual_cli_snapshot_retains_transport_settings_without_contacting_remote(
    tmp_path, transport
):
    config = tmp_path / "operator"
    config.mkdir(mode=0o700)
    (config / "config.json").write_text(
        json.dumps(
            {
                "auths": {"isolated.test": {"auth": "test-registry-sentinel"}},
                "HttpHeaders": {"X-Nexus-Route": "test-daemon-header"},
            }
        )
    )
    environment = {**launch_environment("selected"), "DOCKER_CONFIG": str(config)}
    command = ["docker", "--context", "selected"]
    endpoint = "host=ssh://nexus-route-test.invalid:2222"
    if transport == "tls":
        key, certificate = tmp_path / "key.pem", tmp_path / "ca.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=nexus-route-check",
                "-keyout",
                str(key),
                "-out",
                str(certificate),
            ],
            capture_output=True,
            timeout=15,
            check=True,
        )
        key.chmod(0o600)
        endpoint = f"host=tcp://127.0.0.1:1,ca={certificate},cert={certificate},key={key}"
    subprocess.run(
        [
            "docker",
            "--config",
            str(config),
            "context",
            "create",
            "selected",
            "--docker",
            endpoint,
        ],
        env=environment,
        capture_output=True,
        timeout=5,
        check=True,
    )
    with pin_route(command, environment, subprocess.run) as route:
        private = Path(route.environment["DOCKER_CONFIG"])
        assert json.loads((private / "config.json").read_text())["HttpHeaders"] == {
            "X-Nexus-Route": "test-daemon-header",
        }
        copied = subprocess.run(
            [
                *route.command,
                "context",
                "inspect",
            ],
            env=route.environment,
            capture_output=True,
            timeout=5,
            check=True,
        )
        record = json.loads(copied.stdout)[0]
        if transport == "tls":
            snapshot_key = Path(record["Storage"]["TLSPath"]) / "docker/key.pem"
            assert snapshot_key.is_relative_to(private)
            assert (
                hashlib.sha256(snapshot_key.read_bytes()).digest()
                == hashlib.sha256(key.read_bytes()).digest()
            )
            assert snapshot_key.stat().st_mode & 0o777 == 0o600
            assert not record["Endpoints"]["docker"]["SkipTLSVerify"]
        else:
            assert record["Endpoints"]["docker"]["Host"] == "ssh://nexus-route-test.invalid:2222"
        for path in private.rglob("*"):
            if path.is_file():
                assert b"test-registry-sentinel" not in path.read_bytes()
    assert not private.exists()
