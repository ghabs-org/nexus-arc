"""Run Studio recovery through a disposable filtering Docker proxy.

Uses only a preinstalled immutable image, a private Unix socket and containers
created by this drill. No network ports, credentials or user settings change.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from nexus.core.studio import StudioService
from nexus.core.studio_container_recovery import LABEL, launch_environment

ROOT = Path(__file__).resolve().parents[3]


def exercise(image, context, mode="recovery"):
    if mode not in {"recovery", "failed-remove", "alias", "restart"}:
        raise ValueError("Choose recovery, failed-remove, alias or restart")
    direct_environment = launch_environment(context)
    direct = ["docker", "--context", context]
    inspected = subprocess.run(
        [*direct, "context", "inspect"],
        env=direct_environment,
        capture_output=True,
        timeout=5,
        check=True,
    )
    endpoint = json.loads(inspected.stdout)[0]["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://"):
        raise ValueError("This local drill requires a Unix-socket context")
    # Keep Unix socket paths below the portable socket-path limit.
    with tempfile.TemporaryDirectory(prefix="nx-proxy-", dir="/tmp") as temporary:
        directory = Path(temporary)
        runtime = directory / "runtime.json"
        runtime.write_text(json.dumps({"analysis_workers": {"python": {"image": image}}}))
        runtime.chmod(0o600)
        socket = directory / "proxy.sock"
        environment = {**direct_environment, "DOCKER_HOST": "unix://" + str(socket)}
        proxy_environment = {
            **direct_environment,
            "PROXY_LISTEN": str(socket),
            "PROXY_UPSTREAM": endpoint[7:],
            "STUDIO_RUNTIME_CONFIG": str(runtime),
            "STUDIO_TEAM_CONFIG": str(directory / "no-team.json"),
        }
        docker = ["docker", "--host", "unix://" + str(socket)]
        identities, clients = [], []
        log = directory / "proxy.log"

        def run(args, **kwargs):
            return subprocess.run(args, capture_output=True, timeout=10, **kwargs)

        with log.open("w") as output:

            def start_proxy():
                child = subprocess.Popen(
                    [sys.executable, "-u", str(ROOT / "deploy/studio/docker_proxy.py")],
                    env=proxy_environment,
                    stdout=output,
                    stderr=output,
                )
                deadline = time.monotonic() + 10
                while not socket.is_socket():
                    assert child.poll() is None, log.read_text()
                    assert time.monotonic() < deadline, log.read_text()
                    time.sleep(0.02)
                return child

            proxy = start_proxy()
            with patch.dict(os.environ, environment, clear=True):
                studio = StudioService(
                    directory / "workspace",
                    data_tool_options={
                        "profiles": {"python": {"image": image}},
                    },
                )
                sandbox = studio.data_tools.sandbox
                try:
                    assert sandbox.execute("print('proxy-compatible')") == {
                        "stdout": "proxy-compatible\n",
                        "files": [],
                    }
                    assert sandbox.recovery.pending() == 0
                    name = "nexus-analysis-" + uuid.uuid4().hex
                    lease = sandbox.recovery.register(name, ["docker"], environment, subprocess.run)
                    try:
                        command = sandbox.arguments("python", name)
                        command[1] = "create"
                        command[2:2] = ["--label", LABEL + "=" + name]
                        created = run(command, env=environment)
                        assert created.returncode == 0, created.stderr.decode()
                        identity = created.stdout.decode().strip()
                        identities.append(identity)
                        lease.created(identity)
                    finally:
                        lease.finish(False)
                    if mode in {"failed-remove", "alias"}:
                        client = subprocess.Popen(
                            [*docker, "start", "--attach", "--interactive", identity],
                            env=environment,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                        clients.append(client)
                        client.stdin.write(
                            json.dumps(
                                {
                                    "code": "import time; time.sleep(60)"
                                    if mode == "failed-remove"
                                    else "print('done')",
                                    "files": [],
                                    "timeout": 120,
                                    "open_files": 64,
                                }
                            ).encode()
                            + b"\n"
                        )
                        client.stdin.flush()
                        if mode == "alias":
                            assert client.wait(10) == 0
                            # A different container reuses the old, now absent name.
                            command = sandbox.arguments("python", name)
                            command[1] = "create"
                            outsider = run([*direct, *command[1:]], env=direct_environment)
                            assert outsider.returncode == 0, outsider.stderr.decode()
                            other_id = outsider.stdout.decode().strip()
                            identities.append(other_id)
                            # Docker may treat forced removal of an absent ID as
                            # success. Check the actual other container instead.
                            run([*docker, "rm", "--force", name], env=environment)
                            assert (
                                run(
                                    [*direct, "inspect", other_id], env=direct_environment
                                ).returncode
                                == 0
                            )
                            assert (
                                run([*docker, "inspect", other_id], env=environment).returncode != 0
                            )
                        else:
                            deadline = time.monotonic() + 5
                            while True:
                                state = run(
                                    [
                                        *direct,
                                        "inspect",
                                        "--format",
                                        "{{.State.Running}}",
                                        identity,
                                    ],
                                    env=direct_environment,
                                )
                                if state.returncode == 0 and state.stdout.strip() == b"true":
                                    break
                                assert time.monotonic() < deadline
                                time.sleep(0.02)
                            refused = run([*docker, "rm", identity], env=environment)
                            assert refused.returncode != 0 and b"is running" in refused.stderr
                            forced = run([*docker, "rm", "--force", identity], env=environment)
                            assert forced.returncode == 0, forced.stderr.decode()
                            client.wait(10)
                    elif mode == "restart":
                        proxy.terminate()
                        proxy.wait(5)
                        socket.unlink(missing_ok=True)
                        proxy = start_proxy()
                    report = sandbox.recover_cleanup()
                    if mode == "restart":
                        assert report == {
                            "items": [{"name": name, "status": "unconfirmed"}],
                            "pending": 1,
                        }, report
                        assert (
                            run([*direct, "inspect", identity], env=direct_environment).returncode
                            == 0
                        )
                    else:
                        assert report == {
                            "items": [{"name": name, "status": "removed"}],
                            "pending": 0,
                        }, report
                    assert not studio.records("runs") and not studio.catalog.list("artifacts")
                    return {
                        "mode": mode,
                        "normal_execution": True,
                        "ownership_preserved": True,
                        "restart_requires_review": mode == "restart",
                        "replayed_work": False,
                        "images_pulled": False,
                        "external_accounts_used": False,
                    }
                finally:
                    studio.close()
                    for identity in identities:
                        run([*direct, "rm", "--force", identity], env=direct_environment)
                    for client in clients:
                        if client.poll() is None:
                            client.kill()
                        client.communicate(timeout=5)
                    if proxy.poll() is None:
                        proxy.terminate()
                        proxy.wait(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument(
        "--mode", choices=["recovery", "failed-remove", "alias", "restart"], default="recovery"
    )
    args = parser.parse_args()
    print(json.dumps(exercise(args.image, args.context, args.mode), indent=2))
