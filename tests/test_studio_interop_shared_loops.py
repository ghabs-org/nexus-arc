"""Ordered shared loop templates preserve external aliases and durable controller seeds."""

import json

import pytest

from nexus.core.studio import StudioService
from nexus.tools import tool_registry
from tests.test_studio_interop_workflows import apply, run
from tests.test_studio_workflow_v2 import wait


def source():
    return {
        "agent_name": "shared-loops",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "increase"}],
        "nodes": [
            {
                "id": "first",
                "type": "loop",
                "node": "body",
                "condition": "{{_loop_iteration}} < 1",
                "max_iterations": 3,
            },
            {
                "id": "second",
                "type": "loop",
                "node": "body",
                "depends_on": ["first"],
                "condition": "{{_loop_iteration}} < 1",
                "max_iterations": 3,
            },
            {
                "id": "body",
                "type": "tool",
                "tool_name": "increase",
                "input": {
                    "previous": "{{body.output.total}}",
                    "iteration": "{{_loop_iteration}}",
                },
            },
        ],
        "output_mapping": {
            "first": "{{first.output}}",
            "second": "{{second.output}}",
            "body": "{{body.output}}",
        },
    }


@pytest.mark.parametrize("mode", ["ordered", "transitive", "pruned", "maximum", "concurrent"])
def test_shared_loop_alias_and_source_roundtrip(tmp_path, mode):
    import asyncio

    calls = []

    async def increase(previous=None, iteration=0, delay=0.0):
        if delay:
            await asyncio.sleep(delay)
        calls.append((previous, iteration))
        return {"total": (previous or 0) + 1}

    tool_registry.register("interop:shared-loop", increase, "Local loop fixture")
    studio = StudioService(tmp_path)
    try:
        value = source()
        if mode == "transitive":
            value["nodes"].append(
                {"id": "between", "type": "tool", "tool_name": "increase", "depends_on": ["first"]}
            )
            value["nodes"][1]["depends_on"] = ["between"]
        if mode == "pruned":
            value["nodes"][1]["when"] = "false"
        if mode == "maximum":
            value["nodes"][1].update(condition="true", max_iterations=1)
        if mode == "concurrent":
            value["nodes"][1].pop("depends_on")
            value["nodes"][1].update(condition="true", max_iterations=1)
            # Even if first finishes after second in wall-clock time,
            # second is last in declaration order among maximal controllers.
            value["nodes"][2]["input"]["delay"] = 0.02
        native = apply(studio, value, {"increase": "interop:shared-loop"})
        result = json.loads(run(studio, "shared-loops", {})["output"])
        assert result["first"] == {"iterations_completed": 2, "stopped_reason": "condition_false"}
        if mode == "concurrent":
            assert result["second"] == {
                "iterations_completed": 1,
                "stopped_reason": "max_iterations",
            }
            assert result["body"] == {"total": 1}
            assert sorted(calls, key=lambda x: (x[0] or -1, x[1])) == [
                (None, 0),
                (None, 0),
                (1, 1),
            ]
        else:
            assert result["body"] == {
                "total": 2 if mode == "pruned" else 3 if mode == "maximum" else 4
            }
            loop_calls = calls if mode != "transitive" else calls[:2] + calls[3:]
            assert loop_calls == [(None, 0), (1, 1)] + (
                [] if mode == "pruned" else [(2, 0)] if mode == "maximum" else [(2, 0), (3, 1)]
            )
        exported = studio.configuration.export_interop({"document": native}, None)
        assert exported["valid"], exported
        again = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
        assert again["valid"] and again["document"] == native
    finally:
        studio.close()
        tool_registry.unregister("interop:shared-loop", func=increase)


@pytest.mark.parametrize("mode", ["unjoined_reader", "mixed", "collision"])
def test_unsupported_shared_loop_ownership_rejects_without_writes(tmp_path, mode):
    studio = StudioService(tmp_path)
    try:
        value = source()
        if mode == "unjoined_reader":
            value["nodes"][1].pop("depends_on")
            value["nodes"].append(
                {
                    "id": "reader",
                    "type": "tool",
                    "tool_name": "increase",
                    "depends_on": ["first"],
                    "input": {"previous": "{{body.output.total}}"},
                }
            )
        elif mode == "mixed":
            value["nodes"][1] = {"id": "second", "type": "map", "node": "body", "items": []}
        else:
            # Native graph validation must also protect alias/root collisions.
            value["nodes"][0]["node"] = "first"
        report = studio.configuration.convert_interop(
            {"documents": [value], "bindings": {"tools": {"increase": "text:wordcount"}}}, None
        )
        assert not report["valid"] and report["document"] is None
        assert report["diagnostics"]
        assert not list((tmp_path / "workflows").glob("*.yaml"))
    finally:
        studio.close()


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_second_controller_recovery_pins_seed_and_child_revision(tmp_path, backend):
    calls = []

    async def increase(previous=None, iteration=0):
        calls.append((previous, iteration))
        return {"total": (previous or 0) + 1}

    tool_registry.register("interop:loop-recovery", increase, "Local approved loop fixture")
    options = (
        None
        if backend == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
            "namespace": "loops",
            "workers": 1,
        }
    )
    root = tmp_path / "workspace"
    studio = StudioService(root, state_options=options)
    try:
        studio.save_definition(
            "workflows",
            {
                "name": "Loop child",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {"id": "gate", "kind": "approval"},
                        {
                            "id": "increase",
                            "kind": "tool",
                            "tool_id": "interop:loop-recovery",
                            "kwargs": {
                                "previous": {"ref": "input.previous"},
                                "iteration": {"ref": "input.iteration"},
                            },
                            "depends_on": ["gate"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.increase"},
                },
            },
        )
        value = source()
        body = value["nodes"][2]
        body.pop("tool_name")
        body.update(type="workflow", workflow_name="Loop child")
        # Missing previous at the first iteration is explicit null for the native child.
        body["input"]["previous"] = {"coalesce": ["{{body.output.total}}", None]}
        apply(studio, value, {"increase": "interop:loop-recovery"})
        paused = run(studio, "shared-loops", {}, "waiting")
        # Complete first controller and first iteration of second controller.
        for _ in range(3):
            paused = wait(studio, studio.graph.resume(paused["id"], "approve"), "waiting")
        assert calls == [(None, 0), (1, 1), (2, 0)]
        saved = studio._read(studio._path("graph-runs", paused["id"]))
        assert saved["outputs"]["root/second/interop_initial"] == {"total": 2}
        assert saved["outputs"]["root/body"] == {"total": 3}
        studio.close()
        (root / "workflows/loop-child.yaml").unlink()
        studio = StudioService(root, state_options=options)
        result = wait(studio, studio.graph.resume(paused["id"], "approve"), "completed")
        assert json.loads(result["output"])["body"] == {"total": 4}
        assert calls == [(None, 0), (1, 1), (2, 0), (3, 1)]
    finally:
        studio.close()
        tool_registry.unregister("interop:loop-recovery", func=increase)


def test_native_configuration_cannot_bypass_shared_alias_order(tmp_path):
    studio = StudioService(tmp_path)
    try:
        doc = source()
        doc["nodes"].append(
            {
                "id": "reader",
                "type": "tool",
                "tool_name": "increase",
                "depends_on": ["first", "second"],
                "input": {"previous": "{{body.output.total}}"},
            }
        )
        report = studio.configuration.convert_interop(
            {"documents": [doc], "bindings": {"tools": {"increase": "text:wordcount"}}}, None
        )
        assert report["valid"], report
        graph = report["document"]["definitions"][0]["document"]["graph"]
        # Drop second -> reader edge so second is concurrent with reader while reader reads body.
        graph["edges"] = [
            e
            for e in graph["edges"]
            if not (e["source"] in ("first", "second") and e["target"] in ("second", "reader"))
            or (e["source"] == "first" and e["target"] == "reader")
        ]
        for node in graph["nodes"]:
            if node["id"] == "second":
                node.pop("depends_on", None)
            elif node["id"] == "reader":
                node["depends_on"] = ["first"]
        with pytest.raises(ValueError, match="dependency ordering"):
            studio.save_definition("workflows", {"name": "Unsafe shared alias", "graph": graph})
        assert not list((tmp_path / "workflows").glob("*.yaml"))
    finally:
        studio.close()


@pytest.mark.parametrize("backend", ["files", "sqlite"])
@pytest.mark.parametrize("completed_loops", [1, 2])
@pytest.mark.parametrize("corrupt", [False, True])
def test_legacy_completed_controllers_recover_their_own_body_receipts(
    tmp_path, backend, completed_loops, corrupt
):
    from tests.test_studio_interop_shared_handlers import gate

    calls = []

    async def increase(previous=None, iteration=0):
        calls.append((previous, iteration))
        return {"total": (previous or 0) + 1}

    tool_registry.register("interop:legacy-loop", increase, "Local pre-upgrade loop fixture")
    root = tmp_path / "workspace"
    options = (
        None
        if backend == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
            "namespace": "legacy-loops",
            "workers": 1,
        }
    )
    studio = StudioService(root, state_options=options)
    try:
        gate(studio, "Wait between loops")
        value = source()
        value["nodes"][0]["condition"] = value["nodes"][1]["condition"] = "false"
        previous = "first"
        if completed_loops == 2:
            value["nodes"].insert(
                1,
                {
                    "id": "middle",
                    "type": "loop",
                    "node": "body",
                    "condition": "false",
                    "depends_on": ["first"],
                },
            )
            previous = "middle"
        value["nodes"].append(
            {
                "id": "review",
                "type": "workflow",
                "workflow_name": "Wait between loops",
                "depends_on": [previous],
            }
        )
        next(node for node in value["nodes"] if node["id"] == "second")["depends_on"] = ["review"]
        apply(studio, value, {"increase": "interop:legacy-loop"})
        paused = run(studio, "shared-loops", {}, "waiting")
        assert len(calls) == completed_loops
        path = studio._path("graph-runs", paused["id"])
        saved = studio._read(path)
        saved["outputs"] = {
            key: item for key, item in saved["outputs"].items() if not key.endswith("/interop_body")
        }
        if corrupt:
            saved["outputs"].pop("root/first/0/body")
        studio._write(path, saved)
        studio.close()
        (root / "workflows/wait-between-loops.yaml").unlink()
        studio = StudioService(root, state_options=options)
        result = wait(
            studio,
            studio.graph.resume(paused["id"], "approve"),
            "failed" if corrupt else "completed",
        )
        if corrupt:
            assert "missing acknowledged body outputs" in result["error"]
            assert len(calls) == completed_loops
            return
        assert json.loads(result["output"])["body"] == {"total": completed_loops + 1}
        assert calls == [(None, 0), *[(i, 0) for i in range(1, completed_loops + 1)]]
        saved = studio._read(studio._path("graph-runs", paused["id"]))
        assert saved["outputs"]["root/first/interop_body"] == {"total": 1}
        assert saved["outputs"]["root/second/interop_initial"] == {"total": completed_loops}
    finally:
        studio.close()
        tool_registry.unregister("interop:legacy-loop", func=increase)


@pytest.mark.parametrize("reverse", [False, True])
def test_concurrent_join_seed_uses_dependency_and_declaration_not_finish_order(tmp_path, reverse):
    import asyncio

    calls = []

    async def increase(previous=None, iteration=0):
        position = len(calls)
        calls.append((previous, iteration))
        await asyncio.sleep(0.03 if position == 0 else 0.001)
        return {"total": (previous or 0) + 1}

    tool_registry.register("interop:joined-loops", increase, "Local concurrent join")
    studio = StudioService(tmp_path)
    try:
        value = source()
        value["nodes"][1].pop("depends_on")
        value["nodes"][1]["condition"] = "false"
        if reverse:
            value["nodes"][0], value["nodes"][1] = value["nodes"][1], value["nodes"][0]
        value["nodes"].append(
            {
                "id": "joined",
                "type": "loop",
                "node": "body",
                "condition": "false",
                "depends_on": ["first", "second"],
            }
        )
        apply(studio, value, {"increase": "interop:joined-loops"})
        result = run(studio, "shared-loops", {})
        saved = studio._read(studio._path("graph-runs", result["id"]))
        expected = 2 if reverse else 1
        assert saved["outputs"]["root/first/interop_initial"] is None
        assert saved["outputs"]["root/second/interop_initial"] is None
        assert saved["outputs"]["root/joined/interop_initial"] == {"total": expected}
        assert json.loads(result["output"])["body"] == {"total": expected + 1}
        assert len(calls) == 4
    finally:
        studio.close()
        tool_registry.unregister("interop:joined-loops", func=increase)


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_concurrent_approvals_and_join_restart_keep_pinned_children(tmp_path, backend):
    calls = []

    async def increase(previous=None, iteration=0):
        calls.append((previous, iteration))
        return {"total": (previous or 0) + 1}

    tool_registry.register(
        "interop:concurrent-approved", increase, "Local concurrent approved loops"
    )
    root = tmp_path / "workspace"
    options = (
        None
        if backend == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
            "namespace": "concurrent-loops",
            "workers": 1,
        }
    )
    studio = StudioService(root, state_options=options)
    try:
        studio.save_definition(
            "workflows",
            {
                "name": "Approved loop child",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {"id": "review", "kind": "approval"},
                        {
                            "id": "effect",
                            "kind": "tool",
                            "tool_id": "interop:concurrent-approved",
                            "kwargs": {
                                "previous": {"ref": "input.previous"},
                                "iteration": {"ref": "input.iteration"},
                            },
                            "depends_on": ["review"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.effect"},
                },
            },
        )
        value = source()
        value["nodes"][0]["condition"] = value["nodes"][1]["condition"] = "false"
        value["nodes"][1].pop("depends_on")
        value["nodes"][2].pop("tool_name")
        value["nodes"][2].update(type="workflow", workflow_name="Approved loop child")
        value["nodes"][2]["input"]["previous"] = {"coalesce": ["{{body.output.total}}", None]}
        value["nodes"].append(
            {
                "id": "joined",
                "type": "loop",
                "node": "body",
                "condition": "false",
                "depends_on": ["first", "second"],
            }
        )
        apply(studio, value, {"increase": "interop:concurrent-approved"})
        paused = run(studio, "shared-loops", {}, "waiting")
        assert len(paused["awaiting_approval"]) == 2 and calls == []
        studio.close()
        (root / "workflows/approved-loop-child.yaml").unlink()
        studio = StudioService(root, state_options=options)
        joined = wait(studio, studio.graph.resume(paused["id"], "approve"), "waiting")
        assert calls == [(None, 0), (None, 0)]
        studio.close()
        studio = StudioService(root, state_options=options)
        result = wait(studio, studio.graph.resume(joined["id"], "approve"))
        assert calls == [(None, 0), (None, 0), (1, 0)]
        assert json.loads(result["output"])["body"] == {"total": 2}
    finally:
        studio.close()
        tool_registry.unregister("interop:concurrent-approved", func=increase)


@pytest.mark.parametrize("value", [None, 0, {}, []])
def test_legacy_receipt_upgrade_is_idempotent_and_preserves_nil_or_empty_outputs(value):
    from copy import deepcopy

    from nexus.core.studio_workflow_v2 import restore_loop_receipt

    node = {
        "kind": "loop",
        "interop_body_id": "body",
        "max_iterations": 2,
        "body": {"nodes": [{"id": "body"}], "output_mapping": {"ref": "nodes.body"}},
    }
    state = {
        "nodes": {"root/loop/0/body": "completed"},
        "outputs": {"root/loop": {"iterations_completed": 1}, "root/loop/0/body": value},
    }
    assert restore_loop_receipt(node, "root/loop", state)
    assert state["outputs"]["root/loop/interop_body"] == value
    saved = deepcopy(state)
    assert not restore_loop_receipt(node, "root/loop", state)
    assert state == saved
