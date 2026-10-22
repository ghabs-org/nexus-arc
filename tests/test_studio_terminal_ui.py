"""Base-install terminal presentation and read-only completion contracts."""

import builtins
import io
import shlex

import pytest

from nexus.core.studio_chat_cli import print_result
from nexus.core.studio_terminal_sessions import TerminalSessions
from nexus.core.studio_terminal_ui import TerminalInput, completion_candidates, render_markdown


class TTYBuffer(io.StringIO):
    def isatty(self):
        return True


def test_command_completion_never_connects_or_submits():
    sessions = TerminalSessions(None)
    assert completion_candidates("/he", sessions) == [("/help", 3)]
    assert ("/quit", 2) in completion_candidates("/q", sessions)
    assert not completion_candidates("ordinary question", sessions)
    assert not completion_candidates("", sessions)
    assert completion_candidates("/export j", sessions) == [("json", 1)]
    assert not completion_candidates("/export json output.json", sessions)


def test_session_completion_quotes_titles_and_never_posts():
    calls = []

    class Client:
        def request(self, path, data=None):
            assert data is None
            calls.append(path)
            return {"items": [{"id": "session-1", "title": "Review O'Brien"}]}

    sessions = TerminalSessions(Client())
    candidates = completion_candidates('/switch "Rev', sessions)
    assert len(candidates) == 1
    value, length = candidates[0]
    assert shlex.split(value) == ["Review O'Brien"] and length == 4
    assert calls == ["/api/sessions"]
    assert sessions.session_id is None and not sessions.pending_task
    assert completion_candidates("/switch Review O", sessions)[0][0] == value


def test_download_completion_uses_selected_conversation_and_pinned_revision():
    calls = []
    sessions = TerminalSessions(None)
    assert completion_candidates("/download ", sessions) == []
    sessions.session_id = "owned"
    sessions.current = lambda: {"id": "owned"}
    sessions.files.artifacts = lambda session: (
        calls.append(session["id"])
        or [{"id": "file", "revision": 2, "data": {"filename": "result data.csv"}}]
    )
    assert completion_candidates("/download f", sessions) == [("file:2", 1)]
    assert completion_candidates("/download res", sessions) == [("'result data.csv'", 3)]
    assert calls == ["owned", "owned"]
    assert not sessions.pending_task and not sessions.artifact_ids


def test_denied_completion_never_falls_back_to_global_catalog():
    class Client:
        def request(self, path, data=None):
            assert path == "/api/sessions" and data is None
            raise ValueError("Access revoked")

    assert completion_candidates("/switch ", TerminalSessions(Client())) == []


@pytest.mark.parametrize("plain,terminal", [(True, True), (False, False)])
def test_plain_and_redirected_modes_never_import_terminal_libraries(monkeypatch, plain, terminal):
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("rich", "prompt_toolkit"))
        return original_import(name, *args, **kwargs)

    output = TTYBuffer() if terminal else io.StringIO()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("sys.stdin", TTYBuffer())
    monkeypatch.setattr("builtins.__import__", guarded)
    render_markdown("# Exact **Markdown**", plain=plain)
    render_markdown("# Heading", plain=plain, label="assistant:")
    reader = TerminalInput(TerminalSessions(None), plain=plain)
    assert output.getvalue() == "# Exact **Markdown**\nassistant: # Heading\n"
    assert reader.prompt_session is None


def test_missing_optional_libraries_fall_back_without_changing_payload(monkeypatch):
    original_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name.startswith(("rich", "prompt_toolkit")):
            raise ImportError("Optional capability not installed")
        return original_import(name, *args, **kwargs)

    output = TTYBuffer()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("sys.stdin", TTYBuffer())
    monkeypatch.setattr("builtins.__import__", missing)
    monkeypatch.setattr("builtins.input", lambda prompt: "Unchanged message")
    render_markdown("## Evidence")
    reader = TerminalInput(TerminalSessions(None))
    assert reader.read() == "Unchanged message"
    assert output.getvalue() == "## Evidence\n"


def test_json_result_bypasses_markdown_even_on_terminal(monkeypatch):
    output = TTYBuffer()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(
        "nexus.core.studio_chat_cli.render_markdown",
        lambda *args, **kwargs: pytest.fail("JSON must never use rich rendering"),
    )
    print_result({"id": "run", "status": "completed", "output": "# Evidence"}, structured=True)
    assert '"output": "# Evidence"' in output.getvalue() and "\x1b" not in output.getvalue()


def test_multiline_requires_enhanced_input_and_toggles_without_submission():
    sessions = TerminalSessions(None)
    assert sessions.dispatch("/multiline") and not sessions.multiline
    sessions.enhanced_input = True
    assert sessions.dispatch("/multiline") and sessions.multiline
    assert sessions.dispatch("/multiline argument") and sessions.multiline
    assert sessions.dispatch("/multiline") and not sessions.multiline
    assert not sessions.pending_task


def test_agent_completion_offers_names_and_ids_without_instructions_or_writes():
    class Client:
        def request(self, path, data=None):
            assert path == "/api/agents" and data is None
            return {
                "items": [
                    {
                        "id": "reviewer",
                        "document": {
                            "metadata": {"name": "Review Agent"},
                            "spec": {"purpose": "PRIVATE"},
                        },
                    },
                    {"id": "broken", "error": "PRIVATE_INVALID_DEFINITION"},
                ]
            }

    sessions = TerminalSessions(Client())
    assert completion_candidates("/agent Rev", sessions) == [("'Review Agent'", 3), ("reviewer", 3)]
    assert not completion_candidates("/agent broken", sessions)
    assert not completion_candidates("/agent reviewer message", sessions)
    assert not sessions.pending_task and sessions.pending_agent_id is None
