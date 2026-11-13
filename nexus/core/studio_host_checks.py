"""Read-only, redacted Studio/proxy mount and ownership preflight.

Run on the selected Docker daemon host. Host path identity is mandatory: an
unresolvable source cannot be certified from Docker's path spelling alone.
"""

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from nexus.core.studio_container_recovery import launch_environment
from nexus.core.studio_docker_route import pin_route

INSPECT_FORMAT = (
    '{"id":{{json .Id}},"running":{{json .State.Running}},'
    '"privileged":{{json .HostConfig.Privileged}},"mounts":{{json .Mounts}}}'
)
# This probe is deliberately stdlib-only and reads metadata, never ledger bytes.
STAT_PROBE = r"""
import json,os,stat,sys
from pathlib import Path
p=Path(sys.argv[1]);result={'uid':os.getuid(),'gid':os.getgid(),'ledger':'unavailable','ledger_private':False}
try:
    s=p.lstat()
    result['directory']=stat.S_ISDIR(s.st_mode) and not p.is_symlink() and p.resolve()==p
    result['directory_owner']=s.st_uid==os.getuid()
    result['directory_mode']=stat.S_IMODE(s.st_mode)==0o700
    f=p/'ownership.sqlite'
    try:
        s=f.lstat();result['ledger']='present'
        result['ledger_private']=(stat.S_ISREG(s.st_mode) and not f.is_symlink()
            and s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1)
    except FileNotFoundError:
        result['ledger']='absent';result['ledger_private']=True
except OSError:
    result['directory']=False;result['directory_owner']=False;result['directory_mode']=False
try:
    s=os.stat(sys.argv[2])
    result['socket_access']=(stat.S_ISSOCK(s.st_mode) and not s.st_mode&0o002
        and os.access(sys.argv[2],os.R_OK|os.W_OK))
except OSError:
    result['socket_access']=False
print(json.dumps(result))
"""


def _path(value):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "\x00" in value
    ):
        raise ValueError("Invalid mount path")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError("Invalid mount path")
    return path


def _overlaps(left, right):
    def identity(path):
        metadata = path.stat()
        return metadata.st_dev, metadata.st_ino

    # Inode ancestry also catches bind-mounted aliases and hard-linked sockets.
    return identity(left) in {identity(path) for path in (right, *right.parents)} or identity(
        right
    ) in {identity(path) for path in (left, *left.parents)}


def _mounts(record):
    mounts = record["mounts"]
    if not isinstance(mounts, list) or len(mounts) > 256:
        raise ValueError("Invalid mount metadata")
    for mount in mounts:
        if not isinstance(mount, dict) or mount.get("Type") not in {"bind", "volume", "tmpfs"}:
            raise ValueError("Unsupported mount metadata")
        _path(mount.get("Destination"))
        if mount["Type"] != "tmpfs":
            _path(mount.get("Source"))
        if type(mount.get("RW")) is not bool:
            raise ValueError("Invalid mount metadata")
    return mounts


def _covering(mounts, path):
    choices = [mount for mount in mounts if path.is_relative_to(_path(mount["Destination"]))]
    return max(choices, key=lambda mount: len(_path(mount["Destination"]).parts), default=None)


def _source(mount, destination):
    if not mount or mount["Type"] == "tmpfs":
        return None
    return _path(mount["Source"]) / destination.relative_to(_path(mount["Destination"]))


def verify_proxy_layout(
    studio_container,
    proxy_container,
    *,
    docker_context,
    private_path="/var/lib/nexus-proxy-private",
    upstream_socket="/var/run/docker.sock",
    run=None,
):
    """Inspect explicitly selected containers; return no names, paths or secrets.

    The selected proxy must have Python and the private store mounted already.
    An absent ledger is disclosed, not treated as live recovery evidence.
    Host-source resolution must run on the daemon host, not a remote client.
    """
    for value in (studio_container, proxy_container, docker_context):
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value
        ):
            raise ValueError("Choose explicit Docker container and context names")
    private, upstream = _path(private_path), _path(upstream_socket)
    if (
        private == PurePosixPath("/")
        or private_path != str(private)
        or upstream_socket != str(upstream)
    ):
        raise ValueError("Use canonical absolute proxy storage and socket paths")
    run = run or subprocess.run
    checks, identities, ledger = [], {}, "unavailable"

    def check(identity, status, detail):
        checks.append(
            {"check": identity, "status": status, "passed": status == "passed", "detail": detail}
        )

    def flag(identity, passed, detail):
        check(identity, "passed" if passed else "failed", detail)

    def decoded(result):
        if (
            result.returncode
            or not isinstance(result.stdout, bytes)
            or len(result.stdout) > 1024 * 1024
        ):
            raise ValueError("Inspection unavailable")
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError("Inspection unavailable")
        return value

    try:
        environment = launch_environment(docker_context)
        with pin_route(["docker", "--context", docker_context], environment, run) as route:
            records = []
            for role, name in (("studio", studio_container), ("proxy", proxy_container)):
                record = decoded(
                    run(
                        [*route.command, "container", "inspect", "--format", INSPECT_FORMAT, name],
                        capture_output=True,
                        timeout=5,
                        env=route.environment,
                    )
                )
                if not isinstance(record.get("id"), str) or not re.fullmatch(
                    r"[a-f0-9]{64}", record["id"]
                ):
                    raise ValueError("Invalid container identity")
                if any(type(record.get(key)) is not bool for key in ("running", "privileged")):
                    raise ValueError("Invalid container state")
                identities[role] = hashlib.sha256(record["id"].encode()).hexdigest()
                _mounts(record)
                records.append(record)
            studio, proxy = records
            flag(
                "distinct-running-containers",
                studio["id"] != proxy["id"] and studio["running"] and proxy["running"],
                "Select distinct running Studio and proxy containers.",
            )
            flag(
                "studio-unprivileged",
                not studio["privileged"],
                "Studio must not run as a privileged container.",
            )
            private_mount, socket_mount = (
                _covering(proxy["mounts"], path) for path in (private, upstream)
            )
            private_source, socket_source = (
                _source(private_mount, private),
                _source(socket_mount, upstream),
            )
            flag(
                "dedicated-private-mount",
                bool(
                    private_source
                    and private_mount["RW"]
                    and _path(private_mount["Destination"]) == private
                ),
                "Proxy storage requires its own writable bind or volume mount.",
            )
            flag(
                "private-mount-unshadowed",
                not any(
                    _path(mount["Destination"]) != private
                    and _path(mount["Destination"]).is_relative_to(private)
                    for mount in proxy["mounts"]
                ),
                "Nested mounts must not replace the private ledger or any part of its storage directory.",
            )
            flag(
                "upstream-socket-mount",
                socket_source is not None,
                "The selected upstream socket must have an identifiable host source.",
            )
            # Resolve actual host identities, including aliases, before comparing.
            # Docker Desktop/remote host paths are deliberately not guessed.
            sources = None
            try:
                sources = [
                    Path(m["Source"]).resolve(strict=True)
                    for m in studio["mounts"]
                    if m["Type"] != "tmpfs"
                ]
            except (OSError, RuntimeError, ValueError):
                pass
            observations = []
            for identity, target, detail in (
                (
                    "private-source-isolation",
                    private_source,
                    "No Studio bind or volume source may expose any part or parent of private proxy storage, even read-only.",
                ),
                (
                    "raw-socket-isolation",
                    socket_source,
                    "No Studio mount may expose the raw daemon socket or its parent.",
                ),
            ):
                try:
                    if sources is None or target is None:
                        raise ValueError("Source unavailable")
                    resolved = Path(str(target)).resolve(strict=True)
                    isolated = all(not _overlaps(source, resolved) for source in sources)
                    observations.append((identity, "passed" if isolated else "failed", detail))
                except (OSError, RuntimeError, ValueError):
                    observations.append(
                        (
                            identity,
                            "unavailable",
                            "This source or a Studio source cannot be resolved; aliases remain unverified.",
                        )
                    )
            complete_identity = all(status != "unavailable" for _, status, _ in observations)
            check(
                "host-source-identity",
                "passed" if complete_identity else "unavailable",
                "All host sources must resolve on the selected daemon host; each isolation result is reported independently.",
            )
            for observation in observations:
                check(*observation)
            if proxy["running"]:
                metadata = decoded(
                    run(
                        [
                            *route.command,
                            "exec",
                            proxy["id"],
                            "python",
                            "-I",
                            "-c",
                            STAT_PROBE,
                            str(private),
                            str(upstream),
                        ],
                        capture_output=True,
                        timeout=5,
                        env=route.environment,
                    )
                )
                if type(metadata.get("uid")) is not int or type(metadata.get("gid")) is not int:
                    raise ValueError("Invalid stat identity")
                flag(
                    "proxy-nonroot-user",
                    metadata["uid"] != 0,
                    "Proxy runs under a non-root service UID.",
                )
                flag(
                    "upstream-socket-access",
                    metadata.get("socket_access") is True,
                    "The proxy UID/groups must access a non-world-writable Unix upstream socket.",
                )
                flag(
                    "private-directory",
                    all(
                        metadata.get(key) is True
                        for key in ("directory", "directory_owner", "directory_mode")
                    ),
                    "Private storage must be a canonical service-owned directory with mode 0700.",
                )
                found_ledger = metadata.get("ledger")
                if found_ledger not in {"present", "absent", "unavailable"}:
                    raise ValueError("Invalid ledger stat metadata")
                ledger = found_ledger
                if ledger == "unavailable":
                    check(
                        "private-ledger",
                        "unavailable",
                        "Private storage is missing or unreadable; ledger metadata cannot be verified.",
                    )
                else:
                    flag(
                        "private-ledger",
                        metadata.get("ledger_private") is True,
                        "Existing ledger must be a service-owned mode-0600 regular file with one link; an absent ledger is not recovery proof.",
                    )
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError):
        check(
            "runtime-inspection",
            "unavailable",
            "Docker routing, container metadata or the fixed Python stat probe could not be verified. No services were changed.",
        )
    return {
        "format": "nexus-studio-host-check/v1",
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "Container mount configuration and private-store stat metadata only. Run on the selected daemon host; not complete deployment acceptance.",
        "passed": bool(checks) and all(item["passed"] for item in checks),
        "checks": checks,
        "targets": identities,
        "ledger": ledger,
    }
