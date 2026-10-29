"""Reviewed connector/tool mappings reuse native operations and approval gates."""

import copy
import json

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_workflow_v2 import wait


def documents():
    return [
        {
            "kind": "agent",
            "name": "reviewer",
            "spec": {
                "systemPrompt": "Review supplied material",
                "modelProvider": ["account"],
                "skillRefs": ["guidance"],
            },
        },
        {
            "kind": "workflow",
            "name": "review-flow",
            "spec": {
                "appConfig": {
                    "toolsets": ["local"],
                    "nodes": [
                        {
                            "id": "count",
                            "type": "tool",
                            "tool_name": "words",
                            "input": {"text": "{{workflow.input.text}}"},
                        },
                        {
                            "id": "lookup",
                            "type": "tool",
                            "connector": "service",
                            "tool": "lookup",
                            "depends_on": ["count"],
                            "input": {"id": "{{workflow.input.id}}"},
                        },
                        {
                            "id": "review",
                            "type": "agent",
                            "agent_name": "reviewer",
                            "depends_on": ["lookup"],
                            "input": {"message": "Review {{lookup.output}} and {{count.output}}"},
                        },
                        {"id": "approve", "type": "approval", "depends_on": ["review"]},
                        {
                            "id": "record",
                            "type": "tool",
                            "connector": "service",
                            "tool": "record",
                            "depends_on": ["approve"],
                            "input": {"reply": "{{review.output}}"},
                        },
                    ],
                    "output_mapping": {"result": "{{record.output}}"},
                }
            },
        },
    ]


def seed(studio):
    studio.catalog.save(
        "skills", {"id": "guidance", "name": "Guidance", "data": {"content": "Use evidence only"}}
    )
    studio.catalog.save(
        "toolsets", {"id": "local", "name": "Local", "data": {"tools": ["text:wordcount"]}}
    )
    record = studio.catalog.save(
        "connectors",
        {
            "id": "service",
            "name": "Service",
            "data": {
                "type": "openapi",
                "url": "https://example.test",
                "enabled": True,
                "actions": ["read", "invoke"],
                "operations": ["lookup", "record"],
                "spec": {
                    "openapi": "3.0.3",
                    "paths": {
                        "/lookup": {
                            "get": {
                                "operationId": "lookup",
                                "parameters": [
                                    {
                                        "name": "id",
                                        "in": "query",
                                        "required": True,
                                        "schema": {"type": "string"},
                                    }
                                ],
                            }
                        },
                        "/record": {
                            "post": {
                                "operationId": "record",
                                "requestBody": {
                                    "required": True,
                                    "content": {"application/json": {"schema": {"type": "object"}}},
                                },
                            }
                        },
                    },
                },
            },
        },
    )
    return record


BINDINGS = {
    "models": {"account": {"provider": "codex"}},
    "skills": {"guidance": "guidance"},
    "toolsets": {"local": "local"},
    "tools": {"words": "text:wordcount"},
    "connectors": {"service": "service"},
    "operations": {
        "service/lookup": {"operation_id": "lookup", "input": "parameters"},
        "service/record": {"operation_id": "record", "input": "body"},
    },
}


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    seed(service)
    yield service
    service.close()


def convert(studio, docs=None, bindings=None):
    return studio.configuration.convert_interop(
        {
            "documents": docs if docs is not None else documents(),
            "bindings": bindings if bindings is not None else BINDINGS,
        },
        None,
    )


def test_complete_reviewed_bundle_roundtrips_and_waits_before_write(studio, monkeypatch):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            assert "Use evidence only" in context.prompt
            calls.append(("agent", context.prompt))
            return AgentResult(success=True, output="Reviewed")

    async def execute(identity, data, actor=None, **kwargs):
        calls.append((data["action"], copy.deepcopy(data["arguments"])))
        return {"saved": True} if data["action"] == "invoke" else {"evidence": "Fixture"}

    monkeypatch.setattr(studio, "provider_factory", lambda _: Provider())
    monkeypatch.setattr(studio.connections, "execute", execute)
    result = convert(studio)
    assert result["valid"], result
    assert not calls and not studio.definitions("agents") and not studio.definitions("workflows")
    exported = studio.configuration.export_interop({"document": result["document"]}, None)
    assert exported["valid"], exported
    roundtrip = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
    assert roundtrip["valid"] and roundtrip["document"] == result["document"]
    plan = studio.configuration.plan(result["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    run = wait(
        studio,
        studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": "review-flow",
                "task": "Review",
                "workflow_input": {"id": "7", "text": "one two"},
            }
        ),
        "waiting",
    )
    assert [kind for kind, _ in calls] == ["read", "agent"]
    assert calls[0][1] == {"operation_id": "lookup", "parameters": {"id": "7"}}
    completed = wait(studio, studio.graph.resume(run["id"], "approve"))
    assert [kind for kind, _ in calls] == ["read", "agent", "invoke"]
    assert calls[-1][1] == {"operation_id": "record", "body": {"reply": "Reviewed"}}
    assert json.loads(completed["output"]) == {"result": {"saved": True}}


@pytest.mark.parametrize(
    "failure",
    [
        "unbound_connector",
        "unbound_operation",
        "operation_not_selected",
        "missing_gate",
        "ambiguous_tool",
        "undeclared_builtin",
        "bad_input_mode",
        "mutating_retry",
        "unknown_field",
    ],
)
def test_unreviewed_or_invalid_mapping_rejects_entire_document(studio, failure):
    docs, bindings = documents(), copy.deepcopy(BINDINGS)
    nodes = docs[1]["spec"]["appConfig"]["nodes"]
    if failure == "unbound_connector":
        bindings["connectors"] = {}
    elif failure == "unbound_operation":
        bindings["operations"] = {}
    elif failure == "operation_not_selected":
        bindings["operations"]["service/lookup"]["operation_id"] = "unknown"
    elif failure == "missing_gate":
        nodes[-1]["depends_on"] = ["review"]
    elif failure == "ambiguous_tool":
        nodes[1]["tool_name"] = "words"
    elif failure == "undeclared_builtin":
        docs[1]["spec"]["appConfig"]["toolsets"] = []
    elif failure == "bad_input_mode":
        bindings["operations"]["service/lookup"]["input"] = []
    elif failure == "mutating_retry":
        nodes[-1]["retry_strategy"] = {"limit": 1, "retry_policy": "Always"}
    else:
        nodes[1]["hidden_option"] = True
    report = convert(studio, docs, bindings)
    assert not report["valid"] and report["document"] is None, report
    assert not studio.records("configuration-plans") and not studio.records("runs")
