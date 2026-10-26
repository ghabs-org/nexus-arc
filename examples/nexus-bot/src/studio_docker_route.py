"""Fault-inject context changes against an installed local Docker test image.

Only a disposable context/configuration is changed. No images are pulled and no
user context is updated. Run with --image sha256:... --context default.
"""

import argparse
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from nexus.core.studio import StudioService
from nexus.core.studio_container_recovery import LABEL, launch_environment


def exercise(image, context, mode="dispatch"):
    if mode not in {"dispatch", "selection", "recovery"}:
        raise ValueError("Choose dispatch, selection or recovery")
    original_environment = launch_environment(context)
    inspected = subprocess.run(
        ["docker", "--context", context, "context", "inspect"],
        env=original_environment,
        capture_output=True,
        timeout=5,
        check=True,
    )
    host = json.loads(inspected.stdout)[0]["Endpoints"]["docker"]["Host"]
    if not host.startswith("unix://"):
        raise ValueError("This local acceptance drill requires a Unix-socket context")
    with tempfile.TemporaryDirectory(prefix="nexus-docker-route-check-") as temporary:
        root = Path(temporary)
        config = root / "operator"
        config.mkdir(mode=0o700)
        selected = "nexus-route-check"
        docker = ["docker", "--config", str(config), "--context", selected]
        environment = {**launch_environment(selected), "DOCKER_CONFIG": str(config)}

        def update(endpoint):
            subprocess.run(
                [*docker, "context", "update", selected, "--docker", "host=" + endpoint],
                env=environment,
                capture_output=True,
                timeout=5,
                check=True,
            )

        subprocess.run(
            [
                "docker",
                "--config",
                str(config),
                "context",
                "create",
                selected,
                "--docker",
                "host=" + host,
            ],
            env=environment,
            capture_output=True,
            timeout=5,
            check=True,
        )
        if mode == "selection":
            subprocess.run(
                [
                    *docker,
                    "context",
                    "create",
                    "unselected",
                    "--docker",
                    "host=unix://" + str(root / "unreachable.sock"),
                ],
                env=environment,
                capture_output=True,
                timeout=5,
                check=True,
            )
            subprocess.run(
                [*docker, "context", "use", selected],
                env=environment,
                capture_output=True,
                timeout=5,
                check=True,
            )
        changed = []
        real_popen = subprocess.Popen

        def switch():
            if mode == "selection":
                subprocess.run(
                    [*docker, "context", "use", "unselected"],
                    env=environment,
                    capture_output=True,
                    timeout=5,
                    check=True,
                )
            else:
                update("unix://" + str(root / "unreachable.sock"))
            changed.append(True)

        with patch.dict(os.environ, environment, clear=True):
            studio = StudioService(
                root / "workspace",
                data_tool_options={
                    "profiles": {
                        "python": {
                            "image": image,
                            **({} if mode == "selection" else {"docker_context": selected}),
                        }
                    }
                },
            )
            sandbox = studio.data_tools.sandbox
            try:
                if mode in {"dispatch", "selection"}:

                    def start(args, **kwargs):
                        if "start" in args and not changed:
                            switch()
                        return real_popen(args, **kwargs)

                    with patch("nexus.core.studio_sandbox.subprocess.Popen", start):
                        result = sandbox.execute("print('same-daemon')")
                    assert result == {"stdout": "same-daemon\n", "files": []}
                    assert sandbox.recovery.pending() == 0
                else:
                    name = "nexus-analysis-" + uuid.uuid4().hex
                    original_docker = ["docker", "--context", selected]
                    lease = sandbox.recovery.register(
                        name,
                        original_docker,
                        environment,
                        subprocess.run,
                    )
                    try:
                        command = sandbox.arguments("python", name)
                        command[3] = "create"
                        command[4:4] = ["--label", LABEL + "=" + name]
                        created = subprocess.run(
                            command,
                            capture_output=True,
                            env=environment,
                            timeout=15,
                            check=True,
                        )
                        lease.created(created.stdout.decode().strip())
                    finally:
                        lease.finish(False)

                    def recover(args, **kwargs):
                        if "ps" in args and not changed:
                            switch()
                        return subprocess.run(args, **kwargs)

                    result = sandbox.recovery.recover(recover)
                    assert result == {
                        "items": [{"name": name, "status": "removed"}],
                        "pending": 0,
                    }, result
                assert changed == [True]
                assert not studio.catalog.list("artifacts")
                assert not studio.records("runs")
                return {
                    "mode": mode,
                    "context_changed_once": True,
                    "original_daemon_used": True,
                    "pending_cleanup": 0,
                    "external_accounts_used": False,
                    "images_pulled": False,
                }
            finally:
                update(host)
                if mode == "selection":
                    subprocess.run(
                        [*docker, "context", "use", selected],
                        env=environment,
                        capture_output=True,
                        timeout=5,
                        check=True,
                    )
                studio.close()
                sandbox.recover_cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--mode", choices=["dispatch", "selection", "recovery"], default="dispatch")
    args = parser.parse_args()
    print(json.dumps(exercise(args.image, args.context, args.mode), indent=2))
