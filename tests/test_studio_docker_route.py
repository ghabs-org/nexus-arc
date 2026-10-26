"""Immutable route lifetime, TLS-context privacy and legacy journal compatibility."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core.studio_docker_route import pin_route


class CLI:
    def __init__(self, *, name="remote", failure=None):
        self.failure = failure
        self.calls = []
        self.record = {
            "Name": name,
            "Metadata": {"Description": "Selected server"},
            "Endpoints": {
                "docker": {"Host": "tcp://selected.example:2376", "SkipTLSVerify": False}
            },
            "TLSMaterial": {"docker": ["ca.pem", "cert.pem", "key.pem"]},
            "Storage": {
                "MetadataPath": "/operator/contexts/meta",
                "TLSPath": "/operator/contexts/tls",
            },
        }

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        assert 0 < kwargs["timeout"] <= 2
        if "export" in args:
            return SimpleNamespace(
                returncode=int(self.failure == "export"), stdout=b"private TLS archive"
            )
        if "import" in args:
            assert kwargs["input"] == b"private TLS archive"
            key = Path(kwargs["env"]["DOCKER_CONFIG"]) / "tls/key.pem"
            key.parent.mkdir()
            key.write_text("private key")
            return SimpleNamespace(returncode=int(self.failure == "import"))
        assert args[-2:] == ["context", "inspect"]
        record = json.loads(json.dumps(self.record))
        if "--config" in args:
            record["Storage"] = {"MetadataPath": "private", "TLSPath": "private"}
            if self.failure == "changed":
                record["Endpoints"]["docker"]["Host"] = "tcp://other.example:2376"
        return SimpleNamespace(returncode=0, stdout=json.dumps([record]).encode())


def test_named_context_retains_tls_in_private_config_then_removes_it():
    cli = CLI()
    environment = {
        "HOME": "/operator",
        "DOCKER_CONFIG": "/operator/.docker",
        "UNRELATED_SECRET": "do not persist",
    }
    legacy = hashlib.sha256(
        json.dumps(
            {
                "context": cli.record,
                "environment": {
                    key: environment.get(key)
                    for key in (
                        "DOCKER_HOST",
                        "DOCKER_CONTEXT",
                        "DOCKER_TLS",
                        "DOCKER_TLS_VERIFY",
                        "DOCKER_CERT_PATH",
                        "DOCKER_CONFIG",
                        "HOME",
                        "USERPROFILE",
                    )
                },
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with pin_route(["docker", "--context", "remote"], environment, cli) as route:
        directory = Path(route.environment["DOCKER_CONFIG"])
        assert route.command == ["docker", "--config", str(directory), "--context", "remote"]
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "tls/key.pem").stat().st_mode & 0o777 == 0o600
        assert route.fingerprint == legacy
        assert "private key" not in route.fingerprint
        assert not (directory / "config.json").exists()
        assert route.environment["UNRELATED_SECRET"] == "do not persist"
    assert not directory.exists()
    assert environment["DOCKER_CONFIG"] == "/operator/.docker"


@pytest.mark.parametrize("failure", ["export", "import", "changed"])
def test_snapshot_failure_never_mutates_a_daemon_or_leaks_private_config(failure):
    cli = CLI(failure=failure)
    with pytest.raises(ValueError), pin_route(["docker", "--context", "remote"], {}, cli):
        pytest.fail("An invalid route must not reach dispatch")
    for args, kwargs in cli.calls:
        assert "context" in args
        if "--config" in args:
            assert not Path(kwargs["env"]["DOCKER_CONFIG"]).exists()


def test_private_config_is_removed_after_execution_error():
    cli = CLI()
    with pytest.raises(RuntimeError), pin_route(["docker"], {}, cli) as route:
        directory = Path(route.environment["DOCKER_CONFIG"])
        raise RuntimeError("dispatch failed")
    assert not directory.exists()


def test_snapshot_preserves_daemon_auth_headers_without_registry_or_proxy_settings(tmp_path):
    source = {
        "HttpHeaders": {"Authorization": "test-daemon-token"},
        "auths": {"private.registry": {"auth": "test-registry-token"}},
        "proxies": {"default": {"httpProxy": "http://private-proxy"}},
    }
    (tmp_path / "config.json").write_text(json.dumps(source))
    with pin_route(["docker"], {"DOCKER_CONFIG": str(tmp_path)}, CLI()) as route:
        private = Path(route.environment["DOCKER_CONFIG"]) / "config.json"
        assert json.loads(private.read_text()) == {"HttpHeaders": source["HttpHeaders"]}
        assert private.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("headers", [["invalid"], {"X-Header": 42}])
def test_invalid_daemon_headers_fail_before_context_export(tmp_path, headers):
    (tmp_path / "config.json").write_text(json.dumps({"HttpHeaders": headers}))
    cli = CLI()
    with pytest.raises(ValueError), pin_route(["docker"], {"DOCKER_CONFIG": str(tmp_path)}, cli):
        pytest.fail("Invalid client authentication must not dispatch")
    assert len(cli.calls) == 1


@pytest.mark.parametrize(
    "host",
    [
        "unix:///selected.sock",
        "npipe:////./pipe/selected",
        "tcp://selected.example:2376",
        "ssh://selected.example",
    ],
)
def test_default_context_binds_explicit_host_and_captured_tls_environment(host):
    cli = CLI(name="default")
    cli.record["Endpoints"]["docker"]["Host"] = host
    environment = {
        "DOCKER_CONTEXT": "default",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CERT_PATH": "/selected/certs",
    }
    with pin_route(["docker"], environment, cli) as route:
        assert route.command == ["docker", "--host", host]
        assert "DOCKER_CONTEXT" not in route.environment
        assert route.environment["DOCKER_TLS_VERIFY"] == "1"
        assert route.environment["DOCKER_CERT_PATH"] == "/selected/certs"
    assert len(cli.calls) == 1 and environment["DOCKER_CONTEXT"] == "default"


@pytest.mark.parametrize(
    "field,value",
    [("Name", "../unselected"), ("Endpoints", []), ("Endpoints", {"docker": {"Host": ""}})],
)
def test_invalid_route_metadata_fails_closed_before_export(field, value):
    cli = CLI()
    cli.record[field] = value
    with pytest.raises(ValueError), pin_route(["docker"], {}, cli):
        pytest.fail("Malformed routing must not dispatch")
    assert len(cli.calls) == 1
