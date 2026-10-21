"""Terminal consumers preserve existing file, result and no-replay contracts."""

import base64
import json
import subprocess
import sys
import threading
from argparse import Namespace
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_chat_cli import chat_command, print_result
from nexus.studio import make_studio_handler


@pytest.fixture
def terminal(tmp_path):
    contexts, paths = [], []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            contexts.append(context)
            for item in context.metadata.get("attachment_files", []):
                path = Path(item["path"])
                assert path.read_bytes() == b"Original private evidence"
                assert path.as_uri() in context.prompt
                paths.append(path)
            return AgentResult(success=True, output="Read selected evidence")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "terminal-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def command(*arguments, input=None):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "nexus.cli",
                "chat",
                "--url",
                f"http://127.0.0.1:{server.server_port}",
                "--timeout",
                "5",
                *arguments,
            ],
            input=input,
            capture_output=True,
            text=True,
            timeout=20,
        )

    try:
        yield studio, contexts, paths, command
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.close()


def save_file(studio):
    return studio.catalog.save(
        "artifacts",
        {
            "name": "Private evidence",
            "data": {
                "filename": "private evidence.txt",
                "content_base64": base64.b64encode(b"Original private evidence").decode(),
            },
        },
    )


def test_actual_cli_attaches_saved_original_and_returns_recoverable_json(terminal):
    studio, contexts, paths, command = terminal
    file = save_file(studio)
    result = command(
        "--json", "--artifact-id", file["id"], "--artifact-id", file["id"], "Read this file"
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["format"] == "nexus-studio-chat-result/v1"
    assert record["status"] == "completed" and record["output"] == "Read selected evidence"
    assert record["artifact_refs"] == [{"id": file["id"], "revision": 1}]
    assert len(contexts) == len(paths) == 1 and not paths[0].exists()
    # Updating the current file cannot change inspection of the earlier result.
    studio.catalog.save(
        "artifacts",
        {
            **file,
            "data": {
                "filename": "private evidence.txt",
                "content_base64": base64.b64encode(b"Changed").decode(),
            },
        },
    )
    inspected = command("--json", "--run-id", record["id"])
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == record
    assert len(contexts) == len(studio.records("runs")) == 1
    continued = command("--json", "--session-id", record["session_id"], "Continue")
    assert continued.returncode == 0, continued.stderr
    assert json.loads(continued.stdout)["session_id"] == record["session_id"]
    assert len(contexts) == 2 and not contexts[-1].metadata.get("attachment_files")


def test_interactive_file_applies_only_to_next_message_and_exit_never_submits(terminal):
    studio, contexts, paths, command = terminal
    file = save_file(studio)
    result = command("--artifact-id", file["id"], input="\nRead file\nContinue\n/quit\n")
    assert result.returncode == 0, result.stderr
    assert len(contexts) == 2 and len(paths) == 1
    assert not contexts[-1].metadata.get("attachment_files")
    assert len(studio.records("sessions")) == 1
    assert not paths[0].exists()


def test_unknown_file_fails_before_any_provider_execution(terminal):
    studio, contexts, _, command = terminal
    result = command("--artifact-id", "missing", "Read file")
    assert result.returncode != 0
    assert not contexts and not studio.records("runs")


def test_inspection_keeps_citations_and_never_prints_internal_fields(capsys):
    run = {
        "id": "saved",
        "session_id": "conversation",
        "status": "completed",
        "output": "Answer",
        "environment": {"TOKEN": "PRIVATE_SECRET"},
        "citations": [
            {"url": "https://example.test/source"},
            {"filename": "evidence.pdf", "artifact_revision": 2, "page": 3},
            {"id": "source-without-page"},
        ],
    }
    print_result(run)
    rendered = capsys.readouterr().out
    assert "Source: evidence.pdf · revision 2 · page 3" in rendered
    assert "Source: source-without-page" in rendered
    print_result(run, structured=True)
    result = capsys.readouterr().out
    assert "PRIVATE_SECRET" not in result and "environment" not in result
    assert json.loads(result)["citations"] == run["citations"]


@pytest.mark.parametrize("status", ["failed", "cancelled", "awaiting_approval"])
def test_terminal_result_preserves_noncompleted_state(status, capsys):
    print_result({"id": "saved", "status": status, "error": "Needs review"}, structured=True)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status and result["error"] == "Needs review"


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_id": [str(i) for i in range(6)]},
        {"run_id": "existing", "artifact_id": ["file"], "message": []},
        {"json_output": True, "message": []},
    ],
)
def test_invalid_options_do_not_even_connect(changes, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid input must fail before network access")

    monkeypatch.setattr("nexus.core.studio_chat_cli.StudioHTTPClient", forbidden)
    args = Namespace(
        timeout=5,
        artifact_id=[],
        json_output=False,
        run_id=None,
        message=["Hello"],
        url="http://127.0.0.1:1",
        session_env=None,
        session_id=None,
        agent=None,
        provider=None,
    )
    vars(args).update(changes)
    with pytest.raises(ValueError):
        chat_command(args)


def test_polling_timeout_keeps_run_id_and_never_replays_submission(monkeypatch):
    calls = []
    closed = []

    class Client:
        def __init__(self, *args):
            pass

        def request(self, path, data=None):
            calls.append((path, data))
            return {"id": "pending-run", "session_id": "session", "status": "running"}

        def close(self):
            closed.append(True)

    ticks = iter([0, 0, 6])
    monkeypatch.setattr("nexus.core.studio_chat_cli.StudioHTTPClient", Client)
    monkeypatch.setattr("nexus.core.studio_chat_cli.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("nexus.core.studio_chat_cli.time.sleep", lambda _: None)
    args = Namespace(
        timeout=5,
        artifact_id=[],
        json_output=True,
        run_id=None,
        message=["Explicit message"],
        url="http://127.0.0.1:1",
        session_env=None,
        session_id=None,
        agent=None,
        provider=None,
    )
    with pytest.raises(ValueError, match="pending-run.*do not resubmit"):
        chat_command(args)
    assert sum(data is not None for _, data in calls) == 1
    assert closed == [True]


def test_run_references_come_only_from_validated_attachment_selection(terminal):
    studio, _, _, command = terminal
    file = save_file(studio)
    run = studio.start_run(
        {
            "kind": "chat",
            "task": "Review",
            "artifact_ids": [file["id"]],
            "artifact_refs": [{"id": "forged", "revision": 999}],
        }
    )
    inspected = command("--json", "--run-id", run["id"])
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["artifact_refs"] == [{"id": file["id"], "revision": 1}]
    no_files = studio.start_run(
        {
            "kind": "chat",
            "task": "No attachments",
            "artifact_refs": [{"id": "forged", "revision": 999}],
        }
    )
    inspected = command("--json", "--run-id", no_files["id"])
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)["artifact_refs"] == []
