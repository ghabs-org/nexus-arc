"""Coordinator selection stays scoped and A2A calls follow declared protocols."""

import time

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def done(app, run):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        current = app.run(run["id"])
        if current["status"] not in {"running", "queued"}:
            return current
        time.sleep(0.01)
    pytest.fail("Run did not finish")


@pytest.mark.parametrize("response,accepted", [("beta", True), ("not-an-agent", False)])
def test_automatic_selection_uses_explicit_fallback_and_no_host_router(
    tmp_path, monkeypatch, response, accepted
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-used")

    def forbidden(*args, **kwargs):
        pytest.fail("Coordinator used global router")

    monkeypatch.setattr("nexus.agents.coordinator._call_nexus_router", forbidden)
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(
                success=True,
                output=response
                if "You are a coordinator." in context.prompt
                else "Selected specialist result",
            )

    app = StudioService(tmp_path, provider_factory=lambda name: Provider())
    try:
        for name in ["alpha", "beta"]:
            app.save_definition("agents", {"name": name, "instructions": "Use evidence"})
        run = done(
            app,
            app.start_run(
                {
                    "kind": "delegation",
                    "strategy": "automatic",
                    "agent_ids": ["alpha", "beta"],
                    "default_agent_id": "beta",
                    "coordinator_provider": "codex",
                    "task": "Choose specialist",
                }
            ),
        )
        assert run["status"] == "completed", run
        assert (
            run["coordinator_selected_agent"] == "beta"
            and run["coordinator_choice_accepted"] == accepted
        )
        assert run["delegated_agents"] == ["beta"] and len(calls) == 2
    finally:
        app.close()


@pytest.mark.parametrize("version", ["1.0", "0.3"])
def test_a2a_negotiates_card_and_polls_one_submitted_task(tmp_path, version):
    calls = []
    origin = "http://127.0.0.1:9998"

    def request(url, body, headers):
        calls.append((url, body, headers))
        if body is None:
            return (
                {
                    "name": "Remote researcher",
                    "description": "Evidence",
                    "supportedInterfaces": [
                        {
                            "url": origin + "/rpc",
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                }
                if version == "1.0"
                else {"name": "Remote", "protocolVersion": "0.3.0", "url": origin + "/rpc"}
            )
        method = body["method"]
        if method in {"SendMessage", "message/send"}:
            task = {
                "id": "remote-1",
                "kind": "task",
                "status": {"state": "TASK_STATE_WORKING" if version == "1.0" else "working"},
            }
            result = {"task": task} if version == "1.0" else task
        else:
            assert method in {"GetTask", "tasks/get"}
            result = {
                "id": "remote-1",
                "kind": "task",
                "status": {"state": "completed"},
                "artifacts": [{"parts": [{"text": "Remote evidence"}]}],
            }
        return {"jsonrpc": "2.0", "id": body["id"], "result": result}

    app = StudioService(tmp_path, external_options={"request_client": request})
    try:
        app.catalog.save(
            "externalagents",
            {
                "name": "Research service",
                "data": {
                    "url": origin,
                    "allow_private": True,
                    "enabled": True,
                    "protocol_version": version,
                },
            },
        )
        run = done(
            app,
            app.start_run(
                {
                    "kind": "external",
                    "external_agent_id": "research-service",
                    "task": "Check evidence",
                }
            ),
        )
        assert run["status"] == "completed" and run["output"] == "Remote evidence", run
        sends = [
            body
            for _, body, _ in calls
            if body and body["method"] in {"SendMessage", "message/send"}
        ]
        assert len(sends) == 1
        assert sends[0]["params"]["message"]["role"] == (
            "ROLE_USER" if version == "1.0" else "user"
        )
        assert app.records("external-runs")[0]["task_id"] == "remote-1"
    finally:
        app.close()


def test_a2a_card_cannot_redirect_account_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONAL_AGENT_KEY", "private-key")

    def request(url, body, headers):
        assert headers["Authorization"] == "Bearer private-key"
        return {
            "name": "Wrong destination",
            "supportedInterfaces": [
                {
                    "url": "https://outside.invalid/rpc",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ],
        }

    app = StudioService(tmp_path, external_options={"request_client": request})
    try:
        app.catalog.save(
            "externalagents",
            {
                "name": "Agent",
                "data": {
                    "url": "http://127.0.0.1:9998",
                    "allow_private": True,
                    "enabled": True,
                    "token_env": "PERSONAL_AGENT_KEY",
                },
            },
        )
        with pytest.raises(ValueError, match="configured origin"):
            app.external.discover("agent", None)
        assert app.records("external-runs") == []
    finally:
        app.close()
