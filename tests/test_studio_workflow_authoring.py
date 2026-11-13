"""Structured authoring bounds, mapping preflight and effect-safe recovery."""

import asyncio
import json
import sys

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_workflow_policy import DEFAULT_LIMITS, HARD_LIMITS, workflow_limits
from nexus.tools import tool_registry
from tests.test_studio_workflow_v2 import save, wait


def identity():
    return {
        "version": 2,
        "nodes": [
            {
                "id": "result",
                "kind": "switch",
                "cases": [
                    {
                        "condition": {"left": True, "op": "eq", "right": True},
                        "value": {"ref": "input"},
                    }
                ],
            }
        ],
        "output_mapping": {"ref": "nodes.result"},
    }


def app(tmp_path, **kwargs):
    return StudioService(tmp_path, **kwargs)


@pytest.mark.parametrize("key", DEFAULT_LIMITS)
def test_administrator_bounds_validate_defaults_and_hard_ceilings(key):
    assert workflow_limits()[key] == DEFAULT_LIMITS[key]
    assert workflow_limits({key: HARD_LIMITS[key]})[key] == HARD_LIMITS[key]
    for invalid in [0, True, 1.5, HARD_LIMITS[key] + 1]:
        with pytest.raises(ValueError):
            workflow_limits({key: invalid})


def test_unknown_administrator_policy_is_rejected_before_state_writes(tmp_path):
    with pytest.raises(ValueError, match="Unknown"):
        app(tmp_path, workflow_limits={"unbounded": True})
    assert not (tmp_path / ".nexus").exists()


@pytest.mark.parametrize("concurrency", [4, 6])
def test_map_pool_obeys_concurrency_and_keeps_item_order(tmp_path, concurrency):
    active, peak = 0, 0

    async def measure(value: int):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.03 * (3 - value % 3))
            return value
        finally:
            active -= 1

    tool_registry.register("acceptance:bounded", measure, "Synthetic bounded work")
    studio = app(tmp_path, workflow_limits={"map_concurrency": concurrency})
    try:
        body = {
            "version": 2,
            "nodes": [
                {
                    "id": "work",
                    "kind": "tool",
                    "tool_id": "acceptance:bounded",
                    "kwargs": {"value": {"ref": "input"}},
                }
            ],
            "output_mapping": {"ref": "nodes.work"},
        }
        graph = {
            "nodes": [
                {
                    "id": "each",
                    "kind": "map",
                    "items": {"ref": "input"},
                    "concurrency": concurrency,
                    "body": body,
                }
            ],
            "output_mapping": {"ref": "nodes.each"},
        }
        workflow = save(studio, "Ordered work", graph)
        result = wait(
            studio,
            studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": workflow["id"],
                    "task": "Measure",
                    "workflow_input": list(range(11)),
                }
            ),
        )
        assert json.loads(result["output"]) == list(range(11))
        assert peak == concurrency
    finally:
        studio.close()
        tool_registry.unregister("acceptance:bounded", func=measure)


def test_queued_limits_survive_new_worker_configuration_and_client_cannot_override(tmp_path):
    options = {
        "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
        "namespace": "workflow-pins",
        "workers": 0,
    }
    root = tmp_path / "project"
    controller = app(root, state_options=options, workflow_limits={"map_items": 101})
    workflow = save(
        controller,
        "Pinned bounds",
        {
            "nodes": [
                {
                    "id": "each",
                    "kind": "map",
                    "max_items": 101,
                    "items": {"ref": "input"},
                    "body": identity(),
                }
            ],
            "output_mapping": {"ref": "nodes.each"},
        },
    )
    run = controller.start_run(
        {
            "kind": "workflow",
            "workflow_id": workflow["id"],
            "task": "Map",
            "workflow_input": list(range(101)),
            "_workflow_limits": {"map_items": 99999},
        }
    )
    controller.close()
    worker = app(root, state_options={**options, "workers": 1}, workflow_limits={"map_items": 2})
    try:
        result = wait(worker, run)
        assert json.loads(result["output"]) == list(range(101))
        state = worker._read(worker._path("graph-runs", run["id"]))
        assert state["limits"]["map_items"] == 101
    finally:
        worker.close()


@pytest.mark.parametrize("depth,valid", [(9, True), (10, False)])
def test_inline_nesting_exact_default_boundary(tmp_path, depth, valid):
    studio = app(tmp_path)
    graph = identity()
    for _ in range(depth):
        graph = {"version": 2, "nodes": [{"id": "each", "kind": "map", "items": [], "body": graph}]}
    try:
        if valid:
            save(studio, "Nested", graph)
        else:
            with pytest.raises(ValueError, match="nesting"):
                save(studio, "Nested", graph)
    finally:
        studio.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"ref": "unknown.value"},
        {"ref": "nodes.missing"},
        {"ref": "nodes.other"},
        {"ref": "nodes"},
        {"ref": "input..field"},
    ],
)
def test_invalid_node_references_fail_during_save_before_effects(tmp_path, bad):
    studio = app(tmp_path)
    graph = {
        "nodes": [
            {"id": "one", "kind": "tool", "tool_id": "text:wordcount", "kwargs": {"text": bad}},
            {"id": "other", "kind": "delay", "delay_seconds": 0},
        ]
    }
    try:
        with pytest.raises(ValueError, match="reference|inputs"):
            save(studio, "Invalid refs", graph)
        assert studio.definitions("workflows") == [] and studio.records("runs") == []
    finally:
        studio.close()


@pytest.mark.parametrize(
    "schema", [{"type": "not-a-type"}, {"$ref": "https://unselected.invalid/schema"}, []]
)
def test_schema_syntax_and_external_references_rejected_before_save(tmp_path, schema):
    studio = app(tmp_path)
    graph = identity()
    graph["output_schema"] = schema
    try:
        with pytest.raises(ValueError, match="[Ss]chema"):
            save(studio, "Invalid schema", graph)
        assert studio.definitions("workflows") == []
    finally:
        studio.close()


def test_invalid_coordinates_and_conditions_do_not_reach_canvas(tmp_path):
    studio = app(tmp_path)
    try:
        graph = identity()
        graph["nodes"][0]["position"] = {"x": '0)" onload="bad', "y": 0}
        with pytest.raises(ValueError, match="coordinates"):
            save(studio, "Invalid position", graph)
        graph = identity()
        graph["nodes"][0]["cases"][0]["condition"]["op"] = []
        with pytest.raises(ValueError, match="operator"):
            save(studio, "Invalid condition", graph)
    finally:
        studio.close()


def test_tool_argument_type_failure_does_not_invoke_tool(tmp_path):
    calls = []

    async def effect(**kwargs):
        calls.append(kwargs)
        return "effect"

    tool_registry.register("acceptance:typed-effect", effect, "Synthetic effect")
    studio = app(tmp_path)
    try:
        workflow = save(
            studio,
            "Typed arguments",
            {
                "nodes": [
                    {
                        "id": "effect",
                        "kind": "tool",
                        "tool_id": "acceptance:typed-effect",
                        "kwargs": {"ref": "input"},
                    }
                ]
            },
        )
        result = wait(
            studio,
            studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": workflow["id"],
                    "task": "Check",
                    "workflow_input": [],
                }
            ),
            "failed",
        )
        assert "Resolved tool" in result["error"] and calls == []
    finally:
        studio.close()
        tool_registry.unregister("acceptance:typed-effect", func=effect)


@pytest.mark.parametrize("succeeds", [False, True])
@pytest.mark.parametrize("scope", ["root", "map", "workflow"])
def test_handler_approval_resume_after_restart_never_reexecutes_body(tmp_path, succeeds, scope):
    calls = []

    async def body():
        calls.append(True)
        if not succeeds:
            raise ValueError("Synthetic body failure")
        return 7

    tool_registry.register("acceptance:handler-body", body, "Synthetic handler checkpoint")
    studio = app(tmp_path)
    graph = {
        "nodes": [{"id": "body", "kind": "tool", "tool_id": "acceptance:handler-body"}],
        "on_success" if succeeds else "on_error": {
            "version": 2,
            "nodes": [{"id": "gate", "kind": "approval"}],
        },
    }
    if scope == "map":
        graph = {
            "nodes": [{"id": "each", "kind": "map", "items": [0], "body": {"version": 2, **graph}}]
        }
    elif scope == "workflow":
        child = save(studio, "Child handler", graph)
        graph = {"nodes": [{"id": "child", "kind": "workflow", "workflow_id": child["id"]}]}
    workflow = save(studio, "Recover handler", graph)
    run = wait(
        studio,
        studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Handle"}),
        "waiting",
    )
    assert len(calls) == 1
    studio.close()
    studio = app(tmp_path)
    try:
        resumed = wait(
            studio, studio.graph.resume(run["id"], "approve"), "completed" if succeeds else "failed"
        )
        assert len(calls) == 1
        if not succeeds:
            assert "Synthetic body failure" in resumed["error"]
    finally:
        studio.close()
        tool_registry.unregister("acceptance:handler-body", func=body)


def test_tool_capable_api_agents_cannot_be_retried(tmp_path):
    studio = app(tmp_path)
    studio.save_definition(
        "agents",
        {
            "name": "Tool agent",
            "provider": "openai",
            "instructions": "Review",
            "tool_ids": ["text:wordcount"],
        },
    )
    try:
        with pytest.raises(ValueError, match="without tools"):
            save(
                studio,
                "Unsafe retry",
                {
                    "nodes": [
                        {"id": "agent", "kind": "agent", "agent_id": "tool-agent", "retries": 1}
                    ]
                },
            )
    finally:
        studio.close()


def test_loop_default_iteration_and_executed_instance_boundaries(tmp_path):
    studio = app(tmp_path, workflow_limits={"node_instances": 101})
    body = identity()
    body["nodes"][0]["cases"][0]["value"] = {"ref": "iteration"}
    graph = {
        "nodes": [
            {
                "id": "repeat",
                "kind": "loop",
                "body": body,
                "condition": {"left": {"ref": "iteration"}, "op": "lt", "right": 99},
            }
        ],
        "output_mapping": {"ref": "nodes.repeat"},
    }
    try:
        workflow = save(studio, "Exact loop", graph)
        run = wait(
            studio,
            studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Loop"}),
        )
        assert json.loads(run["output"]) == 99
        state = studio._read(studio._path("graph-runs", run["id"]))
        assert len(state["started"]) == 101
        with pytest.raises(ValueError, match="Loop limit"):
            graph["nodes"][0]["max_iterations"] = 101
            save(studio, "Too many loops", graph)
    finally:
        studio.close()


def test_every_structured_node_kind_in_one_workflow_with_real_local_mcp(tmp_path):
    server = tmp_path / "mcp_server.py"
    server.write_text("""try:
 from mcp.server.mcpserver import MCPServer
except ImportError:
 from mcp.server.fastmcp import FastMCP as MCPServer
server=MCPServer("Local workflow fixture")
@server.tool()
def echo(text:str)->str: return text
server.run(transport="stdio")
""")

    class Model:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=True, output="Reviewed")

    studio = app(
        tmp_path / "workspace",
        provider_factory=lambda _: Model(),
        workbench_options={
            "decision_client": lambda *a, **k: {"route": {"choice": "go", "confidence": 0.9}}
        },
    )
    studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    studio.catalog.save(
        "connectors",
        {
            "name": "Local MCP",
            "data": {
                "type": "mcp",
                "command": sys.executable,
                "args": [str(server)],
                "enabled": True,
                "actions": ["discover"],
                "tools": ["echo"],
                "read_only_tools": ["echo"],
            },
        },
    )
    child = save(studio, "Child", identity())
    count = {
        "version": 2,
        "nodes": [
            {
                "id": "count",
                "kind": "tool",
                "tool_id": "text:wordcount",
                "kwargs": {"text": {"ref": "input"}},
            }
        ],
        "output_mapping": {"ref": "nodes.count"},
    }
    nodes = [
        {
            "id": "ask",
            "kind": "input",
            "output_schema": {"type": "array", "items": {"type": "string"}},
        },
        {
            "id": "each",
            "kind": "map",
            "items": {"ref": "nodes.ask"},
            "depends_on": ["ask"],
            "body": count,
        },
        {
            "id": "repeat",
            "kind": "loop",
            "input": {"ref": "nodes.each"},
            "depends_on": ["each"],
            "max_iterations": 2,
            "condition": {"left": {"ref": "iteration"}, "op": "lt", "right": 1},
            "body": identity(),
        },
        {
            "id": "child",
            "kind": "workflow",
            "workflow_id": child["id"],
            "input": {"ref": "nodes.repeat"},
            "depends_on": ["repeat"],
        },
        {
            "id": "choose",
            "kind": "switch",
            "depends_on": ["child"],
            "cases": [
                {
                    "condition": {"left": {"ref": "nodes.child"}, "op": "contains", "right": 1},
                    "value": {"choice": "go"},
                }
            ],
        },
        {
            "id": "decision",
            "kind": "decision",
            "depends_on": ["choose"],
            "input": {"ref": "nodes.child"},
            "decision": {
                "instructions": "Choose",
                "options": {"go": "Continue", "stop": "Stop"},
                "default": "stop",
                "threshold": 0.5,
                "backend": "openrouter",
            },
        },
        {"id": "gate", "kind": "approval", "depends_on": ["decision"]},
        {
            "id": "discover",
            "kind": "integration",
            "connector_id": "local-mcp",
            "action": "discover",
            "arguments": {},
            "depends_on": ["gate"],
        },
        {"id": "delay", "kind": "delay", "delay_seconds": 0, "depends_on": ["discover"]},
        {
            "id": "review",
            "kind": "agent",
            "agent_id": "reviewer",
            "input": {"ref": "nodes.child"},
            "depends_on": ["delay"],
        },
    ]
    try:
        workflow = save(
            studio,
            "All nodes",
            {
                "nodes": nodes,
                "output_mapping": {
                    "counts": {"ref": "nodes.each"},
                    "review": {"ref": "nodes.review"},
                    "route": {"ref": "nodes.decision.choice"},
                },
            },
        )
        run = wait(
            studio,
            studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Review"}),
            "waiting",
        )
        run = wait(
            studio,
            studio.graph.structured.resume_input(run["id"], {"root/ask": ["one two", "three"]}),
            "waiting",
        )
        assert [g["id"] for g in run["awaiting_approval"]] == ["root/gate"]
        result = wait(studio, studio.graph.resume(run["id"], "approve"))
        assert json.loads(result["output"]) == {
            "counts": [2, 1],
            "review": "Reviewed",
            "route": "go",
        }
        assert (
            len(
                [
                    k
                    for k in result["graph_nodes"]
                    if k.startswith("root/each/") and k.endswith("/count")
                ]
            )
            == 2
        )
    finally:
        studio.close()


def test_generation_retry_and_timeout_error_handler(tmp_path):
    calls = []

    class Model:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("Temporary generation failure")
            return AgentResult(success=True, output='{"ready":true}')

    studio = app(tmp_path, provider_factory=lambda _: Model())
    studio.save_definition(
        "agents", {"name": "Generator", "provider": "openai", "instructions": "Generate only"}
    )
    cancelled = []

    async def slow():
        try:
            await asyncio.sleep(3)
        finally:
            cancelled.append(True)

    tool_registry.register("acceptance:slow", slow, "Synthetic cancellable work")
    try:
        workflow = save(
            studio,
            "Generation retry",
            {
                "nodes": [
                    {
                        "id": "generate",
                        "kind": "agent",
                        "agent_id": "generator",
                        "retries": 1,
                        "output_schema": {"type": "object", "required": ["ready"]},
                    }
                ]
            },
        )
        wait(
            studio,
            studio.start_run(
                {"kind": "workflow", "workflow_id": workflow["id"], "task": "Generate"}
            ),
        )
        assert len(calls) == 2
        workflow = save(
            studio,
            "Timeout handler",
            {
                "nodes": [
                    {
                        "id": "slow",
                        "kind": "tool",
                        "tool_id": "acceptance:slow",
                        "timeout_seconds": 1,
                    }
                ],
                "on_error": {"version": 2, "nodes": [{"id": "gate", "kind": "approval"}]},
            },
        )
        run = wait(
            studio,
            studio.start_run(
                {"kind": "workflow", "workflow_id": workflow["id"], "task": "Timeout"}
            ),
            "waiting",
        )
        assert cancelled == [True]
        failed = wait(studio, studio.graph.resume(run["id"], "approve"), "failed")
        assert "TimeoutError" in failed["error"] and cancelled == [True]
    finally:
        studio.close()
        tool_registry.unregister("acceptance:slow", func=slow)


def test_boolean_false_schema_rejects_input_without_effects(tmp_path):
    studio = app(tmp_path)
    try:
        graph = identity()
        graph["input_schema"] = False
        workflow = save(studio, "Reject all input", graph)
        run = wait(
            studio,
            studio.start_run(
                {"kind": "workflow", "workflow_id": workflow["id"], "task": "Invalid"}
            ),
            "failed",
        )
        assert "schema" in run["error"] and studio.records("node-traces") == []
    finally:
        studio.close()


def test_runtime_policy_file_is_private_and_limits_endpoint_is_read_only(tmp_path):
    from nexus.core.studio_features import StudioFeatures
    from nexus.core.studio_runtime import load_runtime_configuration

    config = tmp_path / "private.json"
    config.write_text(json.dumps({"workflow_limits": {"map_items": 250, "map_concurrency": 8}}))
    config.chmod(0o600)
    root = tmp_path / "project"
    options = load_runtime_configuration(config, root)
    studio = app(root, **options)
    try:
        policy = StudioFeatures(studio).get(["workflow-policy"], {}, None)
        assert policy["effective"]["map_items"] == 250 and policy["defaults"]["map_items"] == 100
        with pytest.raises(FileNotFoundError):
            StudioFeatures(studio).post(["workflow-policy"], {"map_items": 10000}, None)
        config.chmod(0o644)
        with pytest.raises(ValueError, match="0600"):
            load_runtime_configuration(config, root)
    finally:
        studio.close()


def test_nested_mutation_lost_ack_then_error_handler_restart_never_repeats_effect(
    tmp_path, monkeypatch
):
    effects = tmp_path / "effects.txt"
    server = tmp_path / "effects_mcp.py"
    server.write_text(
        """try:
 from mcp.server.mcpserver import MCPServer
except ImportError:
 from mcp.server.fastmcp import FastMCP as MCPServer
from pathlib import Path
server=MCPServer("Synthetic local effect")
@server.tool()
def record()->str:
 with Path("""
        + repr(str(effects))
        + """).open("a") as stream: stream.write("one effect\\n")
 return "Effect recorded"
server.run(transport="stdio")
"""
    )
    root = tmp_path / "workspace"
    studio = app(root)
    studio.catalog.save(
        "connectors",
        {
            "name": "Effect MCP",
            "data": {
                "type": "mcp",
                "command": sys.executable,
                "args": [str(server)],
                "enabled": True,
                "actions": ["call"],
                "tools": ["record"],
            },
        },
    )
    child = save(
        studio,
        "Child effect",
        {
            "nodes": [
                {"id": "gate", "kind": "approval"},
                {
                    "id": "effect",
                    "kind": "integration",
                    "connector_id": "effect-mcp",
                    "action": "call",
                    "tool": "record",
                    "arguments": {},
                    "depends_on": ["gate"],
                },
            ]
        },
    )
    parent = save(
        studio,
        "Parent effect",
        {
            "nodes": [{"id": "child", "kind": "workflow", "workflow_id": child["id"]}],
            "on_error": {"version": 2, "nodes": [{"id": "review", "kind": "approval"}]},
        },
    )
    write = studio._write
    injected = []

    def lose_ack(path, record):
        if (
            "integration-effects" in str(path)
            and record.get("status") == "completed"
            and not injected
        ):
            injected.append(True)
            raise OSError("Synthetic lost effect acknowledgement")
        return write(path, record)

    monkeypatch.setattr(studio, "_write", lose_ack)
    run = wait(
        studio,
        studio.start_run({"kind": "workflow", "workflow_id": parent["id"], "task": "Record"}),
        "waiting",
    )
    assert not effects.exists()
    run = wait(studio, studio.graph.resume(run["id"], "approve"), "waiting")
    assert injected and effects.read_text().splitlines() == ["one effect"]
    assert run["awaiting_approval"][0]["id"] == "on_error/review"
    studio.close()
    studio = app(root)
    try:
        failed = wait(studio, studio.graph.resume(run["id"], "approve"), "failed")
        assert "lost effect acknowledgement" in failed["error"]
        assert effects.read_text().splitlines() == ["one effect"]
        effect = studio.records("integration-effects")[0]
        assert effect["status"] == "attempting"
        from nexus.core.studio_nodes import StudioNodeTypes

        resolved = StudioNodeTypes(studio).resolve(
            effect["id"],
            {"confirm": True, "outcome": "completed", "output": "Reviewed local counter"},
            None,
        )
        assert resolved["status"] == "completed"
        assert effects.read_text().splitlines() == ["one effect"]
    finally:
        studio.close()


def test_nested_snapshot_remains_pinned_when_source_definition_is_removed(tmp_path):
    options = {
        "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
        "namespace": "nested-pins",
        "workers": 0,
    }
    root = tmp_path / "workspace"
    studio = app(root, state_options=options)
    child = save(studio, "Pinned child", identity())
    parent = save(
        studio,
        "Pinned parent",
        {
            "nodes": [{"id": "child", "kind": "workflow", "workflow_id": child["id"]}],
            "output_mapping": {"ref": "nodes.child"},
        },
    )
    run = studio.start_run(
        {"kind": "workflow", "workflow_id": parent["id"], "task": "Original input"}
    )
    (root / "workflows" / (child["id"] + ".yaml")).unlink()
    studio.close()
    studio = app(root, state_options={**options, "workers": 1})
    try:
        result = wait(studio, run)
        assert json.loads(result["output"]) == "Original input"
    finally:
        studio.close()


def test_default_map_and_instance_caps_stop_before_additional_effects(tmp_path):
    calls = []

    async def count(value):
        calls.append(value)
        return value

    tool_registry.register("acceptance:capped", count, "Synthetic bounded effect")

    def graph():
        return {
            "nodes": [
                {
                    "id": "each",
                    "kind": "map",
                    "items": {"ref": "input"},
                    "body": {
                        "version": 2,
                        "nodes": [
                            {
                                "id": "effect",
                                "kind": "tool",
                                "tool_id": "acceptance:capped",
                                "kwargs": {"value": {"ref": "input"}},
                            }
                        ],
                    },
                }
            ]
        }

    studio = app(tmp_path / "default")
    try:
        workflow = save(studio, "Default cap", graph())
        run = wait(
            studio,
            studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": workflow["id"],
                    "task": "Cap",
                    "workflow_input": list(range(101)),
                    "_workflow_limits": {"map_items": 10000},
                }
            ),
            "failed",
        )
        assert "item limit" in run["error"] and calls == []
    finally:
        studio.close()
    studio = app(tmp_path / "limited", workflow_limits={"node_instances": 2})
    try:
        workflow = save(studio, "Instance cap", graph())
        run = wait(
            studio,
            studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": workflow["id"],
                    "task": "Cap",
                    "workflow_input": [0, 1, 2],
                }
            ),
            "failed",
        )
        assert "executed node instances" in run["error"] and calls == [0]
    finally:
        studio.close()
        tool_registry.unregister("acceptance:capped", func=count)


def test_unicode_input_keys_keep_existing_reference_compatibility(tmp_path):
    studio = app(tmp_path)
    try:
        workflow = save(
            studio,
            "Unicode reference",
            {
                "nodes": [
                    {
                        "id": "count",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": {"ref": "input.città"}},
                    }
                ],
                "output_mapping": {"ref": "nodes.count"},
            },
        )
        result = wait(
            studio,
            studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": workflow["id"],
                    "task": "Count",
                    "workflow_input": {"città": "Roma Milano"},
                }
            ),
        )
        assert json.loads(result["output"]) == 2
    finally:
        studio.close()
