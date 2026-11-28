"""Exercise disposable server restart and inactive restore without accounts.

Run on POSIX: PYTHONPATH=. python examples/nexus-bot/src/studio_server_recovery.py
Add --sql to use the existing transactional SQLite state backend.
The drill starts loopback-only CLI processes; it never touches a running workspace.
"""

import argparse
import base64
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from nexus.core.studio import StudioService
from nexus.core.studio_backup import create_snapshot, restore_snapshot, verify_snapshot
from nexus.core.studio_state import StudioSQLState

REPOSITORY = Path(__file__).resolve().parents[3]


def _settled(studio, identity):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        run = studio.run(identity)
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.02)
    raise AssertionError("Disposable indexing or approval run did not settle")


def _visit(root, state, directory, run_id, *, runtime=None, abrupt=False, environment=None):
    command = [
        sys.executable,
        "-m",
        "nexus.cli",
        "studio",
        "--dir",
        str(root),
        "--state-dir",
        str(state),
        "--port",
        "0",
    ]
    if runtime:
        command.extend(["--runtime-config", str(runtime)])
    log = directory / ("server-" + str(time.time_ns()) + ".log")
    with log.open("w") as output, requests.Session() as client:
        client.trust_env = False
        child = subprocess.Popen(
            command,
            cwd=REPOSITORY,
            stdout=output,
            stderr=output,
            env={**os.environ, **(environment or {}), "PYTHONUNBUFFERED": "1"},
        )
        try:
            deadline = time.monotonic() + 20
            address = None
            while time.monotonic() < deadline:
                assert child.poll() is None, log.read_text()
                address = next(
                    (
                        line.removeprefix("Nexus Studio: ")
                        for line in log.read_text().splitlines()
                        if line.startswith("Nexus Studio: http://127.0.0.1:")
                    ),
                    None,
                )
                if address:
                    try:
                        response = client.get(address + "/api/bootstrap", timeout=1)
                        if response.status_code == 200:
                            break
                    except requests.ConnectionError:
                        pass
                time.sleep(0.05)
            else:
                raise AssertionError("Disposable server readiness timed out: " + log.read_text())
            response = client.get(address + "/api/agents", timeout=3)
            response.raise_for_status()
            assert any(item["id"] == "reviewer" for item in response.json()["items"])
            response = client.get(address + "/api/runs/" + run_id, timeout=3)
            response.raise_for_status()
            assert response.json()["status"] == "waiting", response.text
            second = subprocess.run(
                command,
                cwd=REPOSITORY,
                capture_output=True,
                timeout=20,
                env={**os.environ, **(environment or {})},
            )
            assert second.returncode == 75, second.stderr.decode(errors="replace")
            child.send_signal(signal.SIGKILL if abrupt else signal.SIGTERM)
            assert child.wait(timeout=15) == (-signal.SIGKILL if abrupt else 0), log.read_text()
            try:
                client.get(address + "/api/bootstrap", timeout=1)
            except requests.ConnectionError:
                pass
            else:
                raise AssertionError("Disposable server listener survived shutdown")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


def exercise(*, sql=False):
    if os.name != "posix":
        raise RuntimeError("This explicit SIGTERM/SIGKILL drill requires a POSIX host")
    with tempfile.TemporaryDirectory(prefix="nexus-server-recovery-") as temporary:
        directory = Path(temporary)
        root, state = directory / "project", directory / "private"
        url = "sqlite:///" + str(directory / "state.sqlite")
        options = {"url": url, "namespace": "source", "workers": 1} if sql else None
        runtime = None
        environment = {}
        if sql:
            runtime = directory / "runtime.json"
            runtime.write_text(
                json.dumps(
                    {
                        "state": {
                            "url_env": "NEXUS_RECOVERY_STATE_URL",
                            "namespace": "source",
                            "workers": 1,
                        }
                    }
                )
            )
            runtime.chmod(0o600)
            environment["NEXUS_RECOVERY_STATE_URL"] = url
        studio = StudioService(root, state_dir=state, state_options=options)
        try:
            studio.save_definition("agents", {"name": "Reviewer", "instructions": "Cite policy"})
            studio.save_definition(
                "workflows",
                {
                    "name": "Review policy",
                    "graph": {
                        "nodes": [{"id": "gate", "name": "Review first", "kind": "approval"}],
                        "edges": [{"source": "START", "target": "gate"}],
                    },
                },
            )
            project = studio.knowledge.save({"name": "Policy"}, None)
            artifact = studio.catalog.save(
                "artifacts",
                {
                    "name": "Policy",
                    "data": {
                        "filename": "policy.txt",
                        "content_base64": base64.b64encode(b"Refunds within thirty days").decode(),
                    },
                },
            )
            document = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
            assert _settled(studio, document["run_id"])["status"] == "completed"
            studio.catalog.save(
                "artifacts",
                {
                    "id": artifact["id"],
                    "revision": 1,
                    "name": "Policy",
                    "data": {
                        "filename": "policy.txt",
                        "content_base64": base64.b64encode(b"Replacement policy").decode(),
                    },
                },
            )
            run = studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": "review-policy",
                    "task": "Wait for human review",
                }
            )
            assert _settled(studio, run["id"])["status"] == "waiting"
            run_ids = {record["id"] for record in studio.records("runs")}
        finally:
            studio.close()
        for abrupt in (False, True, False):
            _visit(
                root,
                state,
                directory,
                run["id"],
                runtime=runtime,
                abrupt=abrupt,
                environment=environment,
            )
        snapshot = directory / "backup"
        store = StudioSQLState(url, "source") if sql else None
        try:
            create_snapshot(root, snapshot, state_dir=state, sql_state=store, quiesced=True)
        finally:
            if store:
                store.engine.dispose()
        assert verify_snapshot(snapshot)["files"]
        restored = restore_snapshot(snapshot, directory / "restored")
        assert restored["review_required"]
        _visit(Path(restored["root"]), Path(restored["state_dir"]), directory, run["id"])
        for selected_root, selected_state, selected_options in (
            (root, state, options),
            (restored["root"], restored["state_dir"], None),
        ):
            copy = StudioService(
                selected_root, state_dir=selected_state, state_options=selected_options
            )
            try:
                assert copy.run(run["id"])["status"] == "waiting"
                assert {record["id"] for record in copy.records("runs")} == run_ids
                assert copy.validate_workflow("review-policy")["valid"]
                assert (
                    copy.artifacts.get(copy.artifacts.revision(artifact["id"], 1))
                    == b"Refunds within thirty days"
                )
                assert (
                    copy.artifacts.get(copy.artifacts.revision(artifact["id"]))
                    == b"Replacement policy"
                )
                assert (
                    copy.knowledge.search(project["id"], "refunds", None)["items"][0][
                        "artifact_revision"
                    ]
                    == 1
                )
            finally:
                copy.close()
        return {
            "backend": "sqlite" if sql else "files",
            "server_launches": 4,
            "sigterm_cleanup": True,
            "sigkill_restart": True,
            "exclusive_writer": True,
            "inactive_restore": True,
            "artifact_revisions_and_citations": True,
            "approval_retained": True,
            "extra_runs_created": 0,
            "external_account_calls": 0,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", action="store_true")
    print(json.dumps(exercise(sql=parser.parse_args().sql), indent=2))
