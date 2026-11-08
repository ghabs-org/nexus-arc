"""ARC workflow agents routed by nexus-router: picks, pins, hints, one outcome per launch, doctor."""

import asyncio

import pytest

from nexus.core.runtime import agent_routing
from nexus.plugins.builtin.ai_runtime_plugin import AIOrchestrator, AIProvider, _same_tool


class FakeClient:
    url = "http://router.test"

    def __init__(self, decision=None, error=None):
        self.decision = decision or {
            "decision_id": "d1",
            "selected_model": "anthropic/claude-opus-5-5",
            "fallbacks": ["openai-codex/gpt-6.1-sol"],
        }
        self.error = error
        self.routes, self.outcomes, self.observed = [], [], []

    def route(self, identity, task, **fields):
        self.routes.append((task, fields))
        if self.error:
            raise self.error
        return dict(self.decision)

    def outcome(self, identity, decision_id, **fields):
        self.outcomes.append((decision_id, fields))

    def observe(self, identity, provider, http_status=None, error_type=None, **fields):
        self.observed.append((provider, http_status, error_type, fields))

    def diagnostics(self, identity=None):
        return {"warnings": ["registry old"], "providers": {}}


class FakeOrchestrator:
    def __init__(self, available=("claude", "codex"), pin=""):
        self.available = set(available)
        self.agent_spec_model_resolver = lambda agent, project: pin

    def check_tool_available(self, tool):
        return tool.value in self.available


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setenv(agent_routing.ENABLE_ENV, "true")
    monkeypatch.setattr(agent_routing, "_client", lambda: fake)
    return fake


def pick(orchestrator=None, **kwargs):
    return agent_routing.route_agent(
        orchestrator or FakeOrchestrator(),
        prompt="Implement the parser",
        agent_type="developer",
        project_name="nexus",
        issue_num="42",
        workflow_name="full",
        **kwargs,
    )


def test_route_agent_limits_to_ready_tools_and_maps_back(client):
    chosen = pick(hint={"task_type": "coding", "cost_profile": "cheap"})
    assert chosen["tool"] == "claude" and chosen["model"] == "claude-opus-5-5"
    task, fields = client.routes[0]
    assert fields["allowed_providers"] == ["anthropic", "openai-codex"]
    assert fields["nexus_context"] == {
        "nexus_project": "nexus",
        "nexus_issue_id": "42",
        "nexus_workflow_id": "full",
        "nexus_step_id": "developer",
    }
    assert fields["classifier"] == {"task_type": "coding", "confidence": 0.9}
    assert fields["cost_profile"] == "cheap" and fields["source_type"] == "arc-workflow"


def test_route_agent_skips_excluded_and_unready_tools(client):
    chosen = pick(FakeOrchestrator(available=("claude", "codex")), exclude_tools=["claude"])
    assert chosen["tool"] == "codex" and chosen["model"] == "gpt-6.1-sol"
    assert client.routes[0][1]["allowed_providers"] == ["openai-codex"]
    assert pick(FakeOrchestrator(available=())) is None


def test_route_agent_is_advisory(client, monkeypatch):
    client.error = RuntimeError("router down")
    assert pick() is None
    monkeypatch.setenv(agent_routing.ENABLE_ENV, "false")
    monkeypatch.setattr(agent_routing, "_client", lambda: None)
    assert pick() is None


def test_pins_and_hints():
    assert agent_routing.model_pinned(FakeOrchestrator(pin="gpt-x"), "developer", "nexus")
    assert not agent_routing.model_pinned(FakeOrchestrator(pin=""), "developer", "nexus")
    assert agent_routing.step_hint({"task_type": "coding", "cost_profile": "premium"}) == {
        "task_type": "coding",
        "cost_profile": "premium",
    }
    assert agent_routing.step_hint({"task_type": "nope", "cost_profile": "huge"}) is None
    assert agent_routing.step_hint("coding") is None


def test_record_pick_and_one_outcome_per_launch(client):
    chosen = pick()
    entry = {"router_decision_id": "stale", "timestamp": 1.0}
    agent_routing.record_pick(entry, chosen, "claude")
    assert entry["router_decision_id"] == "d1" and entry["routed_tool"] == "claude"
    other = agent_routing.record_pick({"router_decision_id": "stale"}, chosen, "copilot")
    assert "router_decision_id" not in other  # launched with another tool: nothing to attribute
    assert agent_routing.report_outcome(entry, True)
    assert not agent_routing.report_outcome(entry, False)  # already reported
    assert [(d, f["success"]) for d, f in client.outcomes] == [("d1", True)]
    limited = agent_routing.record_pick({}, chosen, "claude")
    agent_routing.report_outcome(limited, False, rate_limited=True)
    assert client.observed == [("anthropic", 429, "rate_limit", {"quota_hint": "low"})]


def test_invoke_agent_model_override_only_for_the_preferred_tool(monkeypatch):
    orchestrator = AIOrchestrator()
    seen = {}

    def fake_invoke_codex(*args, model_override="", **kwargs):
        seen["codex"] = model_override
        return 1234

    monkeypatch.setattr(orchestrator, "_invoke_codex", fake_invoke_codex)
    monkeypatch.setattr(orchestrator, "check_tool_available", lambda tool: True)
    monkeypatch.setattr(orchestrator, "_resolve_model_for_tool", lambda **_: "profile-model")
    pid, tool = orchestrator.invoke_agent(
        "prompt", "/tmp", "/tmp", "/tmp", preferred_tool="codex", model_override="gpt-6.1-sol"
    )
    assert (pid, tool, seen["codex"]) == (1234, AIProvider.CODEX, "gpt-6.1-sol")
    assert _same_tool(AIProvider.CODEX, "codex") and not _same_tool(AIProvider.CLAUDE, "codex")
    assert not _same_tool(AIProvider.CODEX, None)


def test_process_orchestrator_reports_completion_once(client):
    from nexus.core.process_orchestrator import ProcessOrchestrator
    from tests.test_process_orchestrator import StubRuntime

    runtime = StubRuntime()
    runtime.tracker = {"42": agent_routing.record_pick({"timestamp": 1.0}, pick(), "claude")}

    async def noop(*_args, **_kwargs):
        return None

    process = ProcessOrchestrator(runtime=runtime, complete_step_fn=noop, nexus_dir=".nexus")
    process._report_router_outcome("42", True)
    process._report_router_outcome("42", False)
    assert [(d, f["success"]) for d, f in client.outcomes] == [("d1", True)]
    assert "router_decision_id" not in runtime.tracker["42"]


def test_workflow_step_routing_hint(tmp_path):
    from nexus.core.runtime.agent_launcher import (
        _resolve_step_requires_worktree,
        _resolve_step_routing,
    )

    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        "name: demo\nsteps:\n"
        "  - id: develop\n    agent_type: developer\n    requires_worktree: true\n"
        "    routing: {task_type: coding, cost_profile: cheap}\n"
        "  - id: review\n    agent_type: reviewer\n"
    )
    args = {"workflow_path": str(workflow), "tier_name": "", "workflow_name": "", "step_num": 0}
    assert _resolve_step_routing(step_id="develop", **args) == {
        "task_type": "coding",
        "cost_profile": "cheap",
    }
    assert _resolve_step_routing(step_id="review", **args) is None
    assert (
        _resolve_step_requires_worktree(step_id="develop", **args) is True
    )  # shared lookup unchanged


def test_doctor_router_target(monkeypatch):
    from nexus.core.command_bridge import operator as operator_module
    from nexus.core.handlers.ops_command_handlers import (
        _doctor_runtime_target,
        _format_doctor_payload,
    )

    assert _doctor_runtime_target(["router"]) == "router"
    monkeypatch.setattr(
        "nexus.core.router_client.RouterClient.from_env",
        classmethod(lambda cls, default_url=None, timeout=10: FakeClient()),
    )
    service = operator_module.BridgeOperatorService.__new__(operator_module.BridgeOperatorService)
    payload = asyncio.run(service.doctor(target="router"))
    assert payload["scope"] == "router" and payload["diagnostics"]["warnings"] == ["registry old"]
    text = _format_doctor_payload(payload, fix_requested=False)
    assert "Target: `router`" in text and "• registry old" in text
