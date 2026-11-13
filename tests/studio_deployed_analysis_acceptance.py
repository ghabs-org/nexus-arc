"""Explicit real-worker gate, reusable inside a deployed Studio controller.

The caller supplies the existing data-worker example namespace. This keeps the
actual six-operation consumer shared with local acceptance. No live user state,
credentials, provider client or HTTP authentication substitute is involved.
"""

import json
import os
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from nexus.core.studio import StudioService

ISOLATION = """import errno,os,pathlib,resource,socket
assert os.getuid()==65534 and os.getgid()==65534
assert resource.getrlimit(resource.RLIMIT_NOFILE)==(256,256)
assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip()=='256'
assert not pathlib.Path('/proc/net/route').read_text().splitlines()[1:]
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/var/lib/nexus-studio/project').exists()
assert not pathlib.Path('/run/credentials').exists()
with socket.socket() as probe:
    probe.settimeout(1)
    assert probe.connect_ex(('192.0.2.1',443))==errno.ENETUNREACH
try:pathlib.Path('/host-write-must-fail').write_text('x')
except OSError:pass
else:raise AssertionError('Root filesystem writable')
print('isolated')
"""

STOPPED_OWNER = r"""
import json,os,subprocess,sys,time,uuid
from nexus.core.studio import StudioService
from nexus.core.studio_container_recovery import LABEL,launch_environment
from nexus.core.studio_docker_route import pin_route
image,root=sys.argv[1:]
studio=StudioService(root,data_tool_options={'profiles':{'python':{'image':image}}})
sandbox=studio.data_tools.sandbox
name='nexus-analysis-'+uuid.uuid4().hex
environment=launch_environment(None)
with pin_route(['docker'],environment,subprocess.run) as route:
    lease=sandbox.recovery.register(name,['docker'],environment,subprocess.run,
                                   route_sha256=route.fingerprint)
    command=sandbox.arguments('python',name)
    command[1]='create'
    command[2:2]=['--label',LABEL+'='+name]
    command[:1]=route.command
    created=subprocess.run(command,env=route.environment,capture_output=True,timeout=10)
    if created.returncode:raise RuntimeError('Stopped worker creation failed')
    identity=created.stdout.decode().strip()
    lease.created(identity)
    print(json.dumps({'id':identity,'name':name}),flush=True)
    # Keep the kernel ownership lock held. SIGKILL prevents Python finally/close
    # from performing cleanup; only a later explicit recovery may remove it.
    time.sleep(120)
"""


def exercise(example, *, runtime_config=None):
    image = os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"]
    assert os.environ.get("DOCKER_HOST", "").startswith("unix:///"), (
        "Select the filtered Unix socket explicitly"
    )
    assert not Path("/var/run/docker.sock").exists(), "Controller exposes the raw Docker socket"
    assert not Path("/var/lib/nexus-proxy-private").exists(), (
        "Controller exposes private proxy state"
    )
    directory = Path(tempfile.mkdtemp(prefix="nexus-analysis-validation-"))
    studio = None
    success = False
    try:
        if runtime_config:
            from nexus.core.studio_runtime import load_runtime_configuration

            # Validate the actual mounted configuration but consume only its data
            # profiles. Never connect the disposable workspace to production SQL.
            options = load_runtime_configuration(runtime_config, directory)
            profiles = options["data_tool_options"]["profiles"]
            assert set(profiles) == {"data", "python", "media"}
            assert all(profile["image"] == image for profile in profiles.values())
            assert all(not profile.get("docker_context") for profile in profiles.values())
        else:
            profiles = example["profiles"](image, None)
        studio = StudioService(directory / "workspace", data_tool_options={"profiles": profiles})
        assert studio.sql_state is None, "Disposable validation must not use production SQL"
        workflow = example["exercise"](studio)
        assert workflow["artifacts"] == 6
        sandbox = studio.data_tools.sandbox
        assert sandbox.execute(ISOLATION) == {"stdout": "isolated\n", "files": []}
        rejected = 0
        for code, expected in [
            (
                "import os,pathlib\np=pathlib.Path(os.environ['OUTPUT_DIR'])\n"
                "(p/'link').symlink_to('/etc/passwd')",
                "links",
            ),
            (
                "import os,pathlib\np=pathlib.Path(os.environ['OUTPUT_DIR'])/'nested'\n"
                "p.mkdir()\n(p/'file').write_text('x')",
                "directly",
            ),
            (
                "import os,pathlib\np=pathlib.Path(os.environ['OUTPUT_DIR'])\n"
                "for i in range(9):(p/str(i)).write_text('x')",
                "limit",
            ),
        ]:
            try:
                sandbox.execute(code)
            except ValueError as error:
                assert expected in str(error), "Unexpected safe-output validation outcome"
                rejected += 1
            else:
                raise AssertionError("Unsafe artifact output was accepted")
        assert sandbox.recovery.pending() == 0, "Worker cleanup remains unresolved"
        report = {
            "workflow_operations": workflow["operations"],
            "private_artifacts": 6,
            "read_only_sql_total": 7,
            "jq_total": 7,
            "chart_dimensions": [1200, 800],
            "image_dimensions": [2, 2],
            "worker_uid_gid": "65534:65534",
            "networkless_readonly_no_host_mounts": True,
            "unsafe_outputs_rejected": rejected,
            "unresolved_cleanup": 0,
            "configured_tools": sum(
                item["configured"] for item in studio.data_tools.inventory()["items"]
            ),
            "production_state_used": False,
            "provider_calls": 0,
            "mounted_runtime_profiles_used": bool(runtime_config),
        }
        assert report["configured_tools"] == 6
        success = True
        return report
    finally:
        if studio:
            studio.close()
        # Keep failed workspaces/journals for operator review. Only a fully
        # successful probe can discard its own temporary artifacts and state.
        if success:
            shutil.rmtree(directory)


def test_deployed_analysis_consumer():
    import runpy

    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_data_workers.py")
    )
    report = exercise(example, runtime_config=os.environ.get("NEXUS_ACCEPTANCE_RUNTIME_CONFIG"))
    assert report["unsafe_outputs_rejected"] == 3


def exercise_owner_recovery():
    """Kill only this gate's owner, then recover its acknowledged stopped worker."""
    image = os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"]
    directory = Path(tempfile.mkdtemp(prefix="nexus-analysis-owner-validation-"))
    child = None
    studio = None
    identity = None
    success = False
    try:
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", STOPPED_OWNER, image, str(directory / "workspace")],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(30), "Disposable stopped-worker owner did not acknowledge creation"
            frame = child.stdout.readline(4096)
        acknowledged = json.loads(frame)
        identity = acknowledged["id"]
        name = acknowledged["name"]
        assert re.fullmatch(r"[a-f0-9]{64}", identity)
        assert re.fullmatch(r"nexus-analysis-[a-f0-9]{32}", name)
        assert child.poll() is None, "Owner died before the deliberate failure"
        child.kill()
        child.wait(5)
        assert child.returncode == -9
        studio = StudioService(directory / "workspace")
        before = subprocess.run(
            ["docker", "container", "inspect", "--format", "{{.State.Status}}", identity],
            capture_output=True,
            timeout=5,
        )
        assert before.returncode == 0 and before.stdout.strip() == b"created", (
            "Stopped work was started or removed before explicit recovery"
        )
        commands = []

        def observed(command, **kwargs):
            commands.append(command)
            return subprocess.run(command, **kwargs)

        recovered = studio.data_tools.sandbox.recovery.recover(observed, timeout=15)
        assert recovered == {"items": [{"name": name, "status": "removed"}], "pending": 0}
        assert all("create" not in command and "start" not in command for command in commands)
        assert not studio.records("runs") and not studio.catalog.list("artifacts")
        after = subprocess.run(
            ["docker", "container", "inspect", "--format", "{{.Id}}", identity],
            capture_output=True,
            timeout=5,
        )
        assert after.returncode != 0 and b"no such" in after.stderr.lower()
        success = True
        return {
            "disposable_owner_sigkill": True,
            "acknowledged_stopped_work_preserved": True,
            "explicit_cleanup_confirmed": True,
            "unresolved_cleanup": 0,
            "replayed_create_or_start": False,
            "extra_runs": 0,
            "extra_artifacts": 0,
            "studio_or_proxy_restarted": False,
            "daemon_restarted": False,
        }
    finally:
        if child:
            if child.poll() is None:
                child.kill()
                child.wait(5)
            child.stdout.close()
        if studio:
            studio.close()
        if identity:
            subprocess.run(["docker", "rm", "--force", identity], capture_output=True, timeout=5)
        if success:
            shutil.rmtree(directory)


def test_deployed_owner_recovery():
    assert exercise_owner_recovery()["explicit_cleanup_confirmed"]
