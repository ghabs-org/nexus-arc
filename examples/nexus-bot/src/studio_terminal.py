"""Demonstrate the framework terminal client without model accounts or user state.

Run from the repository root: PYTHONPATH=. python examples/nexus-bot/src/studio_terminal.py
The disposable server and deterministic provider are example wiring only.
"""

import base64
import json
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def exercise():
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            for item in context.metadata.get("attachment_files", []):
                assert Path(item["path"]).read_text() == "Example private evidence"
            return AgentResult(success=True, output="Reviewed example evidence")

    with tempfile.TemporaryDirectory(prefix="nexus-terminal-example-") as directory:
        studio = StudioService(directory, provider_factory=lambda _: Provider())
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_studio_handler(studio, "example-terminal-csrf")
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            artifact = studio.catalog.save(
                "artifacts",
                {
                    "name": "Example evidence",
                    "data": {
                        "filename": "example.txt",
                        "content_base64": base64.b64encode(b"Example private evidence").decode(),
                    },
                },
            )
            command = [
                sys.executable,
                "-m",
                "nexus.cli",
                "chat",
                "--json",
                "--url",
                f"http://127.0.0.1:{server.server_port}",
                "--timeout",
                "10",
            ]
            result = subprocess.run(
                [*command, "--artifact-id", artifact["id"], "Review example evidence"],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            record = json.loads(result.stdout)
            inspected = subprocess.run(
                [*command, "--run-id", record["id"]],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert json.loads(inspected.stdout) == record
            assert len(studio.records("runs")) == 1
            print(result.stdout, end="")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            studio.close()


if __name__ == "__main__":
    exercise()
