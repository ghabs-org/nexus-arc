"""Explicit resubmission preserves context and blocks uncertain execution states."""

import pytest

from nexus.core.studio_terminal_retry import TerminalRetry


class Client:
    capabilities = {"expected_artifact_refs": True}

    def __init__(self):
        self.run = {
            "id": "run",
            "session_id": "session",
            "kind": "chat",
            "status": "completed",
            "provider": "codex",
        }
        self.effects = []
        self.calls = []

    def request(self, path, data=None):
        assert data is None, "Retry preparation never submits"
        self.calls.append(path)
        return {"items": self.effects} if path == "/api/workbench/effects" else self.run


def test_retry_keeps_literal_payload_agent_resolved_provider_and_attachment_expectations():
    client = Client()
    client.run["artifact_refs"] = [{"id": "file", "revision": 2}]
    payload = {
        "kind": "chat",
        "task": 'Inspect {"value": 0}\nNext',
        "agent_id": "reviewer",
        "artifact_ids": ["file"],
        "approve": True,
        "TOKEN": "PRIVATE",
    }
    retry = TerminalRetry(client)
    retry.remember(payload, client.run)
    payload["artifact_ids"].clear()
    prepared = retry.prepare("session")
    assert prepared == {
        "kind": "chat",
        "task": 'Inspect {"value": 0}\nNext',
        "agent_id": "reviewer",
        "artifact_ids": ["file"],
        "session_id": "session",
        "provider": "codex",
        "expected_artifact_refs": [{"id": "file", "revision": 2}],
    }
    assert client.calls == ["/api/runs/run", "/api/workbench/effects"]
    prepared["artifact_ids"].clear()
    assert retry.prepare("session")["artifact_ids"] == ["file"]
    with pytest.raises(ValueError, match="No completed message"):
        retry.prepare("other-session")


@pytest.mark.parametrize(
    "state", ["queued", "running", "failed", "cancelled", "waiting", "uncertain"]
)
def test_noncompleted_previous_run_never_prepares_another_submission(state):
    client = Client()
    retry = TerminalRetry(client)
    retry.remember({"kind": "chat", "task": "Original"}, client.run)
    client.run["status"] = state
    with pytest.raises(ValueError, match="Inspect Activity"):
        retry.prepare("session")
    assert client.calls == ["/api/runs/run"]


@pytest.mark.parametrize("status", ["attempting", "uncertain", "completed", "reviewed-retry"])
def test_recorded_external_actions_keep_existing_review_and_recovery_controls(status):
    client = Client()
    retry = TerminalRetry(client)
    retry.remember({"kind": "chat", "task": "Original"}, client.run)
    client.effects = [{"run_id": "run", "status": status}]
    with pytest.raises(ValueError, match="existing review/recovery"):
        retry.prepare("session")


@pytest.mark.parametrize("field", ["awaiting_approval", "awaiting_input"])
def test_retry_cannot_approve_or_bypass_a_gate(field):
    client = Client()
    retry = TerminalRetry(client)
    retry.remember({"kind": "chat", "task": "Original"}, client.run)
    client.run[field] = [{"id": "gate"}]
    with pytest.raises(ValueError, match="Inspect Activity"):
        retry.prepare("session")


def test_effect_access_failure_stops_preparation():
    client = Client()
    retry = TerminalRetry(client)
    retry.remember({"kind": "chat", "task": "Original"}, client.run)

    def denied(path, data=None):
        if path == "/api/workbench/effects":
            raise ValueError("Access revoked")
        return client.run

    client.request = denied
    with pytest.raises(ValueError, match="Access revoked"):
        retry.prepare("session")


@pytest.mark.parametrize("references", [[], [{"id": "file", "revision": 1}]])
def test_legacy_server_cannot_silently_substitute_a_newer_attachment(references):
    client = Client()
    client.capabilities = {}
    client.run["artifact_refs"] = references
    retry = TerminalRetry(client)
    retry.remember({"kind": "chat", "task": "Review", "artifact_ids": ["file"]}, client.run)
    with pytest.raises(ValueError, match="revision guards"):
        retry.prepare("session")
    assert not client.calls


def test_advertised_guard_still_requires_recorded_attachment_pins():
    client = Client()
    retry = TerminalRetry(client)
    retry.remember({"kind": "chat", "task": "Review", "artifact_ids": ["file"]}, client.run)
    with pytest.raises(ValueError, match="revision guards"):
        retry.prepare("session")
    assert not client.calls
