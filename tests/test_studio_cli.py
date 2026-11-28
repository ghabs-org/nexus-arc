import json
import subprocess
import sys
import threading
from argparse import Namespace
from http.server import ThreadingHTTPServer

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_chat_cli import chat_command
from nexus.core.studio_config_cli import configuration_command
from nexus.studio import make_studio_handler


def test_terminal_chat_continues_and_run_inspection_never_resubmits(tmp_path, capsys):
    contexts = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            contexts.append(context)
            return AgentResult(success=True, output="Terminal answer")

    studio = StudioService(tmp_path, provider_factory=lambda name: Provider())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_studio_handler(studio, "fixture-csrf-token")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    args = Namespace(
        url=f"http://127.0.0.1:{server.server_port}",
        session_env=None,
        session_id=None,
        run_id=None,
        message=["Remember", "this"],
        agent=None,
        provider="codex",
        timeout=5,
    )
    try:
        chat_command(args)
        assert "Terminal answer" in capsys.readouterr().out
        assert len(studio.records("runs")) == 1
        run = studio.records("runs")[0]
        args.run_id = run["id"]
        chat_command(args)
        assert len(studio.records("runs")) == 1 and len(contexts) == 1
        args.run_id, args.session_id, args.message = None, run["session_id"], ["Continue"]
        chat_command(args)
        assert len(contexts) == 2 and "Remember this" in contexts[-1].prompt
        assert len(studio.session(run["session_id"])["messages"]) == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.close()


def test_config_validate_cli_uses_nonmutating_endpoint(tmp_path, capsys):
    studio = StudioService(tmp_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_studio_handler(studio, "fixture-csrf-token")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = tmp_path / "config.json"
    source.write_text('{"format":"nexus-studio-config/v1","definitions":[],"resources":[]}')
    try:
        configuration_command(
            Namespace(
                operation="validate",
                file=str(source),
                url=f"http://127.0.0.1:{server.server_port}",
                session_env=None,
                output=None,
            )
        )
        assert '"valid": true' in capsys.readouterr().out
        assert not studio.records("configuration-plans")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.close()


def test_actual_configuration_cli_round_trip_and_complete_preflight(tmp_path):
    studio = StudioService(tmp_path / "workspace")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_studio_handler(studio, "fixture-csrf-token")
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = tmp_path / "configuration.json"
    document = {
        "format": "nexus-studio-config/v1",
        "definitions": [],
        "resources": [
            {
                "kind": "prompts",
                "id": "cli-evidence",
                "name": "CLI evidence",
                "data": {"content": "Use local evidence"},
            }
        ],
    }
    source.write_text(json.dumps(document))

    def command(operation, file=None):
        args = [sys.executable, "-m", "nexus.cli", "config", operation]
        if file:
            args.append(str(file))
        args.extend(["--url", f"http://127.0.0.1:{server.server_port}"])
        return subprocess.run(args, capture_output=True, text=True, timeout=30)

    try:
        validated = command("validate", source)
        assert validated.returncode == 0, validated.stderr
        assert json.loads(validated.stdout)["valid"]
        assert not studio.catalog.list("prompts")
        planned = command("plan", source)
        assert planned.returncode == 0, planned.stderr
        reviewed = tmp_path / "reviewed.json"
        reviewed.write_text(planned.stdout)
        assert not studio.catalog.list("prompts")
        applied = command("apply", reviewed)
        assert applied.returncode == 0, applied.stderr
        exported = command("export")
        assert exported.returncode == 0, exported.stderr
        result = json.loads(exported.stdout)
        assert result["format"] == document["format"] and not result["definitions"]
        assert len(result["resources"]) == 1
        assert result["resources"][0]["id"] == "cli-evidence"
        assert result["resources"][0]["data"]["content"] == "Use local evidence"
        # A valid first resource must never be saved if a later item fails validation.
        invalid = json.loads(source.read_text())
        invalid["resources"][0]["id"] = "must-not-exist"
        invalid["resources"].append({"kind": "prompts", "id": "bad", "data": {}})
        source.write_text(json.dumps(invalid))
        rejected = command("plan", source)
        assert rejected.returncode != 0
        assert [item["id"] for item in studio.catalog.list("prompts")] == ["cli-evidence"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.close()
