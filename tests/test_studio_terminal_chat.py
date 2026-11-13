"""Terminal consumers preserve existing file, result and no-replay contracts."""

import base64
import json
import os
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
            env={**os.environ, "NEXUS_TERMINAL_STATE_DIR": str(tmp_path / "terminal-preferences")},
        )

    command.origin = f"http://127.0.0.1:{server.server_port}"
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
        {"new_session": True, "session_id": "existing"},
        {"new_session": True, "run_id": "existing"},
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


def test_interactive_session_controls_restore_history_without_extra_runs(terminal, tmp_path):
    studio, contexts, _, command = terminal
    exported = tmp_path / "conversation.json"
    markdown = tmp_path / "conversation.md"
    plain = tmp_path / "conversation.txt"
    result = command(
        input=(
            "First evidence\n/rename Investigation\n/new\nSecond evidence\n"
            "/sessions\n/switch Investigation\n/history 1\n"
            f'/export json "{exported}"\n/export md "{markdown}"\n'
            f'/export txt "{plain}"\nContinue first\n/quit\n'
        )
    )
    assert result.returncode == 0, result.stderr
    assert "2 visible conversations" in result.stdout
    assert "assistant: Read selected evidence" in result.stdout
    assert len(contexts) == len(studio.records("runs")) == 3
    sessions = studio.records("sessions")
    assert len(sessions) == 2
    first = next(item for item in sessions if item["title"] == "Investigation")
    assert len(studio.session(first["id"])["messages"]) == 4
    saved = json.loads(exported.read_text())
    assert saved["format"] == "nexus-studio-memory/v1"
    assert saved["conversation"]["id"] == first["id"]
    assert len(saved["conversation"]["messages"]) == 2
    assert exported.stat().st_mode & 0o777 == 0o600
    assert markdown.read_text().startswith("## user\n\nFirst evidence")
    assert plain.read_text().startswith("user\n\nFirst evidence")
    # The durable server title survives a fresh terminal process.
    resumed = command(input="/switch Investigation\n/history 1\n/quit\n")
    assert resumed.returncode == 0, resumed.stderr
    assert "assistant: Read selected evidence" in resumed.stdout
    assert len(studio.records("runs")) == 3


def test_session_command_errors_preserve_selection_and_never_submit(terminal, tmp_path):
    studio, contexts, _, command = terminal
    existing = tmp_path / "existing.txt"
    existing.write_text("Keep this file")
    link = tmp_path / "link.txt"
    link.symlink_to(existing)
    result = command(
        input=(
            "/history\n/help\n/new " + "x" * 201 + "\nHello\n/rename 'Broken\n"
            "/rename Stable\n/history 0\n/history nonsense\n/export xml\n"
            f'/export txt "{existing}"\n/export md "{link}"\n'
            "/switch missing\n/history 1\n/quit\n"
        )
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("Command failed:") == 9
    assert "assistant: Read selected evidence" in result.stdout
    assert existing.read_text() == "Keep this file"
    assert len(contexts) == len(studio.records("runs")) == 1
    assert studio.records("sessions")[0]["title"] == "Stable"


def test_duplicate_session_titles_require_id_and_preserve_current(terminal):
    studio, contexts, _, command = terminal
    result = command(
        input="First\n/rename Same\n/new\nSecond\n/rename Same\n/switch Same\nContinue\n/quit\n"
    )
    assert result.returncode == 0, result.stderr
    assert "Several conversations share that title" in result.stdout
    records = studio.records("sessions")
    lengths = sorted(len(studio.session(item["id"])["messages"]) for item in records)
    assert lengths == [2, 4] and len(contexts) == 3


def test_session_selection_authorization_failure_does_not_replace_current():
    from nexus.core.studio_terminal_sessions import TerminalSessions

    class Client:
        def request(self, path, data=None):
            if path == "/api/sessions":
                return {"items": []}
            raise ValueError("Studio rejected the request")

    sessions = TerminalSessions(Client(), "owned")
    assert sessions.dispatch("/switch another-users-session")
    assert sessions.session_id == "owned"
    assert not sessions.dispatch("/existing-prompt some variables")


def test_explicit_session_id_works_outside_listing_and_wins_over_title():
    from nexus.core.studio_terminal_sessions import TerminalSessions

    paths = []

    class Client:
        def request(self, path, data=None):
            paths.append(path)
            if path == "/api/sessions":
                return {
                    "items": [
                        {"id": "other", "title": "visible"},
                        {"id": "visible", "title": "Selected"},
                    ]
                }
            return {"id": path.rsplit("/", 1)[1], "title": "Selected"}

    sessions = TerminalSessions(Client())
    sessions.dispatch("/switch not-listed")
    assert sessions.session_id == "not-listed"
    sessions.dispatch("/switch visible")
    assert sessions.session_id == "visible"
    assert paths[-1] == "/api/sessions/visible"


def test_local_upload_submits_original_once_and_does_not_overwrite_saved_names(terminal, tmp_path):
    studio, contexts, paths, command = terminal
    source = tmp_path / "private evidence.txt"
    source.write_bytes(b"Original private evidence")
    result = command(input=f'/upload "{source}" Review file\nContinue\n/upload "{source}"\n/quit\n')
    assert result.returncode == 0, result.stderr
    assert len(contexts) == len(studio.records("runs")) == 3
    assert len(paths) == 2 and all(not path.exists() for path in paths)
    assert not contexts[1].metadata.get("attachment_files")
    uploads = [item for item in studio.catalog.list("artifacts") if item["name"] == source.name]
    assert len(uploads) == 2 and len({item["id"] for item in uploads}) == 2
    assert all(item["revision"] == 1 for item in uploads)


def test_generated_download_keeps_recorded_revision_and_large_original_bytes(terminal, tmp_path):
    studio, contexts, _, command = terminal
    assert command("Prepare data").returncode == 0
    run = studio.records("runs")[0]
    original = b"\x00\xff" * (1536 * 1024)
    published = studio.data_tools.publish(
        {
            "stdout": "",
            "files": [
                {"filename": "result.bin", "content_base64": base64.b64encode(original).decode()}
            ],
        },
        None,
        run["id"],
    )["artifacts"][0]
    previous = studio.catalog.get("artifacts", published["id"])
    studio.catalog.save(
        "artifacts",
        {
            **previous,
            "data": {
                "filename": "changed.bin",
                "content_base64": base64.b64encode(b"Replacement").decode(),
            },
        },
    )
    target = tmp_path / "download.bin"
    result = command(
        "--session-id",
        run["session_id"],
        input=f'/artifacts\n/download result.bin "{target}"\n/quit\n',
    )
    assert result.returncode == 0, result.stderr
    assert "2 generated artifacts" in result.stdout
    assert "result.bin" in result.stdout and "changed.bin" not in result.stdout
    assert target.read_bytes() == original and target.stat().st_mode & 0o777 == 0o600
    assert len(contexts) == len(studio.records("runs")) == 1
    # All destinations are checked before any transfer; never overwrite or follow symlinks.
    link = tmp_path / "link.bin"
    link.symlink_to(target)
    rejected = command(
        "--session-id",
        run["session_id"],
        input=f'/download result.bin "{target}"\n/download result.bin "{link}"\n/quit\n',
    )
    assert rejected.returncode == 0 and rejected.stdout.count("Command failed:") == 2
    assert target.read_bytes() == original


def test_artifact_download_rejects_other_conversations_and_no_selection_cancels(terminal, tmp_path):
    studio, contexts, _, command = terminal
    command("First")
    first = studio.records("runs")[0]
    command("Second")
    second = next(item for item in studio.records("runs") if item["id"] != first["id"])
    target = tmp_path / "not-created.txt"
    result = command(
        "--session-id",
        first["session_id"],
        input=f'/download {second["artifact_id"]} "{target}"\n/download\n\n/quit\n',
    )
    assert result.returncode == 0, result.stderr
    assert "Choose one artifact from this conversation" in result.stdout
    assert not target.exists() and len(contexts) == 2


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "oversized"])
def test_invalid_local_upload_never_contacts_studio(kind, tmp_path):
    import os

    from nexus.core.studio_terminal_files import TerminalFiles

    class Client:
        def request(self, *args):
            raise AssertionError("Invalid files must fail before upload")

    path = tmp_path / kind
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "oversized":
        with path.open("wb") as output:
            output.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises((OSError, ValueError)):
        TerminalFiles(Client()).upload(path)


def test_binary_download_rejects_bad_hash_before_creating_file(terminal, tmp_path):
    from nexus.core.studio_http_client import StudioHTTPClient

    studio, _, _, command = terminal
    file = save_file(studio)
    client = StudioHTTPClient(command.origin)
    target = tmp_path / "corrupt.txt"
    try:
        with pytest.raises(ValueError, match="integrity"):
            client.download(
                f"/api/catalog/artifacts/{file['id']}/versions/1", target, sha256="0" * 64
            )
        assert not target.exists()
    finally:
        client.close()


def test_multi_download_selection_validates_all_destinations_first(tmp_path, monkeypatch):
    from nexus.core.studio_terminal_files import TerminalFiles

    calls = []

    class Client:
        def download(self, path, destination, **kwargs):
            calls.append(path)
            Path(destination).write_bytes(b"Selected")

    files = TerminalFiles(Client())
    items = [
        {"id": "first", "revision": 1, "data": {"filename": "first.txt"}},
        {"id": "second", "revision": 2, "data": {"filename": "second.txt"}},
    ]
    monkeypatch.setattr(files, "artifacts", lambda session: items)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "1,2,1")
    files.download([], {"id": "session"})
    assert len(calls) == 2
    assert all((tmp_path / item["data"]["filename"]).exists() for item in items)
    calls.clear()
    with pytest.raises(ValueError, match="distinct new files"):
        files.download([], {"id": "session"})
    assert not calls
    monkeypatch.setattr("builtins.input", lambda _: "0")
    with pytest.raises(ValueError, match="artifact numbers"):
        files.download([], {"id": "session"})

    def ended(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", ended)
    files.download([], {"id": "session"})
    assert not calls


def test_upload_with_full_pending_attachment_selection_never_reads_or_sends_file():
    from nexus.core.studio_terminal_sessions import TerminalSessions

    sessions = TerminalSessions(None, artifact_ids=[str(index) for index in range(5)])
    assert sessions.dispatch("/upload /missing/path")
    assert not sessions.pending_task and len(sessions.artifact_ids) == 5


def test_directed_agent_message_preserves_body_files_and_one_turn_selection(terminal):
    studio, contexts, paths, command = terminal
    studio.save_definition(
        "agents", {"id": "reviewer", "name": "Review Agent", "instructions": "Review carefully"}
    )
    studio.save_definition(
        "agents", {"id": "default", "name": "Default Agent", "instructions": "Default instructions"}
    )
    file = save_file(studio)
    body = 'Inspect {"key": "value"} and preserve "quoted text"'
    result = command(
        "--agent",
        "default",
        "--artifact-id",
        file["id"],
        input=f'/agents\n/agent "Review Agent" {body}\nContinue normally\n/quit\n',
    )
    assert result.returncode == 0, result.stderr
    assert "reviewer · Review Agent · codex · definition available" in result.stdout
    runs = studio.records("runs")
    assert len(runs) == len(contexts) == 2 and len(paths) == 1
    directed = next(run for run in runs if run["task"] == body)
    assert directed["task"] == body and directed["artifact_refs"] == [
        {"id": file["id"], "revision": 1}
    ]
    assert [context.agent_name for context in contexts] == ["reviewer", "default"]
    assert any(run["task"] == "Continue normally" for run in runs)
    assert not contexts[-1].metadata.get("attachment_files") and not paths[0].exists()
    assert len(studio.records("sessions")) == 1


def test_agent_and_entrypoint_discovery_never_runs_targets_or_discloses_configuration(terminal):
    studio, contexts, _, command = terminal
    studio.save_definition(
        "agents", {"name": "Private reviewer", "instructions": "DO_NOT_PRINT_INSTRUCTIONS"}
    )
    entrypoint = studio.catalog.save(
        "entrypoints",
        {
            "name": "Incoming evidence",
            "data": {
                "target_kind": "agent",
                "target_id": "private-reviewer",
                "token_env": "DO_NOT_PRINT_TOKEN_ENV",
                "enabled": False,
            },
        },
    )
    result = command(input="/agents\n/entrypoints\n/quit\n")
    assert result.returncode == 0, result.stderr
    assert "Incoming evidence · HTTP · disabled" in result.stdout
    assert "Connection and last-seen status are not tracked" in result.stdout
    assert "DO_NOT_PRINT" not in result.stdout
    assert not contexts and not studio.records("runs")
    assert not studio.catalog.get("entrypoints", entrypoint["id"])["data"]["enabled"]


def test_invalid_agent_commands_never_submit_or_replace_default(terminal):
    studio, contexts, _, command = terminal
    studio.save_definition("agents", {"id": "one", "name": "Same name"})
    studio.save_definition("agents", {"id": "two", "name": "Same name"})
    (studio.root / "agents" / "broken.yaml").write_text("spec: [unclosed")
    result = command(
        "--agent",
        "one",
        input='/agent\n/agent missing Hello\n/agent "Same name" Hello\n/agent broken Hello\n/agents extra\n/entrypoints extra\nNormal turn\n/quit\n',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("Command failed:") == 6
    assert len(contexts) == len(studio.records("runs")) == 1
    assert contexts[0].agent_name == "one"


def test_aliases_persist_route_once_and_preserve_literal_arguments(terminal):
    studio, contexts, _, command = terminal
    studio.save_definition("agents", {"name": "Reviewer"})
    result = command(
        input=(
            '/alias review /agent reviewer Inspect {"source": "local"}\n'
            '/review and "extra quotes"\n/alias brief "Summarize evidence"\n'
            '/brief Keep {"value": 0}\n/alias\n/alias brief\n/c\n/q\n'
        )
    )
    assert result.returncode == 0, result.stderr
    assert "2 terminal aliases" in result.stdout and "Removed alias /brief" in result.stdout
    assert "Clear screen requires a terminal" in result.stdout
    assert len(contexts) == len(studio.records("runs")) == 2
    assert [context.agent_name for context in contexts] == ["reviewer", "studio"]
    assert {run["task"] for run in studio.records("runs")} == {
        'Inspect {"source": "local"} and "extra quotes"',
        'Summarize evidence Keep {"value": 0}',
    }
    resumed = command(input="/review Again\n/q\n")
    assert resumed.returncode == 0, resumed.stderr
    assert contexts[-1].agent_name == "reviewer" and len(contexts) == 3
    aliases = [
        path
        for path in (studio.root / "terminal-preferences").rglob("*.json")
        if path.parent.name == "aliases"
    ]
    assert len(aliases) == 1 and aliases[0].stat().st_mode & 0o777 == 0o600


def test_saved_prompt_tokens_win_over_prefixes_and_newly_conflicting_aliases(terminal):
    studio, contexts, _, command = terminal
    initial = command(input="/alias review Ordinary alias text\n/q\n")
    assert initial.returncode == 0, initial.stderr
    studio.catalog.save(
        "prompts",
        {
            "name": "Stored review",
            "data": {"content": "Grounded saved instruction", "shortcut": "review"},
        },
    )
    studio.catalog.save(
        "prompts",
        {
            "name": "Saved helper",
            "data": {"content": "Stored helper instruction", "shortcut": "he"},
        },
    )
    result = command(
        input="/review original\n/he next\n/alias he blocked\n/alias help blocked\n/h\n/ag\n/q\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("Command failed:") == 4
    assert "Ambiguous command:" in result.stdout
    assert len(contexts) == len(studio.records("runs")) == 2
    assert "Grounded saved instruction" in contexts[0].prompt
    assert "Stored helper instruction" in contexts[1].prompt
    assert {run["task"] for run in studio.records("runs")} == {"/review original", "/he next"}


def test_explicit_retry_resends_original_files_and_agent_once_then_restores_default(terminal):
    studio, contexts, paths, command = terminal
    studio.save_definition("agents", {"name": "Reviewer"})
    studio.save_definition("agents", {"name": "Default"})
    file = save_file(studio)
    body = 'Review {"value": 0}'
    result = command(
        "--agent",
        "default",
        "--artifact-id",
        file["id"],
        input=f"/retry\n/agent reviewer {body}\n/retry\nContinue\n/q\n",
    )
    assert result.returncode == 0, result.stderr
    assert "No completed message from this terminal" in result.stdout
    assert len(contexts) == len(studio.records("runs")) == 3
    assert [context.agent_name for context in contexts] == ["reviewer", "reviewer", "default"]
    assert len(paths) == 2 and all(not path.exists() for path in paths)
    assert len([run for run in studio.records("runs") if run["task"] == body]) == 2
    assert len(studio.records("sessions")) == 1


def test_expected_attachment_revision_is_validated_before_queueing(terminal):
    studio, contexts, _, command = terminal
    file = save_file(studio)
    expected = [{"id": file["id"], "revision": 1}]
    run = studio.start_run(
        {
            "kind": "chat",
            "task": "Original",
            "artifact_ids": [file["id"]],
            "expected_artifact_refs": expected,
        }
    )
    assert command("--run-id", run["id"]).returncode == 0
    studio.catalog.save(
        "artifacts",
        {
            **file,
            "data": {
                "filename": "changed.txt",
                "content_base64": base64.b64encode(b"Changed").decode(),
            },
        },
    )
    with pytest.raises(ValueError, match="revision changed"):
        studio.start_run(
            {
                "kind": "chat",
                "task": "Repeat",
                "artifact_ids": [file["id"]],
                "expected_artifact_refs": expected,
            }
        )
    assert len(contexts) == len(studio.records("runs")) == 1


@pytest.mark.parametrize(
    "expected",
    [
        None,
        {},
        [{"id": "file", "revision": True}],
        [{"id": "file", "revision": 0}],
        [{"id": "file", "revision": 1, "extra": True}],
    ],
)
def test_malformed_expected_attachment_revisions_cannot_queue(expected, terminal):
    studio, contexts, _, _ = terminal
    with pytest.raises(ValueError, match="Expected attachments"):
        studio.start_run({"kind": "chat", "task": "Invalid", "expected_artifact_refs": expected})
    assert not contexts and not studio.records("runs")


def test_named_pending_session_survives_restart_and_restores_without_resubmitting(terminal):
    studio, contexts, _, command = terminal
    pending = command(input="/new Investigation\n/q\n")
    assert pending.returncode == 0, pending.stderr
    assert not studio.records("runs") and not studio.records("sessions")
    created = command(input="First evidence\n/q\n")
    assert created.returncode == 0, created.stderr
    original = studio.records("sessions")[0]
    assert original["title"] == "Investigation" and original["message_count"] == 2
    assert "message_count" not in studio.session(original["id"])
    restored = command(input="/history 1\nContinue\n/q\n")
    assert restored.returncode == 0, restored.stderr
    assert f"Restored {original['id']}" in restored.stdout
    assert len(contexts) == 2 and len(studio.records("sessions")) == 1
    assert studio.records("sessions")[0]["message_count"] == 4
    assert command("--json", "One-shot remains new").returncode == 0
    inspected = command(input="/sessions\n/q\n")
    assert inspected.returncode == 0, inspected.stderr
    assert (
        f"Restored {original['id']}" in inspected.stdout and "4 saved messages" in inspected.stdout
    )
    assert len(contexts) == 3
    fresh = command("--new-session", input="Fresh conversation\n/q\n")
    assert fresh.returncode == 0, fresh.stderr
    assert "Restored " not in fresh.stdout and len(studio.records("sessions")) == 3


def test_local_delete_persists_without_archiving_history_or_artifacts(terminal):
    studio, contexts, _, command = terminal
    assert command(input="/new Keep history\nEvidence\n/q\n").returncode == 0
    session = studio.records("sessions")[0]
    artifact = studio.records("runs")[0]["artifact_id"]
    deleted = command(input="/delete 'Keep history'\n/sessions\n/q\n")
    assert deleted.returncode == 0, deleted.stderr
    assert "0 visible conversations" in deleted.stdout
    assert studio.session(session["id"])["messages"] and not studio.session(session["id"]).get(
        "archived"
    )
    assert studio.catalog.get("artifacts", artifact)
    absent = command(input="/sessions\n/q\n")
    assert absent.returncode == 0 and "Restored " not in absent.stdout
    assert "0 visible conversations" in absent.stdout
    restored = command(input=f"/switch {session['id']}\n/sessions\n/history 1\n/q\n")
    assert restored.returncode == 0, restored.stderr
    assert "1 visible conversations" in restored.stdout
    assert len(contexts) == len(studio.records("runs")) == 1


@pytest.mark.parametrize("title", [None, True, "", " ", "x" * 201])
def test_invalid_new_session_titles_fail_before_queueing(title, terminal):
    studio, contexts, _, _ = terminal
    with pytest.raises(ValueError):
        studio.start_run({"kind": "chat", "task": "Invalid title", "session_title": title})
    assert not contexts and not studio.records("runs")
