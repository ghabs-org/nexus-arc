"""External retry/exit behavior on the shared runtime, with no provider accounts."""

import asyncio
import copy
import json

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_workflow_retry import retry_delay, validate_retry
from nexus.tools import tool_registry
from tests.test_studio_interop_workflows import apply, run


def source(policy="OnError", limit=2):
    return {
        "agent_name": "policy-run",
        "agent_type": "workflow",
        "retry_strategy": {
            "limit": limit,
            "retry_policy": policy,
            "backoff": {"duration": "1ms", "factor": 2, "max_duration": "2ms"},
        },
        "nodes": [{"id": "work", "type": "agent", "agent_name": "writer"}],
        "output_mapping": {"value": "{{work.output}}"},
    }


def agent():
    return {"agent_name": "writer", "instruction": "Return a JSON integer."}


def convert(studio, workflow, provider="openai"):
    return studio.configuration.convert_interop(
        {
            "documents": [agent(), workflow],
            "bindings": {"agents": {"writer": {"provider": provider}}},
        },
        None,
    )


def install(studio, workflow):
    report = convert(studio, workflow)
    assert report["valid"], report
    plan = studio.configuration.plan(report["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    return report["document"]


@pytest.mark.parametrize(
    "policy,failures,expected_calls,status",
    [
        ("OnFailure", ["reported"], 2, "completed"),
        ("OnFailure", ["error"], 1, "failed"),
        ("OnError", ["error", "reported"], 3, "completed"),
        ("OnError", [], 1, "completed"),
        ("Always", [], 3, "completed"),
        ("Always", [None, None, "error"], 3, "failed"),
        ("OnError", ["error"] * 5, 3, "failed"),
    ],
)
def test_actual_provider_results_distinguish_failure_error_and_always(
    tmp_path, policy, failures, expected_calls, status
):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            index = len(calls)
            calls.append(context)
            behavior = failures[index] if index < len(failures) else None
            if behavior == "error":
                raise OSError("Synthetic provider outage")
            return AgentResult(
                success=behavior != "reported",
                output=str(len(calls)),
                error="Synthetic reported failure" if behavior == "reported" else None,
            )

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        native = install(studio, source(policy))
        result = run(studio, "policy-run", {}, status)
        assert len(calls) == expected_calls
        if status == "completed":
            assert json.loads(result["output"]) == {"value": expected_calls}
        saved = studio._read(studio._path("graph-runs", result["id"]))["retry_attempts"][
            "root/work"
        ]
        assert saved["next"] == expected_calls
        assert saved["terminal"] == status
        exported = studio.configuration.export_interop({"document": native}, None)
        assert exported["valid"], exported
        assert (
            studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)["document"]
            == native
        )
    finally:
        studio.close()


@pytest.mark.parametrize("provider", ["codex", "claude-code"])
def test_retry_never_enables_coding_cli_replay(tmp_path, provider):
    studio = StudioService(tmp_path)
    try:
        report = convert(studio, source(), provider)
        assert not report["valid"] and report["document"] is None
        assert not studio.definitions("agents")
    finally:
        studio.close()


def test_workflow_default_with_explicit_node_disable_and_tool_retry_rejected(tmp_path):
    studio = StudioService(tmp_path)
    try:
        workflow = source()
        workflow["tools"] = [{"tool_type": "builtin", "tool_name": "words"}]
        workflow["nodes"] = [
            {"id": "work", "type": "tool", "tool_name": "words", "input": {"text": "one two"}}
        ]
        request = {"documents": [workflow], "bindings": {"tools": {"words": "text:wordcount"}}}
        report = studio.configuration.convert_interop(request, None)
        assert not report["valid"] and report["document"] is None
        workflow["nodes"][0]["retry_strategy"] = {"limit": 0}
        apply(studio, workflow, {"words": "text:wordcount"})
        assert json.loads(run(studio, "policy-run", {})["output"]) == {"value": 2}
    finally:
        studio.close()


@pytest.mark.parametrize(
    "change",
    [
        {"limit": 4},
        {"limit": True},
        {"retry_policy": "unknown"},
        {"backoff": {"duration": "-1s"}},
        {"backoff": {"duration": "1h"}},
        {"backoff": {"factor": 100}},
        {"backoff": {"duration": "3s", "max_duration": "2s"}},
        {"backoff": {"secret": "synthetic-hidden-value"}},
        None,
    ],
)
def test_invalid_policies_fail_whole_preflight(tmp_path, change):
    studio = StudioService(tmp_path)
    try:
        workflow = source()
        workflow["retry_strategy"] = change
        report = convert(studio, workflow)
        assert not report["valid"] and report["document"] is None
        assert "synthetic-hidden-value" not in json.dumps(report)
        assert not studio.records("configuration-plans")
    finally:
        studio.close()


def test_backoff_math_and_native_shape_validation():
    policy = {
        "limit": 3,
        "policy": "OnError",
        "backoff": {"seconds": 0.5, "factor": 3, "max_seconds": 2},
    }
    assert validate_retry(policy) == policy
    assert [retry_delay(policy, n) for n in range(3)] == [0.5, 1.5, 2]
    for value in [float("nan"), float("inf"), True, -1]:
        invalid = copy.deepcopy(policy)
        invalid["backoff"]["seconds"] = value
        with pytest.raises(ValueError):
            validate_retry(invalid)


def exit_source():
    return {
        "agent_name": "exit-run",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "record"}],
        "on_exit": {"always": "cleanup", "on_success": "notify", "on_failure": "alert"},
        "nodes": [
            {"id": "work", "type": "tool", "tool_name": "record", "input": {"label": "work"}},
            {
                "id": "cleanup",
                "type": "tool",
                "tool_name": "record",
                "input": {
                    "label": "cleanup",
                    "status": "{{workflow.status}}",
                    "original": "{{workflow.input}}",
                    "body": "{{work.output}}",
                },
            },
            {
                "id": "notify",
                "type": "tool",
                "tool_name": "record",
                "input": {
                    "label": "notify",
                    "status": "{{workflow.status}}",
                    "cleanup": "{{cleanup.output}}",
                },
            },
            {
                "id": "alert",
                "type": "tool",
                "tool_name": "record",
                "input": {
                    "label": "alert",
                    "status": "{{workflow.status}}",
                    "error": "{{workflow.error.message}}",
                },
            },
        ],
        "output_mapping": {"body": "{{work.output}}", "exit": "{{notify.output}}"},
    }


@pytest.mark.parametrize(
    "fail_at,expected,status",
    [
        (None, ["work", "cleanup", "notify"], "completed"),
        ("work", ["work", "cleanup", "alert"], "failed"),
        ("cleanup", ["work", "cleanup", "notify"], "completed"),
    ],
)
def test_exit_order_capture_nonfatal_failure_and_source_export(tmp_path, fail_at, expected, status):
    calls = []

    async def record(label, **kwargs):
        calls.append((label, kwargs))
        if label == fail_at:
            raise ValueError("Synthetic failure")
        return label

    tool_registry.register("interop:exit-record", record, "Synthetic exit recorder")
    studio = StudioService(tmp_path)
    try:
        native = apply(studio, exit_source(), {"record": "interop:exit-record"})
        result = run(studio, "exit-run", {"topic": "local"}, status)
        assert [c[0] for c in calls] == expected
        assert calls[1][1]["status"] == ("failure" if fail_at == "work" else "success")
        assert calls[1][1]["original"] == {"topic": "local"}
        if status == "completed":
            assert json.loads(result["output"]) == {"body": "work", "exit": "notify"}
        if fail_at == "work":
            assert calls[-1][1]["error"]
        if fail_at == "cleanup":
            assert result["workflow_warnings"]
        exported = studio.configuration.export_interop({"document": native}, None)
        assert exported["valid"], exported
        assert (
            studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)["document"]
            == native
        )
    finally:
        studio.close()
        tool_registry.unregister("interop:exit-record", func=record)


@pytest.mark.parametrize(
    "change",
    [
        {"on_exit": {"on_abort": "cleanup"}},
        {"on_exit": "missing"},
        {"on_exit": None},
    ],
)
def test_bad_or_unsupported_exit_contract_rejected_before_writes(tmp_path, change):
    studio = StudioService(tmp_path)
    try:
        workflow = exit_source()
        workflow.update(change)
        report = studio.configuration.convert_interop(
            {"documents": [workflow], "bindings": {"tools": {"record": "text:wordcount"}}}, None
        )
        assert not report["valid"] and report["document"] is None
        assert not studio.definitions("workflows")
    finally:
        studio.close()


@pytest.mark.parametrize("succeeds", [True, False])
def test_exit_approval_recovers_pinned_child_without_repeating_body(tmp_path, succeeds):
    from tests.test_studio_workflow_v2 import wait

    calls = []

    async def effect(label):
        calls.append(label)
        if label == "work" and not succeeds:
            raise ValueError("Synthetic body failure")
        return label

    tool_registry.register("interop:exit-recovery", effect, "Synthetic approved exit")
    studio = StudioService(tmp_path)
    try:
        studio.save_definition(
            "workflows",
            {
                "name": "Exit approval",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {"id": "gate", "kind": "approval"},
                        {
                            "id": "effect",
                            "kind": "tool",
                            "tool_id": "interop:exit-recovery",
                            "kwargs": {"label": "exit"},
                            "depends_on": ["gate"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.effect"},
                },
            },
        )
        workflow = {
            "agent_name": "recover-exit",
            "agent_type": "workflow",
            "tools": [{"tool_type": "builtin", "tool_name": "record"}],
            "on_exit": "cleanup",
            "nodes": [
                {"id": "work", "type": "tool", "tool_name": "record", "input": {"label": "work"}},
                {
                    "id": "cleanup",
                    "type": "workflow",
                    "workflow_name": "Exit approval",
                    "retry_strategy": {"limit": 0},
                },
            ],
            "output_mapping": {"result": "{{work.output}}", "cleanup": "{{cleanup.output}}"},
        }
        apply(studio, workflow, {"record": "interop:exit-recovery"})
        paused = run(studio, "recover-exit", {}, "waiting")
        assert calls == ["work"]
        studio.close()
        (tmp_path / "workflows" / "exit-approval.yaml").unlink()
        studio = StudioService(tmp_path)
        result = wait(
            studio,
            studio.graph.resume(paused["id"], "approve"),
            "completed" if succeeds else "failed",
        )
        assert calls == ["work", "exit"]
        assert not result.get("workflow_warnings")
        if succeeds:
            assert json.loads(result["output"]) == {"result": "work", "cleanup": "exit"}
    finally:
        studio.close()
        tool_registry.unregister("interop:exit-recovery", func=effect)


@pytest.mark.parametrize(
    "boundary", ["consumed", "backoff", "terminal", "exhausted", "terminal_tool"]
)
def test_abrupt_exit_keeps_retry_budget_and_completed_generation(tmp_path, boundary):
    import subprocess
    import sys

    studio = StudioService(tmp_path)
    if boundary == "terminal_tool":
        studio.save_definition(
            "workflows",
            {
                "name": "policy-run",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {
                            "id": "work",
                            "kind": "tool",
                            "tool_id": "text:wordcount",
                            "kwargs": {"text": "one two three four five six seven"},
                            "retry_policy": {
                                "limit": 0,
                                "policy": "OnFailure",
                                "backoff": {"seconds": 0, "factor": 1, "max_seconds": 0},
                            },
                        }
                    ],
                    "output_mapping": {"value": {"ref": "nodes.work"}},
                },
            },
        )
    else:
        install(studio, source(limit=0 if boundary == "exhausted" else 2))
    studio.close()
    program = r"""
import os, sys, time
from pathlib import Path
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
root, boundary = Path(sys.argv[1]), sys.argv[2]
calls = root / "provider-calls"
class Provider:
 async def check_availability(self): return True
 async def execute_agent(self, context):
  calls.write_text(calls.read_text() + "call\n" if calls.exists() else "call\n")
  if boundary == "backoff": raise OSError("Synthetic interruption")
  return AgentResult(success=True, output="7")
s = StudioService(root, provider_factory=lambda _: Provider())
original = s._write
def write(path, record):
 original(path, record)
 if path.parent.name != "graph-runs": return
 saved = record.get("retry_attempts", {}).get("root/work", {})
 if (boundary in {"consumed", "exhausted"} and saved.get("next") == 1
     or boundary == "backoff" and saved.get("not_before", 0) > 0
     or boundary in {"terminal", "terminal_tool"} and saved.get("terminal") == "completed"):
  os._exit(73)
s._write = write
s.start_run({"kind":"workflow", "workflow_id":"policy-run", "task":"Synthetic retry crash", "workflow_input":{}})
time.sleep(10)
raise RuntimeError("Crash boundary not reached")
"""
    process = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), boundary],
        timeout=15,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 73, process.stderr
    recovered_calls = []

    class RecoveredProvider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            recovered_calls.append(True)
            return AgentResult(success=True, output="8")

    studio = StudioService(tmp_path, provider_factory=lambda _: RecoveredProvider())
    try:
        record = studio.records("graph-runs")[0]
        saved = record["retry_attempts"]["root/work"]
        assert saved["next"] == 1
        if boundary == "exhausted":
            with pytest.raises(ValueError, match="budget exhausted"):
                asyncio.run(studio.graph.structured.execute(record["id"], record["payload"]))
            assert recovered_calls == []
            return
        output = asyncio.run(studio.graph.structured.execute(record["id"], record["payload"]))
        assert json.loads(output) == {
            "value": 7 if boundary in {"terminal", "terminal_tool"} else 8
        }
        assert len(recovered_calls) == (0 if boundary in {"terminal", "terminal_tool"} else 1)
        after = studio._read(studio._path("graph-runs", record["id"]))["retry_attempts"][
            "root/work"
        ]
        assert after["next"] == (1 if boundary in {"terminal", "terminal_tool"} else 2)
    finally:
        studio.close()


def test_on_error_retries_actual_timeout_but_cancellation_never_retries(tmp_path):
    import time

    from tests.test_studio_workflow_v2 import wait

    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(True)
            if len(calls) == 1:
                await asyncio.sleep(2)
            return AgentResult(success=True, output="9")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        workflow = source(limit=1)
        workflow["nodes"][0]["timeout"] = "1s"
        install(studio, workflow)
        assert json.loads(run(studio, "policy-run", {})["output"]) == {"value": 9}
        assert len(calls) == 2
        calls.clear()
        task = studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": "policy-run",
                "task": "Cancel generation",
                "workflow_input": {},
            }
        )
        deadline = time.monotonic() + 3
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls
        wait(studio, studio.cancel_run(task["id"]), "cancelled")
        assert len(calls) == 1
    finally:
        studio.close()


def test_tool_capable_api_retry_is_rejected_and_input_validation_does_not_mutate(tmp_path):
    studio = StudioService(tmp_path)
    try:
        request = {
            "documents": [
                {**agent(), "tools": [{"tool_type": "builtin", "tool_name": "words"}]},
                source(),
            ],
            "bindings": {
                "agents": {"writer": {"provider": "openai"}},
                "tools": {"words": "text:wordcount"},
            },
        }
        before = copy.deepcopy(request)
        report = studio.configuration.convert_interop(request, None)
        assert not report["valid"] and report["document"] is None
        assert request == before
        assert not studio.records("runs")
    finally:
        studio.close()


def test_handler_cannot_be_regular_dependency_or_capture_unknown_node(tmp_path):
    studio = StudioService(tmp_path)
    try:
        workflow = exit_source()
        workflow["nodes"][0]["depends_on"] = ["cleanup"]
        report = studio.configuration.convert_interop(
            {"documents": [workflow], "bindings": {"tools": {"record": "text:wordcount"}}}, None
        )
        assert not report["valid"] and report["document"] is None
        workflow["nodes"][0].pop("depends_on")
        report = studio.configuration.convert_interop(
            {"documents": [workflow], "bindings": {"tools": {"record": "text:wordcount"}}}, None
        )
        assert report["valid"], report
        graph = report["document"]["definitions"][0]["document"]["graph"]
        before = copy.deepcopy(graph)
        studio.graph.validate(graph)
        assert graph == before
        graph["interop_exit_handlers"][0]["body"]["nodes"][0]["kwargs"] = {"ref": "interop_nodes.missing"}
        with pytest.raises(ValueError, match="capture"):
            studio.graph.validate(graph)
    finally:
        studio.close()


@pytest.mark.parametrize("legacy", [True, False])
def test_provider_override_cannot_turn_safe_generation_retry_into_cli_replay(tmp_path, legacy):
    from tests.test_studio_workflow_v2 import wait

    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(True)
            return AgentResult(success=True, output="1")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        install(studio, source())
        if legacy:
            definition = studio.definition("workflows", "policy-run")
            node = definition["graph"]["nodes"][0]
            node.pop("retry_policy")
            node["retries"] = 1
            current = next(
                item for item in studio.definitions("workflows") if item["id"] == "policy-run"
            )
            studio.update_definition(
                "workflows",
                "policy-run",
                {
                    "name": "policy-run",
                    "graph": definition["graph"],
                    "revision": current["revision"],
                },
            )
        result = wait(
            studio,
            studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": "policy-run",
                    "task": "Unsafe override",
                    "provider": "codex",
                }
            ),
            "failed",
        )
        assert calls == []
        assert "cannot be automatically retried" in result["error"]
    finally:
        studio.close()
