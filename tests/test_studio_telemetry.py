"""Tests for OpenTelemetry trace export, Prometheus metrics and relational parent-child tracing."""

import pytest

from nexus.adapters.ai.base import ExecutionContext
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_team import NexusTeamPolicy
from tests.test_studio_team import NexusIdentity


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.team = None
    service.close()


def test_parent_child_relationships_and_token_usage(studio):
    # Setup agent and run
    studio.save_definition(
        "agents",
        {
            "id": "analyst",
            "name": "Analyst",
            "instructions": "Analyze query",
            "provider": "codex",
            "model": "gpt-5",
        },
    )
    run = studio.start_run({"kind": "chat", "agent_id": "analyst", "task": "Review data", "requester_nexus_id": "alice"})
    run_id = run["id"]

    # Mock provider with token usage and execute invocation
    class MockProvider:
        async def execute_agent(self, context):
            return AgentResult(
                success=True,
                output="Analysis complete",
                metadata={"usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}},
            )

    ctx = ExecutionContext(
        agent_name="Analyst",
        prompt="Review data",
        workspace=studio.root,
        model_override="gpt-5",
    )
    import asyncio
    inv_result = asyncio.run(
        studio.observability.invoke(
            MockProvider(), ctx, run_id, {"requester_nexus_id": "alice"}, "codex"
        )
    )
    inv_id = inv_result.metadata["invocation_id"]

    # Record tool event linked to invocation
    studio.execution.record(
        run_id,
        "tool",
        tool="database_query",
        status="completed",
        parent_id=inv_id,
        duration_seconds=0.25,
    )
    studio._write(
        studio._path("tool-traces", "tool-trace-1"),
        {
            "id": "tool-trace-1",
            "run_id": run_id,
            "owner_id": "alice",
            "tool": "database_query",
            "parent_id": inv_id,
            "ok": True,
            "duration_seconds": 0.25,
        },
    )

    # 1. Verify token usage aggregation
    usage = studio.telemetry.summarize_run_tokens(run_id)
    assert usage["total_prompt_tokens"] == 120
    assert usage["total_completion_tokens"] == 45
    assert usage["total_tokens"] == 165
    assert usage["invocations_total"] == 1
    assert usage["invocations_with_usage"] == 1
    assert "gpt-5" in usage["by_model"]
    assert usage["by_model"]["gpt-5"]["total_tokens"] == 165
    assert "codex" in usage["by_provider"]

    # 2. Verify OpenTelemetry export
    otel = studio.telemetry.export_opentelemetry_traces(run_id)
    assert "resourceSpans" in otel
    spans = otel["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) >= 3  # run root, invocation, tool

    run_span = next(s for s in spans if s["name"].startswith("run:"))
    inv_span = next(s for s in spans if s["name"] == "agent:Analyst")
    tool_span = next(s for s in spans if s["name"] == "tool:database_query")

    assert inv_span["parentSpanId"] == run_span["spanId"]
    assert tool_span["parentSpanId"] == inv_span["spanId"]
    assert inv_span["status"]["code"] == 1
    assert tool_span["status"]["code"] == 1

    # Check token attributes in invocation span
    inv_attrs = {a["key"]: a["value"] for a in inv_span["attributes"]}
    assert inv_attrs["gen_ai.usage.prompt_tokens"]["intValue"] == 120
    assert inv_attrs["gen_ai.usage.completion_tokens"]["intValue"] == 45

    # 3. Verify Prometheus metrics export
    prom = studio.telemetry.export_prometheus_metrics()
    assert "# TYPE nexus_runs_total counter" in prom
    assert "# TYPE nexus_invocations_total counter" in prom
    assert "# TYPE nexus_prompt_tokens_total counter" in prom
    assert 'nexus_prompt_tokens_total{provider="codex",model="gpt-5"} 120' in prom
    assert 'nexus_completion_tokens_total{provider="codex",model="gpt-5"} 45' in prom
    assert 'nexus_tool_calls_total{tool="database_query",status="completed"} 1' in prom


def test_telemetry_redaction_and_team_scoping(tmp_path):
    manager = NexusIdentity()
    policy = NexusTeamPolicy(
        project_key="sample",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/acceptance",
        bindings={"alice": "builder", "bob": "operator", "owner": "owner"},
        manager=manager,
        environment_resolver=lambda _: ({}, None),
    )
    studio = StudioService(
        tmp_path / "workspace", state_dir=tmp_path / "private-state", team_policy=policy
    )
    try:
        # Create run for alice with secret in task
        run = studio.start_run(
            {
                "kind": "chat",
                "requester_nexus_id": "alice",
                "task": "Deploy with Bearer sk-secret-token-123456",
            }
        )
        run_id = run["id"]

        studio._write(
            studio._path("invocation-traces", "inv-1"),
            {
                "id": "inv-1",
                "run_id": run_id,
                "owner_id": "alice",
                "provider": "codex",
                "model": "gpt-4",
                "agent": "builder",
                "status": "completed",
                "duration_seconds": 1.2,
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )

        # Alice can export her OTel traces
        alice_actor = {"nexus_id": "alice", "role": "builder"}
        otel = studio.telemetry.export_opentelemetry_traces(run_id, alice_actor)
        run_span = otel["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        task_attr = next(a for a in run_span["attributes"] if a["key"] == "nexus.task")
        # Check secret was redacted
        assert "sk-secret-token-123456" not in task_attr["value"]["stringValue"]
        assert "[redacted]" in task_attr["value"]["stringValue"]

        # Bob (operator) cannot inspect Alice's traces
        bob_actor = {"nexus_id": "bob", "role": "operator"}
        with pytest.raises(PermissionError, match="belongs to another"):
            studio.telemetry.export_opentelemetry_traces(run_id, bob_actor)

        with pytest.raises(PermissionError, match="belongs to another"):
            studio.telemetry.summarize_run_tokens(run_id, bob_actor)

        # Prometheus metrics for Bob should not count Alice's runs
        bob_prom = studio.telemetry.export_prometheus_metrics(bob_actor)
        assert 'nexus_prompt_tokens_total{' not in bob_prom

        # Owner can see all metrics
        owner_actor = {"nexus_id": "owner", "role": "owner"}
        owner_prom = studio.telemetry.export_prometheus_metrics(owner_actor)
        assert 'nexus_prompt_tokens_total{provider="codex",model="gpt-4"} 10' in owner_prom
    finally:
        studio.close()


def test_workbench_telemetry_routes(studio):
    run = studio.start_run({"kind": "chat", "task": "Process batch"})
    run_id = run["id"]
    studio._write(
        studio._path("invocation-traces", "inv-test"),
        {
            "id": "inv-test",
            "run_id": run_id,
            "provider": "codex",
            "model": "gpt-5",
            "agent": "worker",
            "status": "completed",
            "duration_seconds": 0.5,
            "usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
        },
    )

    # Test GET traces/<run_id>/otel via workbench.get
    otel = studio.workbench.get(["traces", run_id, "otel"], None, {})
    assert "resourceSpans" in otel

    # Test GET traces/<run_id>/usage via workbench.get
    usage = studio.workbench.get(["traces", run_id, "usage"], None, {})
    assert usage["total_tokens"] == 75

    # Test GET metrics via workbench.get
    metrics = studio.workbench.get(["metrics"], None, {})
    assert metrics["format"] == "prometheus"
    assert "nexus_runs_total" in metrics["text"]


def test_http_metrics_and_otel_endpoints(studio):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen

    from nexus.studio import make_studio_handler

    run = studio.start_run({"kind": "chat", "task": "Export traces test"})
    run_id = run["id"]
    studio._write(
        studio._path("invocation-traces", "inv-http"),
        {
            "id": "inv-http",
            "run_id": run_id,
            "provider": "codex",
            "model": "gpt-5",
            "agent": "worker",
            "status": "completed",
            "duration_seconds": 0.4,
            "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
        },
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "test-csrf"))
    port = server.server_port
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()
    try:
        # GET /api/metrics
        with urlopen(f"http://127.0.0.1:{port}/api/metrics") as resp:
            assert resp.status == 200
            assert "text/plain" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            assert "nexus_runs_total" in body
            assert "nexus_prompt_tokens_total" in body

        # GET /api/workbench/traces/<run_id>/otel
        with urlopen(f"http://127.0.0.1:{port}/api/workbench/traces/{run_id}/otel") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "resourceSpans" in data

        # GET /api/workbench/traces/<run_id>/usage
        with urlopen(f"http://127.0.0.1:{port}/api/workbench/traces/{run_id}/usage") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["total_tokens"] == 120
    finally:
        server.shutdown()
        server.server_close()


def test_execute_with_tools_links_parent_id_to_invocation(studio):
    import asyncio

    from nexus.core.studio_telemetry import _span_id
    from nexus.core.studio_tool_loop import execute_with_tools
    from nexus.tools import ToolResult, tool_registry

    async def dummy_tool(**kwargs):
        return ToolResult(ok=True, output={"status": "ok"})

    tool_registry.register("telemetry_test_tool", dummy_tool, description="Test tool")

    class ToolProvider:
        def __init__(self):
            self.turn = 0

        async def execute_agent(self, context):
            self.turn += 1
            if self.turn == 1:
                return AgentResult(
                    success=True,
                    output='{"tool_call":{"name":"telemetry_test_tool","arguments":{}}}',
                    metadata={"usage": {"prompt_tokens": 15, "completion_tokens": 10, "total_tokens": 25}},
                )
            return AgentResult(
                success=True,
                output='{"final":"Finished"}',
                metadata={"usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}},
            )

    run = studio.start_run({"kind": "chat", "task": "Run tool task", "requester_nexus_id": "alice"})
    run_id = run["id"]
    ctx = ExecutionContext(
        agent_name="tool_agent",
        prompt="Do dummy tool",
        workspace=studio.root,
        model_override="gpt-5",
    )
    result = asyncio.run(
        execute_with_tools(
            studio,
            ToolProvider(),
            ctx,
            ["telemetry_test_tool"],
            {"requester_nexus_id": "alice"},
            run_id,
            "codex",
        )
    )
    assert result.output == "Finished"

    # Verify tool-traces has parent_id set to the invocation ID
    tool_traces = studio.records("tool-traces", limit=None)
    matching = [t for t in tool_traces if t.get("tool") == "telemetry_test_tool"]
    assert len(matching) == 1
    tool_trace = matching[0]
    assert tool_trace.get("parent_id") is not None

    inv_traces = sorted(
        studio.records("invocation-traces", limit=None),
        key=lambda r: r.get("created_at", ""),
    )
    assert len(inv_traces) >= 1
    first_inv_id = inv_traces[0]["id"]
    assert tool_trace["parent_id"] == first_inv_id

    # Verify OTel trace export links parentSpanId
    otel = studio.telemetry.export_opentelemetry_traces(run_id)
    spans = otel["resourceSpans"][0]["scopeSpans"][0]["spans"]
    tool_span = next(s for s in spans if s["name"] == "tool:telemetry_test_tool")
    inv_span = next(s for s in spans if _span_id(first_inv_id) == s["spanId"])
    assert tool_span["parentSpanId"] == inv_span["spanId"]


def test_execute_with_tools_handles_preamble_and_markdown(studio):
    import asyncio

    from nexus.core.studio_tool_loop import execute_with_tools, extract_agent_response
    from nexus.tools import ToolResult, tool_registry

    # Test extractor directly with CFO-style preamble and markdown blocks
    cfo_text = (
        "I'll build the model in one script. Inputs are kept in their own blocks.\n\n"
        '{"tool_call":{"name":"preamble_tool","arguments":{"code":"1+1"}}}'
    )
    extracted = extract_agent_response(cfo_text)
    assert extracted == {"tool_call": {"name": "preamble_tool", "arguments": {"code": "1+1"}}}

    fenced_text = "```json\n" '{"tool_call":{"name":"preamble_tool","arguments":{}}}\n' "```"
    assert extract_agent_response(fenced_text) == {"tool_call": {"name": "preamble_tool", "arguments": {}}}

    assert extract_agent_response("Plain text response") is None

    # Test full execute_with_tools loop with preamble in provider output
    async def dummy_tool(**kwargs):
        return ToolResult(ok=True, output={"calc": 42})

    tool_registry.register("preamble_tool", dummy_tool, description="Preamble test tool")

    class PreambleProvider:
        def __init__(self):
            self.turn = 0

        async def execute_agent(self, context):
            self.turn += 1
            if self.turn == 1:
                return AgentResult(
                    success=True,
                    output="I'll run the analysis now:\n\n"
                    '{"tool_call":{"name":"preamble_tool","arguments":{}}}',
                )
            return AgentResult(
                success=True,
                output="Here is the calculated result: 42.",
            )

    run = studio.start_run({"kind": "chat", "task": "Preamble test", "requester_nexus_id": "bob"})
    ctx = ExecutionContext(
        agent_name="cfo_test_agent",
        prompt="Calculate finance",
        workspace=studio.root,
        model_override="gpt-5",
    )
    try:
        result = asyncio.run(
            execute_with_tools(
                studio,
                PreambleProvider(),
                ctx,
                ["preamble_tool"],
                {"requester_nexus_id": "bob"},
                run["id"],
                "codex",
            )
        )
    finally:
        tool_registry.unregister("preamble_tool")
    assert result.output == "Here is the calculated result: 42."
