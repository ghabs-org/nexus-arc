"""Alias/prefix precedence, local privacy and display-only clear contracts."""

import io
import json

import pytest

from nexus.core.studio_terminal_commands import TerminalCommands, clear_terminal
from nexus.core.studio_terminal_sessions import TerminalSessions
from nexus.core.studio_terminal_ui import completion_candidates


class Client:
    terminal_scope = {
        "origin": "https://studio.example",
        "workspace": "/project",
        "user": "alice",
        "mode": "team",
    }

    def request(self, path, data=None):
        assert path == "/api/catalog/prompts" and data is None
        return {"items": []}


def test_alias_records_are_private_account_scoped_and_atomic_per_name(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    first = TerminalCommands(Client(), {"/help", "/alias"})
    first.edit('/alias task "Review evidence"')
    second = TerminalCommands(Client(), {"/help", "/alias"})
    second.edit("/alias other Other evidence")
    assert (
        first.aliases()
        == second.aliases()
        == {"task": "Review evidence", "other": "Other evidence"}
    )
    assert first.resolve('/task {"n": 0}') == 'Review evidence {"n": 0}'
    for field, value in (
        ("user", "bob"),
        ("workspace", "/other"),
        ("origin", "https://other.example"),
    ):
        client = Client()
        client.terminal_scope = {**Client.terminal_scope, field: value}
        assert not TerminalCommands(client, {"/help"}).aliases()
    paths = list(tmp_path.rglob("*.json"))
    assert len(paths) == 2 and all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
    assert not list(tmp_path.rglob("tmp*"))
    second.edit("/alias task")
    assert first.aliases() == {"other": "Other evidence"}


def test_prefix_errors_never_become_tasks_and_alias_cycles_never_expand(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    sessions = TerminalSessions(Client())
    assert sessions.dispatch("/he") and not sessions.pending_task
    assert sessions.dispatch("/h") and not sessions.pending_task
    assert sessions.dispatch("/ag") and not sessions.pending_task
    assert sessions.dispatch("/alias loop /loop")
    assert sessions.dispatch("/loop") and not sessions.pending_task
    assert sessions.dispatch("/q") and sessions.exit_requested
    assert not sessions.dispatch("/some-saved-prompt")


def test_alias_completion_is_local_and_cannot_shadow_builtins(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    sessions = TerminalSessions(Client())
    for name in ("help", "quit", "clear", "alias"):
        assert sessions.dispatch(f"/alias {name} Rejected")
    assert not sessions.shortcuts.aliases()
    sessions.dispatch("/alias brief Summarize")
    assert completion_candidates("/br", sessions) == [("/brief", 3)]
    sessions.dispatch("/alias brief")
    assert not completion_candidates("/br", sessions)


def test_prompt_access_failure_blocks_prefix_execution_without_changing_selection():
    class Denied:
        def request(self, path, data=None):
            raise ValueError("Access denied")

    sessions = TerminalSessions(Denied(), "existing")
    assert sessions.dispatch("/n")
    assert sessions.session_id == "existing" and not sessions.pending_task


@pytest.mark.parametrize(
    "record",
    [
        [],
        {"format": "nexus-studio-terminal-alias/v1", "name": "help", "text": "forbidden"},
        {"format": "nexus-studio-terminal-alias/v1", "name": "wrong", "text": "mismatch"},
    ],
)
def test_malformed_local_alias_records_fail_closed(record, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    commands = TerminalCommands(Client(), {"/help"})
    commands.directory.mkdir(parents=True)
    (commands.directory / "bad.json").write_text(json.dumps(record))
    with pytest.raises(ValueError):
        commands.resolve("/b")


def test_clear_emits_only_display_control_on_a_terminal(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    output = TTY()
    monkeypatch.setattr("sys.stdout", output)
    clear_terminal()
    assert output.getvalue() == "\033[2J\033[H"


@pytest.mark.parametrize("text", ["é" * 8192, "\\" * 16384])
def test_maximum_alias_text_round_trips_json_escaping(text, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    commands = TerminalCommands(Client(), {"/alias"})
    commands.edit("/alias text " + text)
    assert TerminalCommands(Client(), {"/alias"}).aliases() == {"text": text}


def test_new_builtin_keeps_old_conflicting_alias_listable_and_removable(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    old = TerminalCommands(Client(), {"/alias"})
    old.edit("/alias retry Old expansion")
    old.edit("/alias brief Summarize")
    upgraded = TerminalCommands(Client(), {"/alias", "/retry"})
    assert upgraded.resolve("/retry") == "/retry"
    assert upgraded.resolve("/brief") == "Summarize"
    assert upgraded.aliases()["retry"] == "Old expansion"
    upgraded.edit("/alias retry")
    assert upgraded.aliases() == {"brief": "Summarize"}
