#!/usr/bin/env python3
"""Filtering Docker API proxy for Nexus Studio.

Studio starts two kinds of short-lived containers: shared CLI workers
(``nexus-studio-<hex>``, see ``nexus/core/studio_team.py``) and analysis
sandboxes (``nexus-analysis-<hex>``, see ``nexus/core/studio_sandbox.py``).
Handing Studio the raw Docker socket would let a compromised Studio start a
privileged container with the host filesystem mounted. This proxy exposes a
socket that accepts only the calls those two code paths make, and only with the
hardening they already request:

* ``HEAD/GET /_ping`` and ``GET /version``;
* ``GET /images/<ref>/json`` for configured images;
* ``POST /containers/create`` for the two name patterns, a configured image, a
  non-root user, a read-only root, ``CapDrop=ALL``, ``no-new-privileges``,
  auto-remove, resource limits, the expected network, and bind mounts only from
  the credential, scratch, attachment or model locations each path uses;
* attach/start/wait/kill/resize/inspect/delete for containers created here.
* exact analysis-name cleanup lookup, additionally restricted to the tracked
  full container ID; unknown ownership is denied rather than reported absent.

Everything else gets 403. Allowed images and model files are read from Studio's
own team and runtime configuration on every request, so the policy follows that
configuration. Bind sources must contain no symlinks; they are checked at
create and again at start. A source that Studio itself can rewrite between that
check and the daemon's mount is a remaining race (see deploy/studio/README.md).

Standard library only. Each client connection carries one request; responses
are sent with ``Connection: close``, except an attach upgrade, which becomes a
raw stream in both directions.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import runpy
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

# Compose mounts the framework's stdlib-only policy beside this launcher.
# Source checkouts load the same file directly, without importing AI SDKs.
_policy_path = Path(__file__).with_name("studio_docker_proxy_policy.py")
if not _policy_path.is_file():
    _policy_path = Path(__file__).resolve().parents[2] / "nexus/core/studio_docker_proxy_policy.py"
_policy = runpy.run_path(str(_policy_path))
recovery_listing = _policy["recovery_listing"]
container_target = _policy["container_target"]

LISTEN = os.environ.get("PROXY_LISTEN", "/var/lib/nexus-studio/docker-proxy/docker.sock")
UPSTREAM = os.environ.get("PROXY_UPSTREAM", "/var/run/docker.sock")
TEAM_CONFIG = Path(os.environ.get("STUDIO_TEAM_CONFIG", "/etc/nexus-studio/studio-team.json"))
RUNTIME_CONFIG = Path(os.environ.get("STUDIO_RUNTIME_CONFIG", "/etc/nexus-studio/studio-runtime.json"))
WORKER_USER = os.environ.get("STUDIO_WORKER_USER", "1001:1001")
CREDENTIAL_ROOT = os.environ.get("STUDIO_CREDENTIAL_ROOT", "/var/lib/nexus/auth")
STUDIO_TMP = os.environ.get("STUDIO_TMP", "/var/lib/nexus-studio/home/tmp")
STATE_DIR = os.environ.get("PROXY_STATE_DIR")

MAX_HEAD = 64 * 1024
MAX_BODY = 1024 * 1024
MAX_TRACKED = 512

NAME_RE = re.compile(r"^(nexus-studio|nexus-analysis)-[0-9a-f]{32}$")
API_PREFIX_RE = re.compile(r"^/v\d+(\.\d+)?(?=/)")
CONTAINER_ACTION_RE = re.compile(r"^/containers/([^/]+)/(attach|start|wait|kill|resize|json)$")
CONTAINER_RE = re.compile(r"^/containers/([^/]+)$")
IMAGE_INSPECT_RE = re.compile(r"^/images/(.+)/json$")

TEAM_TARGETS = {"/home/nexus", "/run/credentials/codex", "/run/credentials/claude"}
NO_NEW_PRIVILEGES = {"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"}

CONFIG_KEYS = {
    "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr", "Tty",
    "OpenStdin", "StdinOnce", "Env", "Cmd", "Image", "Volumes", "WorkingDir", "Entrypoint",
    "Labels", "HostConfig", "NetworkingConfig", "StopSignal", "StopTimeout", "ArgsEscaped",
}
# HostConfig keys validated below; every other key must be absent or zero-valued.
CHECKED_HOST_KEYS = {
    "LogConfig", "NetworkMode", "RestartPolicy", "AutoRemove", "ConsoleSize", "CapDrop",
    "ReadonlyRootfs", "SecurityOpt", "Tmpfs", "Memory", "MemorySwap", "NanoCpus",
    "MemorySwappiness", "PidsLimit", "Mounts",
}


class Denied(Exception):
    pass


@dataclass
class Policy:
    team_images: set[str] = field(default_factory=set)
    analysis_images: set[str] = field(default_factory=set)
    model_paths: set[str] = field(default_factory=set)
    worker_user: str = WORKER_USER
    credential_root: str = CREDENTIAL_ROOT
    studio_tmp: str = STUDIO_TMP
    project_key: str | None = None

    @property
    def images(self) -> set[str]:
        return self.team_images | self.analysis_images


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_policy() -> Policy:
    team = _read_json(TEAM_CONFIG)
    runtime = _read_json(RUNTIME_CONFIG)
    policy = Policy(project_key=team.get("project_key"))
    if isinstance(team.get("runner_image"), str):
        policy.team_images.add(team["runner_image"])
    workers = runtime.get("analysis_workers")
    for profile in (workers.values() if isinstance(workers, dict) else []):
        if not isinstance(profile, dict):
            continue
        if isinstance(profile.get("image"), str):
            policy.analysis_images.add(profile["image"])
        if isinstance(profile.get("model_path"), str):
            policy.model_paths.add(profile["model_path"])
    return policy


def is_zero(value: Any) -> bool:
    return value in (None, "", 0, False) or value == [] or value == {}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Denied(reason)


def _no_symlinks(source: str) -> bool:
    """True when source is absolute, normalized and has no symlink in any component."""
    return (
        os.path.isabs(source)
        and os.path.normpath(source) == source
        and os.path.realpath(source) == source
        and os.path.lexists(source)
    )


def _direct_child(source: str, parent: str, prefix: str) -> bool:
    return os.path.dirname(source) == parent and os.path.basename(source).startswith(prefix)


def check_mounts(kind: str, mounts: Any, policy: Policy) -> list[str]:
    _require(isinstance(mounts, list), "Mounts must be a list")
    sources: list[str] = []
    targets: set[str] = set()
    for mount in mounts:
        _require(isinstance(mount, dict), "mount must be an object")
        _require(set(mount) <= {"Type", "Source", "Target", "ReadOnly", "Consistency", "BindOptions"}, "unexpected mount field")
        _require(mount.get("Type") == "bind", "only bind mounts are allowed")
        options = mount.get("BindOptions") or {}
        _require(isinstance(options, dict), "BindOptions must be an object")
        _require(options.get("Propagation", "") in ("", "rprivate", "private"), "mount propagation is not allowed")
        _require(not options.get("CreateMountpoint"), "CreateMountpoint is not allowed")
        source, target, read_only = mount.get("Source"), mount.get("Target"), mount.get("ReadOnly") is True
        _require(isinstance(source, str) and isinstance(target, str), "mount needs Source and Target")
        _require(target not in targets, f"duplicate mount target {target}")
        targets.add(target)
        _require(_no_symlinks(source), f"mount source {source} must be a real path without symlinks")
        if kind == "nexus-studio":
            root = policy.credential_root.rstrip("/") + "/"
            if target in TEAM_TARGETS:
                _require(source.startswith(root) and os.path.isdir(source), f"credential mount {source} is outside {policy.credential_root}")
            elif target == "/workspace":
                _require(read_only and os.path.isdir(source) and _direct_child(source, policy.studio_tmp, "nexus-team-prompt-"), f"workspace mount {source} is not a read-only prompt scratch dir")
            elif target == "/workspace/attachments":
                _require(read_only and os.path.isdir(source) and _direct_child(source, policy.studio_tmp, "nexus-attachments-"), f"attachment mount {source} is not a read-only attachment dir")
            else:
                raise Denied(f"mount target {target} is not allowed for CLI workers")
        else:
            _require(target == "/models/model.pt" and read_only, f"mount target {target} is not allowed for analysis workers")
            _require(source in policy.model_paths and os.path.isfile(source), f"model {source} is not a configured analysis model")
        sources.append(source)
    return sources


def check_create(query: dict[str, list[str]], body: Any, policy: Policy) -> tuple[str, list[str]]:
    """Validate a container create request. Returns (name, bind sources) or raises Denied."""
    _require(set(query) <= {"name", "platform"}, "unexpected create query parameter")
    _require(all(is_zero(value) for value in query.get("platform", [])), "platform override is not allowed")
    name = (query.get("name") or [""])[0]
    match = NAME_RE.match(name)
    _require(match is not None, f"container name {name!r} is not a Studio worker name")
    kind = match.group(1)
    _require(isinstance(body, dict), "create body must be an object")

    for key, value in body.items():
        _require(key in CONFIG_KEYS or is_zero(value), f"Config.{key} is not allowed")
    _require(is_zero(body.get("Volumes")), "anonymous volumes are not allowed")
    image = body.get("Image")
    allowed_images = policy.team_images if kind == "nexus-studio" else policy.analysis_images
    _require(image in allowed_images, f"image {image!r} is not configured for {kind} workers")
    expected_user = policy.worker_user if kind == "nexus-studio" else "65534:65534"
    _require(body.get("User") == expected_user, f"user must be {expected_user}")

    host = body.get("HostConfig")
    _require(isinstance(host, dict), "HostConfig is required")
    for key, value in host.items():
        _require(key in CHECKED_HOST_KEYS or is_zero(value), f"HostConfig.{key} is not allowed")
    log_config = host.get("LogConfig") or {}
    _require(log_config.get("Type", "") in ("", "none") and is_zero(log_config.get("Config")), "log driver is not allowed")
    networks = {"default", "bridge"} if kind == "nexus-studio" else {"none"}
    _require(host.get("NetworkMode") in networks, f"network {host.get('NetworkMode')!r} is not allowed")
    _require((host.get("RestartPolicy") or {}).get("Name", "") in ("", "no"), "restart policy is not allowed")
    _require(host.get("AutoRemove") is True, "AutoRemove (--rm) is required")
    _require(host.get("ReadonlyRootfs") is True, "read-only root filesystem is required")
    _require("ALL" in [str(cap).upper() for cap in host.get("CapDrop") or []], "CapDrop=ALL is required")
    security = host.get("SecurityOpt") or []
    _require(bool(security) and set(security) <= NO_NEW_PRIVILEGES, "SecurityOpt must be exactly no-new-privileges")
    _require(isinstance(host.get("Tmpfs") or {}, dict), "Tmpfs must be an object")
    memory, pids, cpus = host.get("Memory") or 0, host.get("PidsLimit") or 0, host.get("NanoCpus") or 0
    _require(0 < memory <= 4 * 1024**3, "a memory limit up to 4 GiB is required")
    _require(0 < pids <= 1024, "a pids limit up to 1024 is required")
    _require(0 < cpus <= 2 * 10**9, "a CPU limit up to 2 CPUs is required")
    _require(host.get("MemorySwappiness") in (None, -1), "MemorySwappiness override is not allowed")
    _require(host.get("MemorySwap") in (None, 0, -1) or (host.get("MemorySwap") or 0) >= memory, "invalid MemorySwap")
    sources = check_mounts(kind, host.get("Mounts") or [], policy)

    endpoints = (body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}
    _require(isinstance(endpoints, dict) and set(endpoints) <= networks, "network attachment is not allowed")
    for endpoint in endpoints.values():
        _require(all(is_zero(value) for value in (endpoint or {}).values()), "endpoint settings are not allowed")
    return name, sources


class Tracker(_policy["ProxyContainers"]):
    """Standalone consumer of the framework's process-local ownership policy."""

    def __init__(self, journal=None) -> None:
        super().__init__(max_tracked=MAX_TRACKED, journal=journal)


@dataclass
class Request:
    method: str
    target: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    head: bytes
    body: bytes


def decide(request: Request, policy: Policy, tracker: Tracker) -> tuple[str, Any]:
    """Return (action, detail) for an allowed request, or raise Denied."""
    method, path = request.method, request.path
    if path == "/_ping" and method in ("GET", "HEAD"):
        return "pass", None
    if path == "/version" and method == "GET":
        return "pass", None
    match = IMAGE_INSPECT_RE.match(path)
    if match and method == "GET":
        _require(unquote(match.group(1)) in policy.images, "image is not configured for Studio workers")
        return "pass", None
    if path == "/containers/create" and method == "POST":
        _require(not tracker.storage_failed, "proxy ownership storage is unavailable; review pending cleanup")
        try:
            body = json.loads(request.body or b"null")
        except ValueError as exc:
            raise Denied("create body is not JSON") from exc
        return "create", check_create(request.query, body, policy)
    if path == "/containers/json" and method == "GET":
        try:
            return "list", recovery_listing(request.query, tracker.resolve)
        except ValueError as exc:
            raise Denied(str(exc)) from None
    match = CONTAINER_ACTION_RE.match(path)
    if match and method in ("POST", "GET"):
        container_id = tracker.resolve(unquote(match.group(1)))
        _require(container_id is not None, "container was not created through this proxy")
        action = match.group(2)
        _require((action == "json") == (method == "GET"), "method not allowed")
        _require(not tracker.storage_failed or action == "json", "proxy ownership storage is unavailable; cleanup only")
        _require(not tracker.cleanup_only(container_id) or action == "json", "recovered ownership permits cleanup only")
        if action == "start":
            # Narrow the swap window: sources must still be the same real paths.
            for source in tracker.sources(container_id):
                _require(_no_symlinks(source), f"mount source {source} changed since create")
        if action == "attach":
            return "upgrade", container_id
        return "pass", container_id
    match = CONTAINER_RE.match(path)
    if match and method == "DELETE":
        container_id = tracker.resolve(unquote(match.group(1)))
        _require(container_id is not None, "container was not created through this proxy")
        return "delete", container_id
    raise Denied(f"{method} {path} is not allowed")


async def read_request(reader: asyncio.StreamReader) -> Request:
    head = await reader.readuntil(b"\r\n\r\n")
    if len(head) > MAX_HEAD:
        raise Denied("request head too large")
    lines = head[:-4].decode("latin-1").split("\r\n")
    method, target, _version = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    _require("transfer-encoding" not in headers, "chunked request bodies are not supported")
    length = int(headers.get("content-length", "0") or "0")
    _require(0 <= length <= MAX_BODY, "request body too large")
    body = await reader.readexactly(length) if length else b""
    parts = urlsplit(target)
    path = API_PREFIX_RE.sub("", parts.path)
    return Request(method, target, path, parse_qs(parts.query, keep_blank_values=True), headers, head, body)


def rewrite_head(request: Request, upgrade: bool, target: str | None = None) -> bytes:
    """Re-emit the request head; non-upgrade requests are forced to Connection: close."""
    lines = request.head[:-4].decode("latin-1").split("\r\n")
    kept = [f"{request.method} {target} HTTP/1.1" if target else lines[0]]
    for line in lines[1:]:
        key = line.partition(":")[0].strip().lower()
        if key in ("connection", "keep-alive", "proxy-connection") and not upgrade:
            continue
        kept.append(line)
    if not upgrade:
        kept.append("Connection: close")
    return ("\r\n".join(kept) + "\r\n\r\n").encode("latin-1")


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except (ConnectionError, OSError):
        pass


def deny_response(reason: str) -> bytes:
    body = json.dumps({"message": f"nexus docker proxy: {reason}"}).encode()
    return (
        b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )


def log(line: str) -> None:
    print(line, flush=True)


class Proxy:
    def __init__(self, upstream: str = UPSTREAM) -> None:
        self.upstream = upstream
        self.tracker = Tracker()
        self.journal = None
        self.identify = None

    async def enable_state(self, directory: str, project: str) -> None:
        path = Path(__file__).with_name("studio_docker_proxy_state.py")
        if not path.is_file():
            path = Path(__file__).resolve().parents[2] / "nexus/core/studio_docker_proxy_state.py"
        helpers = runpy.run_path(str(path))
        reader, writer = await asyncio.open_unix_connection(self.upstream)
        try:
            daemon = await helpers["daemon_identity"](reader, writer)
        finally:
            writer.close()
            await writer.wait_closed()
        journal = helpers["ProxyJournal"](directory, daemon, project, maximum=MAX_TRACKED)
        try:
            tracker = Tracker(journal)
        except BaseException:
            journal.close()
            raise
        self.journal, self.tracker = journal, tracker
        self.identify = helpers["daemon_identity"]

    def close(self) -> None:
        if self.journal:
            self.journal.close()

    async def handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        try:
            try:
                request = await read_request(client_reader)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
                return
            try:
                policy = load_policy()
                if self.journal:
                    self.journal.require_project(policy.project_key)
                action, detail = decide(request, policy, self.tracker)
                target = None
                if action == "list":
                    target = urlsplit(request.target).path + "?" + urlencode(detail, doseq=True)
                elif action in {"pass", "upgrade", "delete"} and detail is not None:
                    target = container_target(request.target, detail)
            except (Denied, ValueError) as exc:
                log(f"deny {request.method} {request.path}: {exc}")
                client_writer.write(deny_response(str(exc)))
                await client_writer.drain()
                return
            log(f"allow {request.method} {request.path}")
            up_reader, up_writer = await asyncio.open_unix_connection(self.upstream)
            try:
                if self.journal:
                    daemon = await self.identify(up_reader, up_writer)
                    self.journal.require_daemon(daemon)
                up_writer.write(rewrite_head(request, action == "upgrade", target) + request.body)
                await up_writer.drain()
                if action == "upgrade":
                    await asyncio.gather(pipe(client_reader, up_writer), pipe(up_reader, client_writer))
                elif action == "create":
                    response = await up_reader.read()
                    self._track_create(response, *detail)
                    client_writer.write(response)
                    await client_writer.drain()
                elif action == "delete":
                    head = await up_reader.readuntil(b"\r\n\r\n")
                    status = head.split(b"\r\n", 1)[0].split()[1]
                    if status in {b"204", b"404"}:
                        self.tracker.retire(detail)
                    client_writer.write(head)
                    await client_writer.drain()
                    await pipe(up_reader, client_writer)
                    # Retain bounded ownership tombstones for lost replies and
                    # ID-scoped absence checks. Evict retired entries first.
                else:
                    await pipe(up_reader, client_writer)
            finally:
                up_writer.close()
        except (ConnectionError, OSError, ValueError, IndexError, sqlite3.Error, asyncio.IncompleteReadError) as exc:
            log(f"connection or ownership error: {type(exc).__name__}; request outcome is unconfirmed")
        finally:
            client_writer.close()

    def _track_create(self, response: bytes, name: str, sources: list[str]) -> None:
        status_line, _, rest = response.partition(b"\r\n")
        if b" 201 " not in status_line + b" ":
            return
        body = rest.partition(b"\r\n\r\n")[2]
        try:
            container_id = json.loads(body).get("Id")
        except ValueError:
            # Content-Length bodies are plain JSON; a chunked one starts with its size.
            try:
                container_id = json.loads(body.split(b"\r\n", 1)[1].rsplit(b"\r\n0\r\n", 1)[0]).get("Id")
            except (ValueError, IndexError):
                container_id = None
        if isinstance(container_id, str):
            self.tracker.add(container_id, name, sources)


async def main() -> None:
    async with _policy["proxy_termination"]() as stop:
        proxy = Proxy()
        try:
            if STATE_DIR:
                await proxy.enable_state(STATE_DIR, _read_json(TEAM_CONFIG).get("project_key"))
            if stop.is_set():
                return
            # Validate and lock private ownership before replacing a listener path.
            listen = Path(LISTEN)
            listen.parent.mkdir(parents=True, exist_ok=True)
            if listen.exists() or listen.is_symlink():
                listen.unlink()
            old_umask = os.umask(0o177)
            try:
                server = await asyncio.start_unix_server(proxy.handle, path=str(listen), limit=MAX_HEAD)
            finally:
                os.umask(old_umask)
            log(f"nexus docker proxy listening on {listen} -> {UPSTREAM}")
            async with server:
                await stop.wait()
        finally:
            proxy.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
