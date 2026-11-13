"""Scoped exit routes, branch publication and phase receipts use one runtime."""

import json

import pytest

from nexus.core.studio import StudioService
from nexus.tools import tool_registry
from tests.test_studio_interop_shared_handlers import gate
from tests.test_studio_interop_workflows import apply, run
from tests.test_studio_team import team as team
from tests.test_studio_workflow_v2 import wait


def source():
    return {
        "agent_name": "exit-routes",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "record"}],
        "on_exit": dict.fromkeys(("always", "on_success", "on_failure", "on_cancel"), "cleanup"),
        "nodes": [
            {"id": "work", "type": "tool", "tool_name": "record", "input": {"label": "work"}},
            {"id": "cleanup", "type": "tool", "tool_name": "record", "input": {"label": "cleanup"}},
            {
                "id": "route",
                "type": "switch",
                "depends_on": ["cleanup"],
                "cases": [{"condition": "{{cleanup.output}} == 1", "node": "first"}],
                "default": "second",
            },
            {
                "id": "first",
                "type": "tool",
                "tool_name": "record",
                "depends_on": ["route"],
                "input": {"label": "first"},
            },
            {
                "id": "second",
                "type": "tool",
                "tool_name": "record",
                "depends_on": ["route"],
                "input": {"label": "second"},
            },
            {
                "id": "joined",
                "type": "tool",
                "tool_name": "record",
                "depends_on": ["first", "second"],
                "input": {
                    "label": "joined",
                    "value": {"coalesce": ["{{first.output}}", "{{second.output}}"]},
                },
            },
        ],
        "output_mapping": {
            "body": "{{work.output}}",
            "cleanup": "{{cleanup.output}}",
            "result": {"coalesce": ["{{first.output}}", "{{second.output}}"]},
            "joined": "{{joined.output}}",
        },
    }


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled", "cleanup-failure"])
def test_exit_subgraph_routes_each_phase_and_clears_old_branches(tmp_path, outcome):
    calls = []

    async def record(label, value=None):
        calls.append((label, value))
        if label == "work" and outcome == "failure":
            raise ValueError("Synthetic primary failure")
        if label == "joined" and outcome == "cleanup-failure" and value == "first":
            raise ValueError("Synthetic first cleanup failure")
        if label == "cleanup":
            return sum(item[0] == "cleanup" for item in calls)
        return value if label == "joined" else label

    tool_registry.register("interop:exit-route-record", record, "Local routed cleanup fixture")
    app = StudioService(tmp_path)
    try:
        value = source()
        value["nodes"][3]["input"]["value"] = "{{work.output}}"
        if outcome == "cancelled":
            gate(app, "Wait")
            value["nodes"][0] = {"id": "work", "type": "workflow", "workflow_name": "Wait"}
        native = apply(app, value, {"record": "interop:exit-route-record"})
        if outcome == "cancelled":
            paused = run(app, "exit-routes", {}, "waiting")
            result = wait(app, app.cancel_run(paused["id"]), "cancelled")
        else:
            result = run(app, "exit-routes", {}, "failed" if outcome == "failure" else "completed")
        saved = app._read(app._path("graph-runs", result["id"]))
        assert [item[0] for item in calls if item[0] != "work"] == [
            "cleanup",
            "first",
            "joined",
            "cleanup",
            "second",
            "joined",
        ]
        assert "root/first" not in saved["outputs"]
        assert saved["outputs"]["root/second"] == saved["outputs"]["root/joined"] == "second"
        phase = (
            "on_failure"
            if outcome == "failure"
            else "on_cancel"
            if outcome == "cancelled"
            else "on_success"
        )
        assert saved["nodes"]["root/interop_exit/cleanup/always/first"] == "completed"
        assert saved["nodes"][f"root/interop_exit/cleanup/{phase}/first"] == "skipped"
        if result["status"] == "completed":
            assert ("first", "work") in calls
            assert json.loads(result["output"]) == {
                "body": "work",
                "cleanup": 2,
                "result": "second",
                "joined": "second",
            }
        if outcome == "failure":
            assert "Synthetic primary failure" in result["error"]
        if outcome == "cleanup-failure":
            assert len(result["workflow_warnings"]) == 1
        exported = app.configuration.export_interop({"document": native}, None)
        assert exported["valid"], exported
        assert (
            app.configuration.convert_interop({"documents": [exported["bundle"]]}, None)["document"]
            == native
        )
    finally:
        app.close()
        tool_registry.unregister("interop:exit-route-record", func=record)


def test_exit_branch_cannot_depend_on_main_graph_without_explicit_capture(tmp_path):
    app = StudioService(tmp_path)
    try:
        value = source()
        value["nodes"][3]["depends_on"].append("work")
        report = app.configuration.convert_interop(
            {"documents": [value], "bindings": {"tools": {"record": "text:wordcount"}}}, None
        )
        assert not report["valid"] and report["document"] is None
        assert not app.records("runs") and not app.records("configuration-plans")
    finally:
        app.close()


@pytest.mark.parametrize("backend", ["files", "sqlite"])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("uncertain", [False, True])
def test_routed_exit_approval_restarts_with_pinned_child_and_no_repeated_effect(
    tmp_path, backend, cancelled, uncertain
):
    calls = []

    async def record(label, value=None):
        calls.append(label)
        return (
            calls.count("cleanup") if label == "cleanup" else value if label == "joined" else label
        )

    tool_registry.register(
        "interop:routed-exit-recovery", record, "Local approved cleanup branches"
    )
    options = (
        None
        if backend == "files"
        else {"url": "sqlite:///" + str(tmp_path / "routes.sqlite"), "namespace": "routed-exits"}
    )
    root = tmp_path / "workspace"
    app = StudioService(root, state_options=options)
    try:
        app.save_definition(
            "workflows",
            {
                "name": "Approved branch",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {"id": "gate", "kind": "approval"},
                        {
                            "id": "effect",
                            "kind": "tool",
                            "tool_id": "interop:routed-exit-recovery",
                            "kwargs": {"label": "approved-effect"},
                            "depends_on": ["gate"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.effect"},
                },
            },
        )
        value = source()
        for index, identity in [(3, "first"), (4, "second")]:
            value["nodes"][index] = {
                "id": identity,
                "type": "workflow",
                "workflow_name": "Approved branch",
                "depends_on": ["route"],
            }
        if cancelled:
            gate(app, "Wait")
            value["nodes"][0] = {"id": "work", "type": "workflow", "workflow_name": "Wait"}
        apply(app, value, {"record": "interop:routed-exit-recovery"})
        paused = run(app, "exit-routes", {}, "waiting")
        if cancelled:
            paused = wait(app, app.cancel_run(paused["id"]), "waiting")
        if uncertain:
            import time

            checkpoint = app._path("graph-runs", paused["id"])
            saved = app._read(checkpoint)
            effect_key = paused["awaiting_approval"][0]["id"].rsplit("/", 1)[0] + "/effect"
            saved["started"][effect_key] = time.time()
            app._write(checkpoint, saved)
        next_gate = wait(app, app.graph.resume(paused["id"], "approve"), "waiting")
        assert calls.count("approved-effect") == (0 if uncertain else 1)
        app.close()
        (root / "workflows/approved-branch.yaml").unlink()
        app = StudioService(root, state_options=options)
        result = wait(
            app,
            app.graph.resume(next_gate["id"], "approve"),
            "cancelled" if cancelled else "completed",
        )
        assert calls.count("approved-effect") == (1 if uncertain else 2)
        assert calls.count("work") == (0 if cancelled else 1)
        saved = app._read(app._path("graph-runs", result["id"]))
        assert "root/first" not in saved["outputs"]
        assert saved["outputs"]["root/second"] == "approved-effect"
        effects = [
            key
            for key, status in saved["nodes"].items()
            if key.endswith("/effect") and status == "completed"
        ]
        assert len(effects) == (1 if uncertain else 2)
        assert any("/always/first/" in key for key in effects) is not uncertain
        if uncertain:
            assert result["workflow_warnings"][0]["node_id"].endswith("/always")
        assert any(
            ("/on_cancel/second/" if cancelled else "/on_success/second/") in key for key in effects
        )
    finally:
        app.close()
        tool_registry.unregister("interop:routed-exit-recovery", func=record)


@pytest.mark.parametrize("choice,selected", [("first", "first"), ("other", "second")])
def test_decision_exit_root_routes_direct_and_default_branches(tmp_path, choice, selected):
    calls, decisions = [], []

    async def record(label, value=None):
        calls.append(label)
        return value if label == "joined" else label

    def decide(state, questions, **kwargs):
        decisions.append(state)
        return {
            "path": {
                "type": "choice",
                "choice": choice,
                "confidence": 0.99,
                "probabilities": {key: int(key == choice) for key in ("first", "other")},
            }
        }

    tool_registry.register("interop:exit-decision-record", record, "Local decision cleanup fixture")
    app = StudioService(tmp_path, workbench_options={"decision_client": decide})
    try:
        value = source()
        value["decision"] = {"model_alias": "cleanup"}
        value["nodes"][1] = {
            "id": "cleanup",
            "type": "decision",
            "questions": {
                "path": {
                    "type": "choice",
                    "instructions": "Select cleanup",
                    "criteria": {"first": "First", "other": "Default"},
                }
            },
            "route_on": "path",
            "routes": {"first": "first"},
            "default": "second",
            "input": {"outcome": "{{workflow.status}}"},
        }
        value["nodes"].pop(2)
        for node in value["nodes"][2:4]:
            node["depends_on"] = ["cleanup"]
        report = app.configuration.convert_interop(
            {
                "documents": [value],
                "bindings": {
                    "tools": {"record": "interop:exit-decision-record"},
                    "decisions": {
                        "cleanup": {"backend": "openrouter", "model": "typesafe/jev-1.13"}
                    },
                },
            },
            None,
        )
        assert report["valid"], report
        plan = app.configuration.plan(report["document"], None)
        app.configuration.apply(plan["id"], plan["digest"], None)
        result = json.loads(run(app, "exit-routes", {})["output"])
        assert result["result"] == result["joined"] == selected
        assert result["cleanup"]["selected_branch"] == selected
        assert calls == ["work", selected, "joined", selected, "joined"]
        assert decisions == [{"outcome": "success"}] * 2
    finally:
        app.close()
        tool_registry.unregister("interop:exit-decision-record", func=record)


def test_http_conversion_and_routed_cleanup_keep_current_execution_identity(team):
    from nexus.core.studio_connections import execution_identity

    app, _, _, request, _ = team
    app.team.allowed_tools.add("interop:exit-http")
    calls = []

    async def record(label, value=None):
        calls.append((label, execution_identity.get()))
        return (
            sum(item[0] == "cleanup" for item in calls)
            if label == "cleanup"
            else value
            if label == "joined"
            else label
        )

    tool_registry.register("interop:exit-http", record, "Local HTTP cleanup fixture")
    try:
        payload = {"documents": [source()], "bindings": {"tools": {"record": "interop:exit-http"}}}
        assert (
            request("/api/studio/configuration/interop/convert", payload, user="viewer")[0] == 403
        )
        code, converted = request(
            "/api/studio/configuration/interop/convert", payload, user="owner"
        )
        assert code == 200 and converted["valid"], converted
        graph = converted["document"]["definitions"][0]["document"]["graph"]
        code, _ = request("/api/workflows", {"name": "exit-routes", "graph": graph}, user="alice")
        assert code == 201
        code, task = request(
            "/api/runs",
            {
                "kind": "workflow",
                "workflow_id": "exit-routes",
                "task": "Private routed cleanup",
                "requester_nexus_id": "owner",
            },
            user="alice",
        )
        assert code == 202
        assert wait(app, task)["status"] == "completed"
        assert len(calls) == 7 and {identity for _, identity in calls} == {"alice"}
        assert request(f"/api/runs/{task['id']}", user="bob")[0] == 403
    finally:
        tool_registry.unregister("interop:exit-http", func=record)
