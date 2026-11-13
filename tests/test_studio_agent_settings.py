"""Agent resource references are validated, source-exported and pinned at queue time."""

import copy
import json
import sys

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_configuration import digest
from tests.test_studio_workflow_v2 import wait


@pytest.fixture
def fixture(tmp_path):
    contexts, providers, answers = [], [], ["1"]

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            contexts.append(context)
            return AgentResult(
                success=True, output=answers[min(len(contexts) - 1, len(answers) - 1)]
            )

    def factory(name):
        providers.append(name)
        return Provider()

    studio = StudioService(tmp_path, provider_factory=factory)
    studio.catalog.save(
        "models",
        {"id": "chosen", "name": "Chosen", "data": {"provider": "codex", "model": "first-model"}},
    )
    studio.catalog.save(
        "skills", {"id": "evidence", "name": "Evidence", "data": {"content": "FIRST SKILL CONTENT"}}
    )
    studio.catalog.save(
        "toolsets",
        {"id": "local-tools", "name": "Local tools", "data": {"tools": ["text:wordcount"]}},
    )
    try:
        yield studio, contexts, providers, answers
    finally:
        studio.close()


def source():
    return {
        "agent_name": "resource-agent",
        "instruction": "Use supplied evidence.",
        "request_timeout_seconds": 20,
        "max_llm_calls_per_task": 2,
        "output_schema": {"type": "integer"},
        "skills": [{"name": "evidence-bundle"}],
        "tools": [{"tool_type": "builtin-group", "group_name": "local"}],
        "agent_card": {
            "description": "Checks evidence",
            "skills": [
                {
                    "id": "review",
                    "name": "Review",
                    "description": "Review supplied evidence",
                    "tags": ["review"],
                    "examples": ["Review this claim"],
                }
            ],
        },
    }


def bindings():
    return {
        "agents": {"resource-agent": {"profile": "chosen"}},
        "skills": {"evidence-bundle": "evidence"},
        "toolsets": {"local": "local-tools"},
    }


def install(studio, value=None, mapping=None):
    converted = studio.configuration.convert_interop(
        {"documents": [value or source()], "bindings": mapping or bindings()}, None
    )
    assert converted["valid"], converted
    plan = studio.configuration.plan(converted["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    return converted["document"]


def test_bound_resources_runtime_settings_and_capabilities_round_trip(fixture):
    studio, contexts, providers, _ = fixture
    document = install(studio)
    spec = document["definitions"][0]["document"]["spec"]
    assert spec["model_id"] == "chosen"
    assert spec["max_model_calls"] == 2 and spec["timeout_seconds"] == 20
    assert spec["skill_ids"] == ["evidence"] and spec["toolset_ids"] == ["local-tools"]
    assert spec["agent_card"]["skills"][0]["id"] == "review"
    exported = studio.configuration.export_interop({"document": document}, None)
    assert exported["valid"], exported
    assert (
        studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)["document"]
        == document
    )
    assert (
        wait(studio, studio.start_run({"agent_id": "resource-agent", "task": "Check"}))["output"]
        == "1"
    )
    assert "FIRST SKILL CONTENT" in contexts[0].prompt
    assert contexts[0].model_override == "first-model" and contexts[0].timeout == 20
    assert providers == ["codex"]


@pytest.mark.parametrize("tool,expected", [("word_count", "completed"), ("other", "failed")])
def test_selected_agent_mcp_operations_use_real_stdio_without_permission_widening(
    fixture, tmp_path, tool, expected
):
    studio, _, _, answers = fixture
    server = tmp_path / "selected_operations.py"
    effect = tmp_path / "called.txt"
    server.write_text(
        """try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer
from pathlib import Path
server = MCPServer("Agent permissions fixture")
@server.tool()
def word_count(text: str) -> str:
    Path("""
        + repr(str(effect))
        + """).write_text("word_count")
    return str(len(text.split()))
@server.tool()
def other(text: str) -> str:
    Path("""
        + repr(str(effect))
        + """).write_text("other")
    return text
server.run(transport="stdio")
"""
    )
    params = {"type": "stdio", "command": sys.executable, "args": [str(server)]}
    studio.catalog.save(
        "connectors",
        {
            "id": "local-mcp",
            "name": "Local MCP",
            "data": {
                "type": "mcp",
                "command": sys.executable,
                "args": [str(server)],
                "enabled": True,
                "actions": ["call"],
                "tools": ["word_count", "other"],
                "read_only_tools": ["word_count", "other"],
            },
        },
    )
    value = source()
    value.pop("skills")
    value["tools"] = [
        {"tool_type": "mcp", "connection_params": params, "allow_list": ["word_count"]}
    ]
    mapping = {
        "agents": {"resource-agent": {"profile": "chosen"}},
        "connectors": {"mcp-" + digest(params)[:12]: "local-mcp"},
    }
    document = install(studio, value, mapping)
    assert document["definitions"][0]["document"]["spec"]["connector_tools"] == {
        "local-mcp": ["word_count"]
    }
    wrapper = next(name for name in studio.operations.tools if name.endswith(":local-mcp"))
    answers[:] = [
        json.dumps(
            {
                "tool_call": {
                    "name": wrapper,
                    "arguments": {
                        "action": "call",
                        "tool": tool,
                        "arguments": {"text": "one two three"},
                    },
                }
            }
        ),
        "3",
    ]
    result = wait(
        studio,
        studio.start_run({"agent_id": "resource-agent", "task": "Count local fixture"}),
        expected,
    )
    if expected == "completed":
        assert result["output"] == "3" and effect.read_text() == "word_count"
    else:
        assert "selected tools" in result["error"] and not effect.exists()
    assert studio.configuration.export_interop({"document": document}, None)["valid"]


def test_queue_pins_model_skill_and_toolset_instead_of_reloading_latest(fixture):
    studio, contexts, providers, _ = fixture
    install(studio)
    pending, submit = [], studio.executor.submit
    studio.executor.submit = lambda *args: pending.append(args)
    try:
        task = studio.start_run({"agent_id": "resource-agent", "task": "Queued evidence"})
        studio.catalog.save(
            "models",
            {
                "id": "chosen",
                "name": "Chosen",
                "revision": 1,
                "data": {"provider": "claude-code", "model": "second-model"},
            },
        )
        studio.catalog.save(
            "skills",
            {
                "id": "evidence",
                "name": "Evidence",
                "revision": 1,
                "data": {"content": "SECOND SKILL CONTENT"},
            },
        )
        studio.catalog.save(
            "toolsets",
            {
                "id": "local-tools",
                "name": "Local tools",
                "revision": 1,
                "data": {"tools": ["text:grep"]},
            },
        )
    finally:
        studio.executor.submit = submit
    callback, *args = pending[0]
    callback(*args)
    assert studio.run(task["id"])["status"] == "completed"
    assert providers == ["codex"] and contexts[0].model_override == "first-model"
    assert (
        "FIRST SKILL CONTENT" in contexts[0].prompt
        and "SECOND SKILL CONTENT" not in contexts[0].prompt
    )
    assert "text:wordcount" in contexts[0].prompt and "text:grep" not in contexts[0].prompt


def test_total_model_calls_limit_does_not_allow_one_extra_mutating_attempt(fixture):
    studio, contexts, _, answers = fixture
    install(studio)
    answers[:] = [
        json.dumps({"tool_call": {"name": "text:wordcount", "arguments": {"text": "one two"}}})
    ]
    task = wait(
        studio, studio.start_run({"agent_id": "resource-agent", "task": "Bounded"}), "failed"
    )
    assert len(contexts) == 2
    traces = [
        item for item in studio.records("tool-traces", limit=None) if item["run_id"] == task["id"]
    ]
    assert len(traces) == 1 and traces[0]["ok"]


@pytest.mark.parametrize("output", ["not JSON", '"wrong type"'])
def test_agent_output_schema_failure_is_visible_without_generation_replay(fixture, output):
    studio, contexts, _, answers = fixture
    install(studio)
    answers[:] = [output]
    result = wait(
        studio, studio.start_run({"agent_id": "resource-agent", "task": "Validate"}), "failed"
    )
    assert len(contexts) == 1 and result["error"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_timeout_seconds", 0),
        ("max_llm_calls_per_task", 0),
        ("max_llm_calls_per_task", 101),
        ("skills", [{"path": "/private/secret-never-read"}]),
        ("output_schema", {"type": "unknown"}),
    ],
)
def test_invalid_settings_and_unbound_files_reject_before_writes(fixture, field, value):
    studio, contexts, _, _ = fixture
    data = source()
    data[field] = value
    converted = studio.configuration.convert_interop(
        {"documents": [data], "bindings": bindings()}, None
    )
    assert not converted["valid"] and converted["document"] is None
    assert not studio.definitions("agents") and not contexts


def test_declarative_additional_configuration_references_round_trip(fixture):
    studio, _, _, _ = fixture
    raw = source()
    value = {
        "kind": "agent",
        "name": "resource-agent",
        "spec": {
            "systemPrompt": raw["instruction"],
            "modelProvider": ["profile"],
            "additionalConfigurations": {
                k: raw[k]
                for k in (
                    "skills",
                    "tools",
                    "request_timeout_seconds",
                    "max_llm_calls_per_task",
                    "output_schema",
                )
            },
        },
    }
    mapping = copy.deepcopy(bindings())
    mapping["models"] = {"profile": {"profile": "chosen"}}
    native = install(studio, value, mapping)
    exported = studio.configuration.export_interop({"document": native}, None)
    assert exported["valid"], exported
    assert (
        studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)["document"]
        == native
    )


def test_agent_max_tool_calls_zero_allowed_and_enforced(fixture):
    from nexus.core.studio_agent_settings import call_limit, validate_settings

    studio, _, _, _ = fixture
    assert validate_settings(studio, {"max_tool_calls": 0})["max_tool_calls"] == 0
    assert validate_settings(studio, {"max_tool_calls": None}) == {}
    assert validate_settings(studio, {"max_model_calls": None}) == {}
    assert call_limit({"max_tool_calls": 0}) == 0
    assert call_limit({"max_tool_calls": 0, "max_model_calls": 10}) == 0
    assert call_limit({"max_tool_calls": 3, "max_model_calls": 10}) == 3

    with pytest.raises(ValueError, match="Agent tool call limit must be between zero and 100"):
        validate_settings(studio, {"max_tool_calls": -1})
    with pytest.raises(ValueError, match="Agent tool call limit must be between zero and 100"):
        validate_settings(studio, {"max_tool_calls": 101})
    with pytest.raises(ValueError, match="Agent tool call limit must be between zero and 100"):
        validate_settings(studio, {"max_tool_calls": "0"})
