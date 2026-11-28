"""Pin Docker routing for one operation without changing the operator's config.

Named contexts are exported/imported by Docker itself into a private temporary
configuration. This retains context TLS settings without copying registry logins
or translating transport options. The in-memory default context uses its resolved
explicit host and captured TLS environment. Cleanup journals retain hashes only.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROUTE_ENVIRONMENT = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "DOCKER_CONFIG",
    "HOME",
    "USERPROFILE",
)


def inspect_route(command, environment, run, *, timeout=2):
    result = run(
        [*command, "context", "inspect"],
        capture_output=True,
        timeout=timeout,
        env=environment,
    )
    if result.returncode:
        raise ValueError("Docker routing could not be verified")
    records = json.loads(result.stdout)
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("Docker routing could not be verified")
    routing = {
        "context": records[0],
        "environment": {key: environment.get(key) for key in ROUTE_ENVIRONMENT},
    }
    fingerprint = hashlib.sha256(json.dumps(routing, sort_keys=True).encode()).hexdigest()
    return records[0], fingerprint


def _http_headers(command, environment):
    if "--config" in command:
        directory = command[command.index("--config") + 1]
    else:
        home = environment.get("HOME") or environment.get("USERPROFILE") or str(Path.home())
        directory = environment.get("DOCKER_CONFIG") or str(Path(home) / ".docker")
    source = Path(directory) / "config.json"
    try:
        with source.open("rb") as stream:
            content = stream.read(1024 * 1024 + 1)
    except FileNotFoundError:
        return {}
    if len(content) > 1024 * 1024:
        raise ValueError("Docker client configuration exceeds the snapshot limit")
    config = json.loads(content)
    headers = config.get("HttpHeaders") or {}
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise ValueError("Docker daemon headers could not be preserved")
    return headers


@dataclass(frozen=True)
class DockerRoute:
    command: list
    environment: dict
    fingerprint: str


@contextmanager
def pin_route(command, environment, run, *, timeout=2):
    record, fingerprint = inspect_route(command, environment, run, timeout=timeout)
    name = record.get("Name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise ValueError("Docker routing could not be verified")
    endpoints = record.get("Endpoints")
    if not isinstance(endpoints, dict) or not isinstance(endpoints.get("docker"), dict):
        raise ValueError("Docker routing could not be verified")
    endpoint = endpoints["docker"]
    host = endpoint.get("Host")
    if not isinstance(host, str) or not host or "\x00" in host:
        raise ValueError("Docker routing could not be verified")
    selected = dict(environment)
    selected.pop("DOCKER_CONTEXT", None)
    if name == "default":
        # --host selects the resolved default endpoint even if context use changes.
        yield DockerRoute([command[0], "--host", host], selected, fingerprint)
        return
    headers = _http_headers(command, environment)
    exported = run(
        [*command, "context", "export", name, "-"],
        capture_output=True,
        timeout=timeout,
        env=environment,
    )
    if exported.returncode or not exported.stdout or len(exported.stdout) > 1024 * 1024:
        raise ValueError("Docker context snapshot could not be prepared")
    with tempfile.TemporaryDirectory(prefix="nexus-docker-route-") as temporary:
        if headers:
            target = Path(temporary) / "config.json"
            with target.open("w", encoding="utf-8") as stream:
                os.chmod(target, 0o600)
                json.dump({"HttpHeaders": headers}, stream)
        for key in ("DOCKER_HOST", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            selected.pop(key, None)
        selected["DOCKER_CONFIG"] = temporary
        bound = [command[0], "--config", temporary, "--context", name]
        imported = run(
            [*bound, "context", "import", name, "-"],
            input=exported.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=selected,
        )
        if imported.returncode:
            raise ValueError("Docker context snapshot could not be prepared")
        for path in Path(temporary).rglob("*"):
            if path.is_symlink():
                raise ValueError("Docker context snapshot contains an unsupported link")
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        copied, _ = inspect_route(bound, selected, run, timeout=timeout)
        keys = ("Name", "Metadata", "Endpoints", "TLSMaterial")
        if any(copied.get(key) != record.get(key) for key in keys):
            raise ValueError("Docker context changed while its snapshot was prepared")
        yield DockerRoute(bound, selected, fingerprint)
