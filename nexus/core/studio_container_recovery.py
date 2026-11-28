"""Host-local analysis cleanup journals; recovery never starts or replays work."""

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from nexus.core.studio_desktop import WorkspaceLock
from nexus.core.studio_docker_route import inspect_route, pin_route

LABEL = "io.nexus.studio.analysis-owner"
FORMAT = "nexus-analysis-cleanup/v1"
NAME = re.compile(r"nexus-analysis-[a-f0-9]{32}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{64}\Z")


def launch_environment(context):
    environment = dict(os.environ)
    if context:
        for key in (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        ):
            environment.pop(key, None)
    return environment


def route_fingerprint(command, environment, run, *, timeout=2):
    """Bind recovery to routing metadata without storing environment values."""
    return inspect_route(command, environment, run, timeout=timeout)[1]


def host_fingerprint():
    """Keep host ownership stable when a slim Linux image cannot discover a MAC.

    Real MAC-based hashes retain their existing format. Python marks random
    fallback nodes with the multicast bit; those are not persistent host IDs.
    A validated kernel boot UUID supplies a stable, host-specific fallback for
    process/container restarts within that boot. A changed boot or hostname
    still requires review; old random-node grants are never adopted.
    """
    node = uuid.getnode()
    if node & (1 << 40) and sys.platform.startswith("linux"):
        try:
            with Path("/proc/sys/kernel/random/boot_id").open() as stream:
                boot = stream.read(64).strip()
            identity = uuid.UUID(boot)
            if identity.int and str(identity) == boot:
                node = "linux-boot:" + boot
        except (OSError, ValueError):
            # Retain legacy fail-closed behavior on unsupported kernels instead
            # of weakening the host check to hostname alone.
            pass
    return hashlib.sha256(f"{socket.gethostname()}:{node}".encode()).hexdigest()


class ContainerRecovery:
    def __init__(self, studio):
        # Process/daemon ownership is host-local, even when workspace records use SQL.
        # Reuse the workspace's atomic record writer and kernel ownership lock.
        self.directory = studio.state / "analysis-workers"
        self.write, self.read = studio._write, studio._read
        # Shared state volumes must not let another worker host retire this
        # host's cleanup records merely because its own Docker daemon is empty.
        self.host = host_fingerprint()

    def register(self, name, command, environment, run, *, route_sha256=None):
        if not NAME.fullmatch(name):
            raise ValueError("Invalid analysis cleanup identity")
        fingerprint = (
            route_fingerprint(command, environment, run) if route_sha256 is None else route_sha256
        )
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise ValueError("Invalid analysis cleanup route fingerprint")
        directory = self.directory / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = WorkspaceLock(directory, ".owner.lock")
        if not lock.acquire(0):
            raise RuntimeError("Analysis cleanup identity is already owned")
        context = command[2] if command[1:2] == ["--context"] else None
        record = {
            "format": FORMAT,
            "name": name,
            "context": context,
            "route_sha256": fingerprint,
            "host_sha256": self.host,
            "container_id": None,
            "creation_uncertain": True,
        }
        try:
            self.write(directory / "record.json", record)
        except BaseException:
            lock.release()
            raise
        return CleanupLease(self, record, lock)

    def pending(self):
        return sum(1 for _ in self.directory.glob("*/record.json"))

    def recover(self, run=subprocess.run, *, timeout=8):
        """Explicit bounded cleanup; running owners and changed routes are untouched."""
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
            raise ValueError(
                "Cleanup recovery timeout must be greater than zero and at most 30 seconds"
            )
        deadline = time.monotonic() + timeout
        results = []

        def call(command, **kwargs):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            requested = kwargs.pop("timeout", 2)
            return run(command, timeout=min(requested, 2, remaining), **kwargs)

        for path in self.directory.glob("*/record.json"):
            if time.monotonic() >= deadline or len(results) >= 32:
                break
            name = path.parent.name
            if not NAME.fullmatch(name) or path.is_symlink() or path.parent.is_symlink():
                results.append({"name": name, "status": "invalid_record"})
                continue
            lock = WorkspaceLock(path.parent, ".owner.lock")
            if not lock.acquire(0):
                results.append({"name": name, "status": "owner_active"})
                continue
            status = "unconfirmed"
            try:
                # Read only after taking the lock; an owner may have just finished.
                if not path.exists():
                    continue
                record = self.read(path)
                if not isinstance(record, dict):
                    raise ValueError("Invalid analysis cleanup record")
                if record.get("host_sha256") != self.host:
                    status = "other_host"
                    continue
                context = record.get("context")
                identity = record.get("container_id")
                if (
                    record.get("format") != FORMAT
                    or record.get("name") != name
                    or not isinstance(record.get("creation_uncertain"), bool)
                    or (
                        identity is not None
                        and (not isinstance(identity, str) or not CONTAINER_ID.fullmatch(identity))
                    )
                    or (
                        context is not None
                        and (
                            not isinstance(context, str)
                            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", context)
                        )
                    )
                ):
                    raise ValueError("Invalid analysis cleanup record")
                command = ["docker", "--context", context] if context else ["docker"]
                environment = launch_environment(context)
                with pin_route(command, environment, call) as route:
                    if route.fingerprint != record.get("route_sha256"):
                        status = "route_changed"
                        continue
                    command, environment = route.command, route.environment
                    listing = call(
                        [
                            *command,
                            "ps",
                            "--all",
                            "--no-trunc",
                            "--filter",
                            "name=^/" + name + "$",
                            "--format",
                            "{{.ID}}",
                        ],
                        capture_output=True,
                        env=environment,
                    )
                    if listing.returncode:
                        continue
                    found = listing.stdout.decode().strip()
                    if not found:
                        status = "awaiting_creation" if record["creation_uncertain"] else "removed"
                    elif not CONTAINER_ID.fullmatch(found) or (identity and found != identity):
                        status = "ownership_mismatch"
                    else:
                        inspection = call(
                            [*command, "inspect", found], capture_output=True, env=environment
                        )
                        if inspection.returncode:
                            continue
                        info = json.loads(inspection.stdout)[0]
                        if not isinstance(info, dict):
                            raise ValueError("Invalid analysis container inspection")
                        if (
                            info.get("Id") != found
                            or info.get("Name") != "/" + name
                            or (info.get("Config", {}).get("Labels") or {}).get(LABEL) != name
                        ):
                            status = "ownership_mismatch"
                            continue
                        # Persist acknowledgment before removal, so a lost remove reply
                        # can be resolved by absence during a later recovery attempt.
                        record.update(container_id=found, creation_uncertain=False)
                        self.write(path, record)
                        removal = call(
                            [*command, "rm", "--force", found], capture_output=True, env=environment
                        )
                        if removal.returncode == 0:
                            status = "removed"
                    if status == "removed":
                        path.unlink(missing_ok=True)
            except (
                OSError,
                ValueError,
                KeyError,
                IndexError,
                TypeError,
                subprocess.SubprocessError,
            ):
                pass
            finally:
                lock.release()
                results.append({"name": name, "status": status})
        return {"items": results, "pending": self.pending()}


class CleanupLease:
    def __init__(self, store, record, lock):
        self.store, self.record, self.lock = store, record, lock
        self.path = store.directory / record["name"] / "record.json"

    def created(self, identity):
        if not CONTAINER_ID.fullmatch(identity):
            raise ValueError("Docker returned an invalid analysis container identity")
        self.record.update(container_id=identity, creation_uncertain=False)
        self.store.write(self.path, self.record)

    def finish(self, confirmed):
        try:
            if confirmed and not self.record["creation_uncertain"]:
                self.path.unlink(missing_ok=True)
        finally:
            self.lock.release()
