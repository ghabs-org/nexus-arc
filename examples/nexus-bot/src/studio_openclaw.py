"""Exercise the compiled OpenClaw plugin against a disposable real Studio server.

From the repository root, build packages/nexus-arc first, then run:
PYTHONPATH=. python examples/nexus-bot/src/studio_openclaw.py
Only the language-model provider is deterministic; HTTP, jobs, artifacts and
approvals use the actual framework. This does not represent an installed gateway.
"""

import base64
import json
import os
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def exercise():
    secret_name = "NEXUS_OPENCLAW_EXAMPLE_SECRET"
    previous = os.environ.get(secret_name)
    os.environ[secret_name] = "synthetic-example-openclaw-" + "s" * 32
    artifact = {}

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(
                success=True,
                output="Reviewed example evidence",
                metadata={
                    "structured_content": [
                        {
                            "type": "artifact",
                            "artifact_id": artifact["id"],
                            "revision": artifact["revision"],
                        }
                    ]
                },
            )

    try:
        with tempfile.TemporaryDirectory(prefix="nexus-openclaw-example-") as directory:
            studio = StudioService(directory, provider_factory=lambda _: Provider())
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_studio_handler(studio, "example-csrf")
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                artifact.update(
                    studio.catalog.save(
                        "artifacts",
                        {
                            "name": "Report",
                            "data": {
                                "filename": "report.txt",
                                "content_base64": base64.b64encode(b"Example evidence").decode(),
                            },
                        },
                    )
                )
                studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
                for kind in ("approval", "input"):
                    studio.save_definition(
                        "workflows",
                        {
                            "name": kind,
                            "graph": {
                                "version": 2,
                                "nodes": [
                                    {
                                        "id": "gate",
                                        "kind": kind,
                                        **(
                                            {"output_schema": {"type": "string"}}
                                            if kind == "input"
                                            else {}
                                        ),
                                    },
                                    {
                                        "id": "count",
                                        "kind": "tool",
                                        "tool_id": "text:wordcount",
                                        "kwargs": {"text": "one two"},
                                        "depends_on": ["gate"],
                                    },
                                ],
                                "output_mapping": {"ref": "nodes.count"},
                            },
                        },
                    )
                studio.catalog.save(
                    "entrypoints",
                    {
                        "name": "OpenClaw",
                        "data": {
                            "type": "openclaw",
                            "enabled": True,
                            "target_kind": "agent",
                            "target_id": "reviewer",
                            "token_env": secret_name,
                            "identity_map": {"telegram:alice": "desktop"},
                            "allowed_channels": ["telegram:room"],
                            "operator_targets": [
                                "agents:reviewer",
                                "workflows:approval",
                                "workflows:input",
                            ],
                        },
                    },
                )
                result = subprocess.run(
                    [
                        "node",
                        str(Path(__file__).with_name("studio_openclaw_client.mjs")),
                        f"http://127.0.0.1:{server.server_port}",
                    ],
                    env=dict(os.environ),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                report = json.loads(result.stdout)
                assert len(studio.records("runs")) == 4
                assert all(run["status"] == "completed" for run in studio.records("runs"))
                print(json.dumps(report))
                return report
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                studio.close()
    finally:
        if previous is None:
            os.environ.pop(secret_name, None)
        else:
            os.environ[secret_name] = previous


if __name__ == "__main__":
    exercise()
