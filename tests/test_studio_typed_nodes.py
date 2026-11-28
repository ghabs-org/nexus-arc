"""Decision branching, approved effects and retry behavior use real graph runtime."""

import time
from unittest.mock import AsyncMock

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


@pytest.fixture
def app(tmp_path):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(success=True, output="Verified")

    service = StudioService(
        tmp_path,
        provider_factory=lambda name: Provider(),
        workbench_options={
            "decision_client": lambda *args, **kwargs: {
                "route": {"choice": "review", "confidence": 0.9}
            }
        },
    )
    service.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    yield service, calls
    service.close()


def test_decision_node_routes_exact_named_choice_and_keeps_trace(app):
    service, calls = app
    graph = {
        "nodes": [
            {
                "id": "choose",
                "name": "Choose",
                "kind": "decision",
                "decision": {
                    "instructions": "Choose route",
                    "options": {"review": "Check", "research": "Gather"},
                    "default": "research",
                    "backend": "openrouter",
                    "threshold": 0.7,
                },
            },
            {"id": "review", "name": "Review", "agent_id": "reviewer", "prompt": "{task}"},
            {"id": "research", "name": "Research", "kind": "delay", "delay_seconds": 0},
        ],
        "edges": [
            {"source": "START", "target": "choose"},
            {"source": "choose", "target": "review", "choice": "review"},
            {"source": "choose", "target": "research", "choice": "research"},
        ],
    }
    service.save_definition("workflows", {"name": "Route", "graph": graph})
    run = done(
        service,
        service.start_run({"kind": "workflow", "workflow_id": "route", "task": "Check evidence"}),
    )
    assert run["status"] == "completed", run
    assert run["graph_nodes"] == {
        "choose": "completed",
        "review": "completed",
        "research": "skipped",
    }
    assert len(calls) == 1
    assert service.records("decisions")[0]["choice"] == "review"
    assert len(service.records("node-traces")) == 2


def test_decision_node_resolves_catalog_decision_model_and_tracks_lifecycle_dependency(app):
    service, calls = app
    service.catalog.save(
        "decisionmodels",
        {
            "id": "saved-router",
            "name": "Saved Router",
            "data": {
                "backend": "openrouter",
                "model": "anthropic/claude-3.5-sonnet",
                "timeout_seconds": 20,
            },
        },
    )
    graph = {
        "nodes": [
            {
                "id": "choose",
                "name": "Choose",
                "kind": "decision",
                "decision": {
                    "instructions": "Choose route",
                    "options": {"review": "Check", "research": "Gather"},
                    "default": "research",
                    "decision_model_id": "saved-router",
                    "threshold": 0.7,
                },
            },
            {"id": "review", "name": "Review", "agent_id": "reviewer", "prompt": "{task}"},
            {"id": "research", "name": "Research", "kind": "delay", "delay_seconds": 0},
        ],
        "edges": [
            {"source": "START", "target": "choose"},
            {"source": "choose", "target": "review", "choice": "review"},
            {"source": "choose", "target": "research", "choice": "research"},
        ],
    }
    wf = service.save_definition(
        "workflows",
        {"id": "route-with-model", "name": "Route With Model", "graph": graph},
    )

    from nexus.core.studio_resource_lifecycle import dependencies

    deps = dependencies("workflows", wf["document"])
    assert "decisionmodels:saved-router" in deps

    run = done(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": "route-with-model", "task": "Check evidence"}
        ),
    )
    assert run["status"] == "completed", run
    assert run["graph_nodes"]["choose"] == "completed"
    assert run["graph_nodes"]["review"] == "completed"


def test_mutating_integration_requires_gate_and_is_never_retried(app):
    service, _ = app
    adapter = AsyncMock()
    adapter.add_comment.return_value = {"id": "comment-1"}
    service.connections.factories["github"] = lambda *args: adapter
    service.catalog.save(
        "connectors",
        {
            "name": "Git",
            "data": {"type": "github", "repo": "o/r", "actions": ["comment"], "enabled": True},
        },
    )
    node = {
        "id": "send",
        "name": "Comment",
        "kind": "integration",
        "connector_id": "git",
        "action": "comment",
        "arguments": {"issue_id": "1", "body": "Reviewed {previous}"},
    }
    with pytest.raises(ValueError, match="approval"):
        service.graph.validate({"nodes": [node], "edges": [{"source": "START", "target": "send"}]})
    with pytest.raises(ValueError, match="retries"):
        service.graph.validate(
            {"nodes": [{**node, "retries": 1}], "edges": [{"source": "START", "target": "send"}]}
        )
    graph = {
        "nodes": [{"id": "gate", "name": "Review comment", "kind": "approval"}, node],
        "edges": [{"source": "START", "target": "gate"}, {"source": "gate", "target": "send"}],
    }
    service.save_definition("workflows", {"name": "Approved effect", "graph": graph})
    run = done(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": "approved-effect", "task": "Evidence"}
        ),
    )
    assert run["status"] == "waiting" and adapter.add_comment.await_count == 0
    run = done(service, service.graph.resume(run["id"], "approve"))
    assert run["status"] == "completed", run
    assert adapter.add_comment.await_count == 1


def test_readonly_integration_retry_has_attempt_traces(app):
    service, _ = app
    adapter = AsyncMock()
    adapter.list_open_issues.side_effect = [RuntimeError("temporary"), [{"number": 1}]]
    service.connections.factories["github"] = lambda *args: adapter
    service.catalog.save(
        "connectors",
        {
            "name": "Git",
            "data": {"type": "github", "repo": "o/r", "actions": ["list_issues"], "enabled": True},
        },
    )
    graph = {
        "nodes": [
            {
                "id": "read",
                "name": "Read",
                "kind": "integration",
                "connector_id": "git",
                "action": "list_issues",
                "retries": 1,
            }
        ],
        "edges": [{"source": "START", "target": "read"}],
    }
    service.save_definition("workflows", {"name": "Retry read", "graph": graph})
    run = done(
        service,
        service.start_run({"kind": "workflow", "workflow_id": "retry-read", "task": "List issues"}),
    )
    assert run["status"] == "completed", run
    traces = service.records("node-traces")
    assert {t["status"] for t in traces} == {"failed", "completed"}
    assert adapter.list_open_issues.await_count == 2


def test_multi_question_typed_decision_node_routes_and_escalates(app):
    service, calls = app
    decision_answers = {
        "priority": {
            "type": "choice",
            "choice": "critical",
            "confidence": 0.95,
            "probabilities": {"critical": 0.95, "low": 0.05},
        },
        "department": {
            "type": "choice",
            "choice": "technical",
            "confidence": 0.9,
            "probabilities": {"technical": 0.9, "billing": 0.1},
        },
    }
    service.workbench.jobs.decision_client = lambda *args, **kwargs: decision_answers

    graph = {
        "nodes": [
            {
                "id": "classify",
                "name": "Classify",
                "kind": "decision",
                "decision": {
                    "format": "nexus-typed-decision/v1",
                    "backend": "openrouter",
                    "model": "typesafe/jev-1.13",
                    "questions": {
                        "priority": {
                            "type": "choice",
                            "instructions": "Urgency",
                            "criteria": {"critical": "Outage", "low": "Question"},
                        },
                        "department": {
                            "type": "choice",
                            "instructions": "Team",
                            "criteria": {"technical": "Dev", "billing": "Pay"},
                        },
                    },
                    "route_on": "priority",
                    "routes": {"critical": "escalate"},
                    "default": "resolve",
                    "escalate": "escalate",
                    "escalation_mode": "handoff",
                    "min_confidence": 0.8,
                },
            },
            {"id": "escalate", "name": "Escalate", "agent_id": "reviewer", "prompt": "{task}"},
            {"id": "resolve", "name": "Resolve", "kind": "delay", "delay_seconds": 0},
        ],
        "edges": [
            {"source": "START", "target": "classify"},
            {"source": "classify", "target": "escalate", "choice": "escalate"},
            {"source": "classify", "target": "resolve", "choice": "resolve"},
        ],
    }
    service.save_definition("workflows", {"name": "Triage", "graph": graph})
    run = done(
        service,
        service.start_run({"kind": "workflow", "workflow_id": "triage", "task": "Database is down"}),
    )
    assert run["status"] == "completed", run
    assert run["graph_nodes"]["classify"] == "completed"
    assert run["graph_nodes"]["escalate"] == "completed"
    assert run["graph_nodes"]["resolve"] == "skipped"

    # Verify low confidence triggers escalation
    decision_answers["priority"]["confidence"] = 0.5
    run2 = done(
        service,
        service.start_run({"kind": "workflow", "workflow_id": "triage", "task": "Uncertain query"}),
    )
    assert run2["status"] == "completed", run2
    assert run2["graph_nodes"]["escalate"] == "completed"

    # Verify low priority with high confidence routes to default (resolve)
    decision_answers["priority"]["choice"] = "low"
    decision_answers["priority"]["confidence"] = 0.9
    decision_answers["priority"]["probabilities"] = {"critical": 0.1, "low": 0.9}
    run3 = done(
        service,
        service.start_run({"kind": "workflow", "workflow_id": "triage", "task": "Just a question"}),
    )
    assert run3["status"] == "completed", run3
    assert run3["graph_nodes"]["resolve"] == "completed"
    assert run3["graph_nodes"]["escalate"] == "skipped"

