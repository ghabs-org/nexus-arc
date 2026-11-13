"""Session preferences are private; restore and local hiding never submit work."""

import json

import pytest

from nexus.core.studio_terminal_sessions import TerminalSessions


class Client:
    terminal_scope = {
        "origin": "https://studio.example",
        "workspace": "/project",
        "user": "alice",
        "mode": "team",
    }
    capabilities = {"session_title": True}

    def __init__(self):
        self.calls = []
        self.status = "completed"
        self.denied = False

    def request(self, path, data=None):
        assert data is None, "Session selection must never submit or delete server work"
        self.calls.append(path)
        if self.denied:
            raise ValueError("Access revoked")
        if path == "/api/runs/run":
            return {"id": "run", "session_id": "saved", "status": self.status}
        if path == "/api/sessions":
            return {"items": [{"id": "saved", "title": "Saved"}]}
        return {"id": "saved", "title": "Saved", "messages": [{"content": "PRIVATE_CONTENT"}]}


def test_restore_checks_access_and_records_only_private_selection_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    client = Client()
    original = TerminalSessions(client)
    original.index.select("saved", run_id="run")
    restored = TerminalSessions(client)
    restored.restore()
    assert restored.session_id == "saved"
    assert client.calls == ["/api/runs/run", "/api/sessions/saved"]
    path = original.index.root / "active.json"
    assert path.stat().st_mode & 0o777 == 0o600 and "PRIVATE_CONTENT" not in path.read_text()
    client.denied = True
    denied = TerminalSessions(client)
    with pytest.raises(ValueError, match="revoked"):
        denied.restore()
    assert denied.session_id is None


@pytest.mark.parametrize("status", ["queued", "running", "waiting", "uncertain"])
def test_restoring_pending_receipt_never_resubmits_or_selects_another_chat(
    status, tmp_path, monkeypatch
):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    client = Client()
    client.status = status
    sessions = TerminalSessions(client)
    sessions.index.select("saved", run_id="run")
    with pytest.raises(ValueError, match="No task was resubmitted"):
        sessions.restore()
    assert sessions.session_id is None and client.calls == ["/api/runs/run"]


def test_hidden_markers_do_not_resurrect_on_another_terminal_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    first, second = TerminalSessions(Client()), TerminalSessions(Client())
    first.index.hide("saved")
    second.index.hide("other")
    second.index.select("saved", run_id="run")
    assert first.index.hidden("saved") and second.index.hidden("other")
    first.restore()
    assert first.session_id is None and not first.client.calls
    first.select({"id": "saved"})
    assert not second.index.hidden("saved") and second.index.hidden("other")


@pytest.mark.parametrize(
    "record",
    [
        [],
        {"format": "unsupported"},
        {"format": "nexus-studio-terminal-selection/v1", "session_id": 3},
    ],
)
def test_corrupt_or_unsupported_selection_is_preserved_and_fails_closed(
    record, tmp_path, monkeypatch
):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    sessions = TerminalSessions(Client())
    sessions.index.select(None)
    path = sessions.index.root / "active.json"
    path.write_text(json.dumps(record))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        sessions.restore()
    assert path.read_bytes() == before and not sessions.client.calls


def test_legacy_server_does_not_silently_discard_pending_title(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    client = Client()
    sessions = TerminalSessions(client)
    sessions.index.select(None, pending_title="New title")
    client.capabilities = {}
    with pytest.raises(ValueError, match="requires server support"):
        sessions.restore()
    assert not client.calls


def test_blank_new_label_is_unnamed_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_TERMINAL_STATE_DIR", str(tmp_path))
    sessions = TerminalSessions(Client())
    assert sessions.dispatch('/new ""')
    restored = TerminalSessions(Client())
    restored.restore()
    assert restored.session_id is None and restored.pending_title is None


def test_preference_write_failure_reports_accepted_run_instead_of_replaying(monkeypatch):
    sessions = TerminalSessions(Client())

    def broken(*args, **kwargs):
        raise OSError("Local disk unavailable")

    monkeypatch.setattr(sessions.index, "select", broken)
    with pytest.raises(ValueError, match="Run accepted-run was accepted"):
        sessions.accepted({"id": "accepted-run", "session_id": "saved"})
    assert not sessions.client.calls
