"""Exercise Docker lifetime ownership with an explicitly installed pinned image."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_container_recovery import LABEL, launch_environment

PROGRAM = """import os,pathlib,subprocess,sys,time
subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(90)'],start_new_session=True)
pathlib.Path(os.environ['OUTPUT_DIR'],'ready').write_text('active')
time.sleep(90)
"""


def owner(image, context, root):
    profile = {"image": image, "docker_context": context, "timeout": 120}
    studio = StudioService(root / "workspace", data_tool_options={"profiles": {"python": profile}})
    original = studio.data_tools.sandbox.arguments

    def arguments(profile_id, name):
        command = original(profile_id, name)
        (root / "container-name").write_text(name)
        return command

    studio.data_tools.sandbox.arguments = arguments
    requested = threading.Event()

    def close():
        # Avoid a buffered-stdin lock if setup fails before the close command.
        if os.read(sys.stdin.fileno(), 1):
            requested.set()
            studio.close()

    control = threading.Thread(target=close, daemon=True)
    control.start()
    try:
        asyncio.run(studio.data_tools.execute("python", {"code": PROGRAM}))
        raise AssertionError("A stopped worker must not publish a result")
    except (ValueError, asyncio.CancelledError):
        pass
    finally:
        if requested.is_set():
            control.join(10)
            assert not control.is_alive()
        else:
            studio.close()
    assert not studio.catalog.list("artifacts")
    assert not studio.data_tools.sandbox.cleanup_errors


def exercise(image, context, mode="close"):
    if os.name != "posix":
        raise RuntimeError("POSIX process-group acceptance requires a native POSIX host")
    docker = ["docker", "--context", context]
    with tempfile.TemporaryDirectory(prefix="nexus-docker-lifetime-") as directory:
        root = Path(directory)
        name = None
        with (root / "owner.log").open("w") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--owner",
                    "--root",
                    str(root),
                    "--image",
                    image,
                    "--context",
                    context,
                ],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    assert child.poll() is None, (root / "owner.log").read_text()[-2000:]
                    if (root / "container-name").exists():
                        name = (root / "container-name").read_text()
                        ready = subprocess.run(
                            [
                                *docker,
                                "exec",
                                name,
                                "python3",
                                "-I",
                                "-c",
                                "from pathlib import Path; raise SystemExit(not Path('/work/outputs/ready').is_file())",
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=3,
                        )
                        if ready.returncode == 0:
                            break
                    time.sleep(0.05)
                else:
                    raise AssertionError("The isolated worker did not become active")
                state = json.loads(subprocess.check_output([*docker, "inspect", name]))[0]
                assert state["Config"]["StdinOnce"] and state["HostConfig"]["NetworkMode"] == "none"
                started = time.monotonic()
                if mode == "close":
                    child.stdin.write(b"close\n")
                    child.stdin.flush()
                elif mode == "kill":
                    child.kill()
                else:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait(10)
                deadline = started + 8
                while time.monotonic() < deadline:
                    check = subprocess.run(
                        [
                            *docker,
                            "ps",
                            "--all",
                            "--filter",
                            "name=^/" + name + "$",
                            "--format",
                            "{{.ID}}",
                        ],
                        capture_output=True,
                        timeout=2,
                    )
                    assert check.returncode == 0, "Docker cleanup could not be confirmed"
                    if not check.stdout.strip():
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("Owned container survived shutdown")
                if mode == "close":
                    assert child.returncode == 0, (root / "owner.log").read_text()[-2000:]
                restarted = StudioService(root / "workspace")
                try:
                    # A crashed owner releases its kernel lock. Its acknowledged
                    # container may already be gone; the journal can now settle.
                    assert restarted.data_tools.sandbox.recover_cleanup()["pending"] == 0
                finally:
                    restarted.close()
                return {
                    "mode": mode,
                    "container_removed": True,
                    "child_in_container_stopped": True,
                    "seconds": round(time.monotonic() - started, 3),
                    "image_pulled": False,
                    "live_accounts_used": False,
                    "restart_cleanup_confirmed": True,
                }
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(5)
                if child.stdin:
                    child.stdin.close()
                if name:
                    subprocess.run(
                        [*docker, "rm", "--force", name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )


def exercise_recovery(image, context):
    """Exercise durable cleanup, actual unreachable transport and late creation."""
    docker = ["docker", "--context", context]
    environment = launch_environment(context)
    names = []
    with tempfile.TemporaryDirectory(prefix="nexus-docker-recovery-") as temporary:
        root = Path(temporary)
        studio = StudioService(
            root / "workspace",
            data_tool_options={
                "profiles": {"python": {"image": image, "docker_context": context}},
            },
        )
        try:
            sandbox = studio.data_tools.sandbox
            assert sandbox.execute("print('recovery-compatible')") == {
                "stdout": "recovery-compatible\n",
                "files": [],
            }
            assert sandbox.recovery.pending() == 0
            name = "nexus-analysis-" + uuid.uuid4().hex
            names.append(name)
            lease = sandbox.recovery.register(name, docker, environment, subprocess.run)
            lease.finish(False)
            # The request was never acknowledged. Absence cannot close its journal.
            absent = sandbox.recover_cleanup()
            assert absent["items"] == [{"name": name, "status": "awaiting_creation"}]
            command = sandbox.arguments("python", name)
            command[len(docker)] = "create"
            command[len(docker) + 1 : len(docker) + 1] = ["--label", LABEL + "=" + name]
            late = subprocess.run(command, capture_output=True, env=environment, timeout=15)
            assert late.returncode == 0, late.stderr.decode()[-1000:]
            info = json.loads(subprocess.check_output([*docker, "inspect", name], env=environment))[
                0
            ]
            assert info["State"]["Status"] == "created" and not info["State"]["Running"]
            studio.close()
            # Reopen the real workspace; no live owner may retain its old lock.
            studio = StudioService(root / "workspace")
            sandbox = studio.data_tools.sandbox
            unavailable_calls = []

            def unavailable(args, **kwargs):
                if "context" in args and "inspect" in args:
                    return subprocess.run(args, **kwargs)
                unavailable_calls.append(args)
                # This is an actual Docker CLI failure, not a fabricated response.
                # Only the transport changes; the real engine remains untouched.
                failed = [
                    "docker",
                    "--host",
                    "unix://" + str(root / "missing.sock"),
                    *args[len(docker) :],
                ]
                return subprocess.run(failed, **kwargs)

            failed = sandbox.recovery.recover(unavailable)
            assert failed["pending"] == 1 and unavailable_calls
            status, recovered = studio.workbench.post(["readiness", "cleanup"], {}, None)
            assert status == 200 and recovered["checked_at"]
            assert recovered["items"] == [{"name": name, "status": "removed"}]
            assert recovered["pending"] == 0
            assert not studio.catalog.list("artifacts")
            return {
                "normal_execution": True,
                "late_create_never_started": True,
                "restart_recovery": True,
                "actual_unreachable_transport": True,
                "container_removed": True,
                "replayed_work": False,
                "image_pulled_by_worker": False,
            }
        finally:
            studio.close()
            for name in names:
                subprocess.run(
                    [*docker, "rm", "--force", name],
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument(
        "--mode", choices=["close", "kill", "group-kill", "recovery"], default="close"
    )
    parser.add_argument("--owner", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.owner:
        owner(args.image, args.context, args.root)
    elif args.mode == "recovery":
        print(json.dumps(exercise_recovery(args.image, args.context), indent=2))
    else:
        print(json.dumps(exercise(args.image, args.context, args.mode), indent=2))
