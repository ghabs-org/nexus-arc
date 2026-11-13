"""Demonstrate the framework terminal client without model accounts or user state.

Run from the repository root: PYTHONPATH=. python examples/nexus-bot/src/studio_terminal.py
The disposable server and deterministic provider are example wiring only.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_http_client import StudioHTTPClient
from nexus.core.studio_terminal_sessions import TerminalSessions
from nexus.core.studio_terminal_ui import completion_candidates, render_markdown
from nexus.studio import make_studio_handler


def exercise():
    agents = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            agents.append(context.agent_name)
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
            exported = Path(directory) / "export.json"
            interactive = subprocess.run(
                [argument for argument in command if argument != "--json"],
                input=(
                    f"/switch {record['session_id']}\n/rename Example review\n"
                    f'/history 1\n/export json "{exported}"\n/quit\n'
                ),
                env={
                    **os.environ,
                    "NEXUS_TERMINAL_STATE_DIR": str(Path(directory) / "terminal-preferences"),
                },
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert "assistant: Reviewed example evidence" in interactive.stdout
            assert json.loads(exported.read_text())["conversation"]["title"] == "Example review"
            assert len(studio.records("runs")) == 1
            local = Path(directory) / "local evidence.txt"
            local.write_text("Example private evidence")
            transferred = subprocess.run(
                [*[argument for argument in command if argument != "--json"], "--new-session"],
                input=(f'/upload "{local}" Review local file\n/artifacts\n/download\n1\n/quit\n'),
                cwd=directory,
                env={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
                    "NEXUS_TERMINAL_STATE_DIR": str(Path(directory) / "terminal-preferences"),
                },
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            new_run = next(run for run in studio.records("runs") if run["id"] != record["id"])
            generated = studio.catalog.get("artifacts", new_run["artifact_id"])
            downloaded = Path(directory) / generated["data"]["filename"]
            assert downloaded.read_text() == "Reviewed example evidence"
            assert "1 generated artifacts" in transferred.stdout
            assert len(studio.records("runs")) == 2
            studio.save_definition(
                "agents",
                {"name": "Example reviewer", "instructions": "Review the selected evidence"},
            )
            client = StudioHTTPClient(f"http://127.0.0.1:{server.server_port}")
            try:
                sessions = TerminalSessions(client, record["session_id"])
                candidates = completion_candidates("/switch Example", sessions)
                assert ("'Example review'", len("Example")) in candidates
                assert ("'Example reviewer'", len("Example")) in completion_candidates(
                    "/agent Example", sessions
                )
                assert len(studio.records("runs")) == 2
            finally:
                client.close()
            directed = subprocess.run(
                [argument for argument in command if argument != "--json"],
                input='/agents\n/entrypoints\n/alias review /agent "Example reviewer" Review {"source": "local"}\n/review\n/retry\n/alias review\n/c\n/q\n',
                env={
                    **os.environ,
                    "NEXUS_TERMINAL_STATE_DIR": str(Path(directory) / "terminal-preferences"),
                },
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert "example-reviewer · Example reviewer" in directed.stdout
            assert any(
                run["task"] == 'Review {"source": "local"}' for run in studio.records("runs")
            )
            assert len(studio.records("runs")) == 4
            assert agents[-2:] == ["example-reviewer", "example-reviewer"]
            preferences = {
                **os.environ,
                "NEXUS_TERMINAL_STATE_DIR": str(Path(directory) / "terminal-preferences"),
            }
            previous_count = len(studio.records("sessions"))
            pending = subprocess.run(
                [argument for argument in command if argument != "--json"],
                input="/new Named example\n/q\n",
                env=preferences,
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert len(studio.records("sessions")) == previous_count
            assert "Pending title · Named example" in pending.stdout
            named = subprocess.run(
                [argument for argument in command if argument != "--json"],
                input="Create named evidence\n/delete 'Named example'\n/q\n",
                env=preferences,
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            saved = next(
                item for item in studio.records("sessions") if item["title"] == "Named example"
            )
            assert "Server history and artifacts remain" in named.stdout
            assert len(studio.session(saved["id"])["messages"]) == 2
            resumed = subprocess.run(
                [argument for argument in command if argument != "--json"],
                input=f"/switch {saved['id']}\n/history 1\n/q\n",
                env=preferences,
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert "Reviewed example evidence" in resumed.stdout
            assert len(studio.records("runs")) == 5
            if sys.stdout.isatty():
                render_markdown(
                    "# Terminal chat\n\n- **Tab** completes commands, sessions and artifacts.\n"
                    "- `/multiline` enables editing; **Alt+Enter** sends.\n\n"
                    "Install `nexus-arc[studio-terminal]` to enable the enhanced terminal."
                )
            print(result.stdout, end="")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            studio.close()


if __name__ == "__main__":
    exercise()
