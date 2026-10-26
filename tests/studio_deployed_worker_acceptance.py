"""Opt-in worker acceptance inside an already deployed Studio controller.

Run with an explicitly selected configured image, filtered Unix socket and worker
UID:GID. This exercises real Docker requests without provider CLIs, credentials,
workspace mounts, downloads, service restarts or daemon changes. Missing
prerequisites fail this separate gate rather than skip a required regression.
"""

import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path


def exercise():
    image = os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"]
    endpoint = os.environ["NEXUS_ACCEPTANCE_FILTERED_SOCKET"]
    user = os.environ["NEXUS_ACCEPTANCE_WORKER_USER"]
    assert re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", image), "Choose a pinned configured image"
    assert re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", user), "Choose a non-root worker UID:GID"
    assert endpoint.startswith("unix:///"), "Choose the controller's filtered Unix socket"
    socket = Path(endpoint.removeprefix("unix://"))
    assert socket == socket.resolve() and stat.S_ISSOCK(socket.lstat().st_mode)
    assert not Path("/var/run/docker.sock").exists(), "Controller exposes the raw Docker socket"
    assert not Path("/var/lib/nexus-proxy-private").exists(), (
        "Controller exposes private proxy storage"
    )

    # A fresh client configuration cannot import registry logins, context changes,
    # daemon headers or TLS environment from the controller's account homes.
    environment = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    marker = b"nexus-deployed-worker-check\n"
    uncertain = set()
    owned = []
    with tempfile.TemporaryDirectory(prefix="nexus-worker-check-") as configuration:
        docker = ["docker", "--config", configuration, "--host", endpoint]

        def call(arguments, *, timeout=10):
            return subprocess.run(
                [*docker, *arguments], capture_output=True, env=environment, timeout=timeout
            )

        def denied(result):
            assert result.returncode != 0 and b"nexus docker proxy" in result.stderr.lower(), (
                "Expected explicit filtering-proxy denial; raw diagnostics omitted"
            )

        def command(name):
            return [
                "create",
                "--rm",
                "--pull",
                "never",
                "--name",
                name,
                "--network",
                "bridge",
                "--read-only",
                "--user",
                user,
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "128m",
                "--memory-swap",
                "128m",
                "--cpus",
                "0.25",
                "--pids-limit",
                "32",
                "--log-driver",
                "none",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=16777216",
                "--entrypoint",
                "/bin/sh",
                image,
                "-c",
                "printf 'nexus-deployed-worker-check\\n'",
            ]

        def absent(identity):
            # Generic `docker inspect` falls back to image inspection after a
            # container 404, which the proxy correctly denies for unknown images.
            result = call(["container", "inspect", "--format", "{{.Id}}", identity])
            assert result.returncode != 0 and b"no such" in result.stderr.lower(), (
                "Owned worker absence was not confirmed; review required"
            )

        def create():
            name = "nexus-studio-" + uuid.uuid4().hex
            uncertain.add(name)
            result = call(command(name))
            assert result.returncode == 0, (
                "Configured worker creation failed; raw diagnostics omitted"
            )
            identity = result.stdout.decode().strip()
            assert re.fullmatch(r"[a-f0-9]{64}", identity), "Creation acknowledgment is invalid"
            owned.append(identity)
            uncertain.remove(name)
            return identity

        try:
            # Stop before mutation if the selected endpoint exposes global daemon
            # operations instead of the expected private filtering contract.
            denied(call(["ps", "--all", "--quiet"]))
            denied(call(["container", "inspect", "--format", "{{.Id}}", "0" * 64]))

            for fault in ("capability", "mount", "image"):
                name = "nexus-studio-" + uuid.uuid4().hex
                uncertain.add(name)
                args = command(name)
                position = args.index("--entrypoint")
                if fault == "capability":
                    args[position:position] = ["--cap-add", "SYS_ADMIN"]
                elif fault == "mount":
                    args[position:position] = [
                        "--mount",
                        f"type=bind,source={configuration},destination=/private,readonly",
                    ]
                else:
                    args[args.index(image)] = "unconfigured-worker@sha256:" + "0" * 64
                denied(call(args))
                uncertain.remove(name)

            identity = create()
            inspected = call(
                [
                    "container",
                    "inspect",
                    "--format",
                    '{"id":{{json .Id}},"running":{{json .State.Running}},'
                    '"user":{{json .Config.User}},"readonly":{{json .HostConfig.ReadonlyRootfs}},'
                    '"mounts":{{json .Mounts}},"network":{{json .HostConfig.NetworkMode}}}',
                    identity,
                ]
            )
            assert inspected.returncode == 0, "Owned stopped-worker inspection failed"
            metadata = json.loads(inspected.stdout)
            assert metadata == {
                "id": identity,
                "running": False,
                "user": user,
                "readonly": True,
                "mounts": [],
                "network": "bridge",
            }
            executed = call(["start", "--attach", identity])
            assert executed.returncode == 0 and executed.stdout == marker, (
                "Static no-provider worker invocation failed; raw diagnostics omitted"
            )
            # AutoRemove is asynchronous relative to the attach response. An
            # explicit deletion also resolves an already-removed tracked identity.
            removed = call(["rm", "--force", identity])
            assert removed.returncode == 0, "Tracked worker removal failed"
            absent(identity)

            stopped = create()
            removed = call(["rm", "--force", stopped])
            assert removed.returncode == 0, "Unstarted worker cleanup failed"
            absent(stopped)
            # A retained absence receipt must not turn into a proxy denial after
            # cleanup. This checks tombstones without replaying create or start.
            absent(stopped)
            return {
                "controller_raw_socket_absent": True,
                "controller_private_store_absent": True,
                "unscoped_listing_denied": True,
                "unknown_inspection_denied": True,
                "extra_capability_denied": True,
                "unapproved_mount_denied": True,
                "unconfigured_image_denied": True,
                "stopped_owned_inspection": True,
                "static_worker_execution": True,
                "unstarted_worker_cleanup": True,
                "tracked_absence_retained": True,
                "creates_acknowledged": len(owned),
                "starts": 1,
                "credentials_mounted": False,
                "providers_called": False,
                "images_pulled": False,
                "services_restarted": False,
                "proxy_restart_recovery": "not exercised",
            }
        finally:
            # Never enumerate or delete unrelated workers. Names cover a lost
            # create acknowledgment; IDs avoid deleting a reused name after ack.
            for identity in owned:
                call(["rm", "--force", identity])
            for name in uncertain:
                call(["rm", "--force", name])


def test_deployed_worker_boundary():
    report = exercise()
    assert report["creates_acknowledged"] == 2 and report["starts"] == 1


if __name__ == "__main__":
    print(json.dumps(exercise(), sort_keys=True))
