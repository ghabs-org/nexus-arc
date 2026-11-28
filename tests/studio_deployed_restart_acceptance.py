"""Explicit two-phase acceptance across a selected Studio/proxy restart.

An operator controls the service restart outside this module. Only synthetic
workers and a disposable file-state workspace are used; no production run,
credential, provider, database, Docker daemon or other service is modified here.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_container_recovery import LABEL, launch_environment
from nexus.core.studio_docker_route import pin_route

PROGRAM = """import os,pathlib,subprocess,sys,time
out=pathlib.Path(os.environ['OUTPUT_DIR'])
child="import pathlib,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(180)"
subprocess.Popen([sys.executable,'-c',child,str(out/'detached-ready')],start_new_session=True)
deadline=time.monotonic()+5
while not (out/'detached-ready').exists():
    if time.monotonic()>deadline:raise RuntimeError('Detached child failed to become ready')
    time.sleep(0.02)
(out/'ready').write_text('active')
time.sleep(110)
"""


def _write(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data))
    temporary.chmod(0o600)
    temporary.replace(path)


def prepare(root):
    """Hold an unstarted lease and run a worker until the controller exits."""
    image = os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"]
    scope = os.environ.get("NEXUS_VALIDATION_SCOPE")
    if scope:
        assert re.fullmatch(r"[a-zA-Z0-9-]{1,80}", scope)
    root = Path(root)
    assert root.is_dir() and not root.is_symlink()
    assert not Path("/var/run/docker.sock").exists()
    studio = StudioService(
        root / "workspace",
        data_tool_options={"profiles": {"python": {"image": image, "timeout": 120}}},
    )
    sandbox = studio.data_tools.sandbox
    environment = launch_environment(None)
    state = {"running_id": None, "running_name": None, "owner_finished": False}
    lease = None
    worker = None
    try:
        with pin_route(["docker"], environment, subprocess.run) as route:
            name = "nexus-analysis-" + uuid.uuid4().hex
            lease = sandbox.recovery.register(
                name, ["docker"], environment, subprocess.run, route_sha256=route.fingerprint
            )
            command = sandbox.arguments("python", name)
            command[1] = "create"
            command[2:2] = ["--label", LABEL + "=" + name]
            if scope:
                command[2:2] = ["--label", "nexus.studio.validation=" + scope]
            command[:1] = route.command
            created = subprocess.run(
                command, env=route.environment, capture_output=True, timeout=10
            )
            assert created.returncode == 0, "Stopped fixture creation failed"
            identity = created.stdout.decode().strip()
            assert re.fullmatch(r"[a-f0-9]{64}", identity)
            lease.created(identity)
            state.update(stopped_id=identity, stopped_name=name)
            _write(root / "ready.json", state)
            original = sandbox.arguments

            def arguments(profile, selected):
                result = original(profile, selected)
                if scope:
                    position = result.index("--entrypoint")
                    result[position:position] = ["--label", "nexus.studio.validation=" + scope]
                state["running_name"] = selected
                _write(root / "ready.json", state)
                return result

            sandbox.arguments = arguments

            def run():
                try:
                    sandbox.execute(PROGRAM)
                except BaseException:
                    pass
                finally:
                    state["owner_finished"] = True
                    _write(root / "ready.json", state)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            deadline = time.monotonic() + 100
            while not (root / "abort").exists():
                # Execution creates through Popen. Observe the committed receipt,
                # not an unrelated run-command hook or an unacknowledged name.
                if state["running_name"] and not state["running_id"]:
                    record = sandbox.recovery.directory / state["running_name"] / "record.json"
                    if record.exists():
                        acknowledged = studio._read(record).get("container_id")
                        if acknowledged:
                            assert re.fullmatch(r"[a-f0-9]{64}", acknowledged)
                            state["running_id"] = acknowledged
                            _write(root / "ready.json", state)
                assert not state["owner_finished"], "Worker ended before the controlled restart"
                assert time.monotonic() < deadline, "Operator did not restart the controller"
                time.sleep(0.05)
            # This path aborts preparation without touching any other worker.
    finally:
        sandbox.close()
        if worker:
            worker.join(10)
        if lease:
            lease.finish(False)
        sandbox.recover_cleanup()
        studio.close()


def verify(root):
    """Require restored cleanup-only authority and retire both saved records."""
    root = Path(root)
    state = json.loads((root / "ready.json").read_text())
    assert not state["owner_finished"], "Restart did not interrupt the disposable owner"
    stopped = state["stopped_id"]
    assert re.fullmatch(r"[a-f0-9]{64}", stopped)
    studio = StudioService(root / "workspace")
    success = False
    try:
        denied = subprocess.run(["docker", "start", stopped], capture_output=True, timeout=10)
        assert denied.returncode != 0 and b"cleanup only" in denied.stderr.lower(), (
            "Restored authority permitted execution or failed for another reason"
        )
        inspected = subprocess.run(
            ["docker", "container", "inspect", "--format", "{{.State.Status}}", stopped],
            capture_output=True,
            timeout=5,
        )
        assert inspected.returncode == 0 and inspected.stdout.strip() == b"created"
        commands = []

        def observed(command, **kwargs):
            commands.append(command)
            return subprocess.run(command, **kwargs)

        recovered = studio.data_tools.sandbox.recovery.recover(observed, timeout=20)
        assert recovered["pending"] == 0
        assert {item["name"]: item["status"] for item in recovered["items"]} == {
            state["running_name"]: "removed",
            state["stopped_name"]: "removed",
        }
        assert all("create" not in command and "start" not in command for command in commands)
        assert not studio.records("runs") and not studio.catalog.list("artifacts")
        success = True
        return {
            "restored_start_denied": True,
            "stopped_work_never_started": True,
            "running_and_stopped_records_removed": True,
            "recovery_replayed_create_or_start": False,
            "unresolved_cleanup": 0,
            "extra_runs": 0,
            "extra_artifacts": 0,
        }
    finally:
        studio.close()
        if success:
            shutil.rmtree(root)
