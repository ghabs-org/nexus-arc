"""Studio exercises the real definition loaders, provider boundary and HTTP API."""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


class Provider:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        self.calls.append((self.name, context))
        if "fail this task" in context.prompt:
            return AgentResult(success=False, output="", error="Account usage limit reached")
        return AgentResult(success=True, output=f"Reply from {self.name}", provider_used=self.name)


@pytest.fixture
def studio(tmp_path):
    calls = []
    service = StudioService(tmp_path, provider_factory=lambda name: Provider(name, calls))
    yield service, calls
    service.close()


def wait_run(service, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = service.run(identity)
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.01)
    pytest.fail("Studio task did not finish")


def test_agent_selection_model_instructions_and_conversation_survive_restart(studio):
    from nexus.adapters.ai.registry import AgentRegistry

    service, calls = studio
    created = service.save_definition(
        "agents",
        {
            "name": 'Reviewer "A"',
            "provider": "claude-code",
            "model": "opus",
            "instructions": "Review for correctness.",
        },
    )
    assert AgentRegistry(service.root / "agents").get_provider_name(created["id"]) == "claude-code"
    run = service.start_run({"task": "Remember the test project", "agent_id": created["id"]})
    done = wait_run(service, run["id"])
    assert done["status"] == "completed"
    assert calls[0][0] == "claude-code"
    assert calls[0][1].model_override == "opus"
    assert "Review for correctness." in calls[0][1].prompt
    assert calls[0][1].workspace == service.root
    restored = StudioService(service.root, provider_factory=lambda name: Provider(name, calls))
    try:
        assert (
            restored.session(run["session_id"])["messages"][-1]["content"]
            == "Reply from claude-code"
        )
        second = restored.start_run(
            {"task": "What did I tell you?", "session_id": run["session_id"], "provider": "codex"}
        )
        assert wait_run(restored, second["id"])["status"] == "completed"
        assert "Remember the test project" in calls[-1][1].prompt
        assert calls[-1][0] == "codex"
    finally:
        restored.close()


def test_failed_provider_does_not_fall_back_or_commit_a_successful_reply(studio):
    service, calls = studio
    run = service.start_run({"task": "fail this task", "provider": "claude-code"})
    done = wait_run(service, run["id"])
    assert done["status"] == "failed"
    assert "usage limit" in done["error"]
    assert [name for name, _ in calls] == ["claude-code"]
    assert service.records("sessions") == []


def test_sequential_workflow_uses_real_engine_and_per_agent_providers(studio):
    service, calls = studio
    service.save_definition("agents", {"name": "Planner", "provider": "claude-code"})
    service.save_definition(
        "agents", {"name": "Builder", "provider": "codex", "model": "test-model"}
    )
    service.save_definition(
        "workflows",
        {
            "name": "Build a feature",
            "steps": [
                {"id": "plan", "agent_type": "planner", "prompt": "Plan {task}"},
                {"id": "build", "agent_type": "builder", "prompt": "Implement {task}"},
            ],
        },
    )
    assert service.validate_workflow("build-a-feature")["valid"]
    run = service.start_run(
        {"kind": "workflow", "workflow_id": "build-a-feature", "task": "a task board"}
    )
    done = wait_run(service, run["id"])
    assert done["status"] == "completed", done
    assert [name for name, _ in calls] == ["claude-code", "codex"]
    assert "Reply from claude-code" in calls[1][1].prompt
    assert "Implement a task board" in calls[1][1].prompt
    assert calls[1][1].model_override == "test-model"
    records = list((service.state / "workflow-runtime" / "workflows").glob("*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text())["state"] == "completed"


def test_workflow_approval_gate_prevents_provider_execution(studio):
    service, calls = studio
    service.save_definition("agents", {"name": "Reviewer", "provider": "codex"})
    service.save_definition(
        "workflows",
        {
            "name": "Release",
            "steps": [
                {"id": "review", "agent_type": "reviewer", "require_human_approval": True},
            ],
        },
    )
    run = service.start_run(
        {"kind": "workflow", "workflow_id": "release", "task": "Review release"}
    )
    done = wait_run(service, run["id"])
    assert done["status"] == "waiting", done
    assert done["awaiting_approval"][0]["name"]
    assert calls == []
    resumed = wait_run(service, service.graph.resume(done["id"], "approve")["id"])
    assert resumed["status"] == "completed" and len(calls) == 1


def test_tools_run_through_the_registry(studio):
    service, _ = studio
    run = service.start_run({"kind": "tool", "tool": "text:wordcount", "args": ["one two three"]})
    done = wait_run(service, run["id"])
    assert done["status"] == "completed"
    assert done["output"] == "3"
    assert done["provider"] == "local-tool"
    with pytest.raises(ValueError, match="Unknown tool"):
        service.start_run({"kind": "tool", "tool": "unknown"})


def test_invalid_definitions_and_paths_never_escape_workspace(studio, tmp_path):
    service, _ = studio
    with pytest.raises(ValueError, match="identifier"):
        service.save_definition("agents", {"id": "../outside", "name": "Bad"})
    service.save_definition("agents", {"name": "Research", "instructions": "Keep this"})
    with pytest.raises(FileExistsError):
        service.save_definition("agents", {"name": "Research", "instructions": "Replace"})
    assert service.definition("agents", "research")["spec"]["purpose"] == "Keep this"
    with pytest.raises(ValueError):
        service.save_definition("workflows", {"name": "Broken", "steps": []})
    (tmp_path / "agents" / "escape.yaml").symlink_to("/etc/hosts")
    assert "escape" not in [a["id"] for a in service.definitions("agents")]
    with pytest.raises(ValueError):
        service.session("../../outside")


def test_provider_inventory_never_claims_authentication_or_unlimited_quota(studio, monkeypatch):
    service, _ = studio
    monkeypatch.setattr("nexus.core.studio.shutil.which", lambda name: "/usr/local/bin/" + name)
    providers = service.providers()
    assert {"codex", "claude-code", "openai", "claude"} <= {p["id"] for p in providers}
    assert all(p["auth_verified"] is False and p["quota"] is None for p in providers)
    assert next(p for p in providers if p["id"] == "claude-code")["status"] == "CLI installed"


def test_claude_code_is_a_framework_adapter():
    from nexus.adapters.ai.claude_code_provider import ClaudeCodeProvider
    from nexus.adapters.registry import AdapterRegistry

    assert isinstance(AdapterRegistry().create_ai("claude-code"), ClaudeCodeProvider)


def test_agy_provider_and_effort_in_studio(studio):
    from nexus.adapters.ai.agy_provider import AntigravityProvider
    from nexus.adapters.registry import AdapterRegistry

    assert isinstance(AdapterRegistry().create_ai("agy"), AntigravityProvider)
    service, calls = studio
    assert "agy" in {p["id"] for p in service.providers()}
    created = service.save_definition(
        "agents",
        {
            "name": "Agy Architect",
            "provider": "agy",
            "model": "opus-5-5",
            "effort": "high",
            "instructions": "Architect carefully.",
        },
    )
    assert created["document"]["spec"]["provider"] == "agy"
    assert created["document"]["spec"]["effort"] == "high"
    run = service.start_run(
        {"task": "Design API", "agent_id": created["id"], "effort": "xhigh"}
    )
    done = wait_run(service, run["id"])
    assert done["status"] == "completed"
    assert done["provider"] == "agy"
    assert done["effort"] == "xhigh"
    assert calls[-1][0] == "agy"
    assert calls[-1][1].model_override == "opus-5-5"
    assert calls[-1][1].effort == "xhigh"


def test_local_workspace_does_not_require_bot_configuration():
    import os
    import subprocess
    import sys

    environment = dict(os.environ)
    environment.pop("PROJECT_CONFIG_PATH", None)
    process = subprocess.run(
        [sys.executable, "-m", "nexus.cli", "studio", "--help"],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    assert "--dir" in process.stdout


def test_server_serves_workspace_and_rejects_untrusted_mutations(studio):
    service, _ = studio
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(service, "test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None, headers=None):
        req = urllib.request.Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            return urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            return exc

    try:
        with request("/") as response:
            assert response.status == 200
            assert "Nexus Studio" in response.read().decode()
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        with request("/api/bootstrap") as response:
            assert json.load(response)["token"] == "test-token"
        assert request("/api/agents", {"name": "Bad"}).status == 403
        assert (
            request(
                "/api/agents",
                {"name": "Bad"},
                {"X-Nexus-Studio-Token": "test-token", "Origin": "https://untrusted.example"},
            ).status
            == 403
        )
        assert request("/api/bootstrap", headers={"Host": "untrusted.example"}).status == 403
        with request(
            "/api/agents",
            {"name": "New agent", "provider": "claude-code"},
            {"X-Nexus-Studio-Token": "test-token"},
        ) as response:
            assert response.status == 201
        assert request("/api/agents", [], {"X-Nexus-Studio-Token": "test-token"}).status == 400
        assert request("/api/sessions/missing").status == 404
        for asset in ["/studio.js", "/studio.css"]:
            assert request(asset).status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_codex_respects_context_model_uses_stdin_and_stops_timed_out_process(tmp_path):
    from unittest.mock import AsyncMock, MagicMock, patch

    from nexus.adapters.ai.base import ExecutionContext
    from nexus.adapters.ai.codex_provider import CodexCLIProvider

    process = MagicMock()
    process.returncode = None
    process.communicate = AsyncMock(side_effect=TimeoutError)
    process.wait = AsyncMock(return_value=-9)
    with patch(
        "nexus.adapters.ai.codex_provider.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=process),
    ) as spawn:
        result = asyncio.run(
            CodexCLIProvider(model="default").execute_agent(
                ExecutionContext(
                    "test", "private prompt", tmp_path, model_override="selected", timeout=1
                )
            )
        )
    assert not result.success and "Timeout" in result.error
    assert spawn.call_args.args[-3:] == ("--model", "selected", "-")
    process.communicate.assert_awaited_once_with(b"private prompt")
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
