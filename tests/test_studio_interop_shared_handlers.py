"""A reused exit template runs once per matching phase, with durable receipts."""

import json

import pytest

from nexus.core.studio import StudioService
from nexus.tools import tool_registry
from tests.test_studio_interop_workflows import apply, run
from tests.test_studio_workflow_v2 import wait


def source(name="shared-exits"):
    return {
        "agent_name": name,
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "record"}],
        "on_exit": dict.fromkeys(("always", "on_success", "on_failure", "on_cancel"), "cleanup"),
        "nodes": [
            {"id": "work", "type": "tool", "tool_name": "record", "input": {"label": "work"}},
            {
                "id": "cleanup",
                "type": "tool",
                "tool_name": "record",
                "input": {"label": "cleanup", "outcome": "{{workflow.status}}"},
            },
        ],
        "output_mapping": {"result": "{{cleanup.output}}"},
    }


def gate(studio, name, tool=None):
    nodes = [{"id": "gate", "kind": "approval"}]
    if tool:
        nodes.append(
            {
                "id": "effect",
                "kind": "tool",
                "tool_id": tool,
                "kwargs": {"label": "cleanup"},
                "depends_on": ["gate"],
            }
        )
    return studio.save_definition(
        "workflows",
        {
            "name": name,
            "graph": {
                "version": 2,
                "nodes": nodes,
                "output_mapping": {"ref": "nodes.effect"} if tool else {"literal": "approved"},
            },
        },
    )


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_shared_handler_matches_outcome_and_roundtrips_source(tmp_path, outcome):
    calls = []

    async def record(label, **kwargs):
        calls.append((label, kwargs.get("outcome")))
        if label == "work" and outcome == "failure":
            raise ValueError("Synthetic work failure")
        return str(len([c for c in calls if c[0] == "cleanup"]))

    tool_registry.register("interop:shared-handler", record, "Local shared handler fixture")
    studio = StudioService(tmp_path)
    try:
        value = source()
        if outcome == "cancelled":
            gate(studio, "Wait")
            value["nodes"][0] = {"id": "work", "type": "workflow", "workflow_name": "Wait"}
        native = apply(studio, value, {"record": "interop:shared-handler"})
        if outcome == "cancelled":
            paused = run(studio, value["agent_name"], {}, "waiting")
            result = wait(studio, studio.cancel_run(paused["id"]), "cancelled")
        else:
            result = run(
                studio, value["agent_name"], {}, "completed" if outcome == "success" else "failed"
            )
        assert [call for call in calls if call[0] == "cleanup"] == [("cleanup", outcome)] * 2
        if outcome == "success":
            assert json.loads(result["output"]) == {"result": "2"}
        saved = studio._read(studio._path("graph-runs", result["id"]))
        expected_phase = {
            "success": "on_success",
            "failure": "on_failure",
            "cancelled": "on_cancel",
        }[outcome]
        for phase in ("always", expected_phase):
            assert saved["nodes"][f"root/interop_exit/cleanup/{phase}/cleanup"] == "completed"
        exported = studio.configuration.export_interop({"document": native}, None)
        assert exported["valid"], exported
        assert (
            exported["bundle"]["documents"][0]["spec"]["appConfig"]["on_exit"] == value["on_exit"]
        )
        again = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
        assert again["valid"] and again["document"] == native
    finally:
        studio.close()
        tool_registry.unregister("interop:shared-handler", func=record)


@pytest.mark.parametrize("fail_at", [1, 2])
def test_shared_handler_failure_does_not_skip_later_phase(tmp_path, fail_at):
    calls = []

    async def record(label, **kwargs):
        if label == "cleanup":
            calls.append(label)
            if len(calls) == fail_at:
                raise ValueError("Synthetic cleanup phase failure")
        return "phase " + str(len(calls)) if label == "cleanup" else "body"

    tool_registry.register("interop:shared-failure", record, "Local nonfatal cleanup fixture")
    studio = StudioService(tmp_path)
    try:
        apply(studio, source(), {"record": "interop:shared-failure"})
        result = run(studio, "shared-exits", {})
        assert len(calls) == 2
        assert json.loads(result["output"]) == {"result": "phase " + str(2 if fail_at == 1 else 1)}
        assert result["workflow_warnings"][0]["node_id"].endswith(
            "/cleanup/" + ("always" if fail_at == 1 else "on_success")
        )
    finally:
        studio.close()
        tool_registry.unregister("interop:shared-failure", func=record)


@pytest.mark.parametrize("backend", ["files", "sqlite"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_recovery_keeps_completed_phase_and_original_shared_child(tmp_path, backend, cancelled):
    calls = []

    async def record(label, **kwargs):
        calls.append(label)
        return str(calls.count("cleanup"))

    tool_registry.register("interop:shared-recovery", record, "Local approved shared cleanup")
    options = (
        None
        if backend == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
            "namespace": "shared-exit",
            "workers": 1,
        }
    )
    studio = StudioService(tmp_path / "workspace", state_options=options)
    try:
        gate(studio, "Cleanup gate", "interop:shared-recovery")
        value = source()
        value["nodes"][1] = {"id": "cleanup", "type": "workflow", "workflow_name": "Cleanup gate"}
        if cancelled:
            gate(studio, "Wait")
            value["nodes"][0] = {"id": "work", "type": "workflow", "workflow_name": "Wait"}
        apply(studio, value, {"record": "interop:shared-recovery"})
        paused = run(studio, value["agent_name"], {}, "waiting")
        if cancelled:
            paused = wait(studio, studio.cancel_run(paused["id"]), "waiting")
        second_gate = wait(studio, studio.graph.resume(paused["id"], "approve"), "waiting")
        assert calls.count("cleanup") == 1
        studio.close()
        # Recovery must use queued child snapshots, not reload its deleted source.
        (tmp_path / "workspace/workflows/cleanup-gate.yaml").unlink()
        studio = StudioService(tmp_path / "workspace", state_options=options)
        result = wait(
            studio,
            studio.graph.resume(second_gate["id"], "approve"),
            "cancelled" if cancelled else "completed",
        )
        assert calls.count("cleanup") == 2
        assert calls.count("work") == (0 if cancelled else 1)
        saved = studio._read(studio._path("graph-runs", result["id"]))
        scopes = [
            key
            for key, status in saved["nodes"].items()
            if key.endswith("/effect") and status == "completed"
        ]
        assert len(scopes) == 2 and any("/always/" in key for key in scopes)
        assert any(("/on_cancel/" if cancelled else "/on_success/") in key for key in scopes)
    finally:
        studio.close()
        tool_registry.unregister("interop:shared-recovery", func=record)
