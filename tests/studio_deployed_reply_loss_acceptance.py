"""Exercise the installed recovery implementation over actual lost responses."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from nexus.core.studio import StudioService


def exercise(proxy_factory):
    upstream = os.environ["DOCKER_HOST"]
    image = os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"]
    assert upstream.startswith("unix:///")
    assert not Path("/var/run/docker.sock").exists()
    root = Path(tempfile.mkdtemp(prefix="nexus-reply-loss-"))
    studio = None
    success = False
    marker = "This generated program must never run"
    try:
        with proxy_factory(root / "fault.sock", upstream.removeprefix("unix://")) as fault:
            os.environ["DOCKER_HOST"] = "unix://" + str(root / "fault.sock")
            studio = StudioService(
                root / "workspace",
                data_tool_options={"profiles": {"python": {"image": image}}},
            )
            sandbox = studio.data_tools.sandbox
            original = sandbox.arguments

            def arguments(profile, name):
                command = original(profile, name)
                scope = os.environ["NEXUS_VALIDATION_SCOPE"]
                position = command.index("--entrypoint")
                command[position:position] = ["--label", "nexus.studio.validation=" + scope]
                return command

            sandbox.arguments = arguments
            try:
                try:
                    sandbox.execute("print(" + repr(marker) + ")")
                except ValueError as error:
                    assert "not acknowledged" in str(error)
                else:
                    raise AssertionError("Unacknowledged creation returned a successful program")
                assert sandbox.recovery.pending() == 1
                records = list(sandbox.recovery.directory.glob("*/record.json"))
                record = studio._read(records[0])
                assert record["container_id"] is None and record["creation_uncertain"]
                assert marker not in records[0].read_text()
                assert fault.created_id, "No actual container was created before losing the reply"
                fault.set_mode("lose-delete")
                first = sandbox.recover_cleanup(timeout=15)
                assert first["pending"] == 1
                assert first["items"][0]["status"] == "unconfirmed"
                acknowledged = studio._read(records[0])
                assert acknowledged["container_id"] == fault.created_id
                assert acknowledged["creation_uncertain"] is False
                counts = fault.snapshot()
                assert counts["lost_create_replies"] == counts["lost_delete_replies"] == 1
                fault.set_mode("passthrough")
                second = sandbox.recover_cleanup(timeout=15)
                assert second["pending"] == 0
                assert second["items"][0]["status"] == "removed"
                counts = fault.snapshot()
                assert counts["create_forwarded"] == counts["delete_forwarded"] == 1
                assert counts["start_requests"] == 0 and not counts["unexpected_errors"]
                assert not studio.records("runs") and not studio.catalog.list("artifacts")
                # Use the original upstream for independent authoritative absence.
                absent = subprocess.run(
                    [
                        "docker",
                        "--host",
                        upstream,
                        "container",
                        "inspect",
                        "--format",
                        "{{.Id}}",
                        fault.created_id,
                    ],
                    capture_output=True,
                    timeout=5,
                )
                assert absent.returncode != 0 and b"no such" in absent.stderr.lower()
                success = True
                return {
                    **counts,
                    "create_uncertainty_retained": True,
                    "delete_uncertainty_retained": True,
                    "final_absence_confirmed": True,
                    "cleanup_did_not_repeat_delete": True,
                    "generated_program_started": False,
                    "unresolved_cleanup": 0,
                    "extra_runs": 0,
                    "extra_artifacts": 0,
                }
            finally:
                fault.set_mode("passthrough")
                sandbox.recover_cleanup(timeout=8)
                studio.close()
                studio = None
    finally:
        os.environ["DOCKER_HOST"] = upstream
        if studio:
            studio.close()
        if success:
            shutil.rmtree(root)
