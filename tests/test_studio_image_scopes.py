"""Image references follow scoped receipts, including exit phases and recovery."""

import json
import runpy
from pathlib import Path

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_decisions import StudioTypedDecisions
from nexus.core.studio_workflow_artifacts import image_references, node_image_states
from nexus.core.studio_workflow_v2 import resolve
from nexus.tools import tool_registry
from tests.test_studio_interop_shared_handlers import gate
from tests.test_studio_interop_workflows import run
from tests.test_studio_workflow_v2 import wait


def source():
    return {
        "agent_name": "cleanup-images",
        "agent_type": "workflow",
        "decision": {"model_alias": "vision"},
        "tools": [{"tool_type": "builtin", "tool_name": "record"}],
        "nodes": [
            {"id": "work", "type": "tool", "tool_name": "record", "input": {"label": "work"}},
            {
                "id": "evidence",
                "type": "tool",
                "tool_name": "record",
                "input": {"label": "evidence"},
            },
            {
                "id": "inspect",
                "type": "decision",
                "questions": {"visible": {"type": "boolean", "instructions": "Inspect evidence"}},
                "images": ["{{work.output.photo}}", "{{evidence.output.photo}}"],
            },
        ],
        "on_exit": {"always": "evidence", "on_success": "inspect"},
        "output_mapping": {"answer": "{{inspect.output}}"},
    }


def install(studio, value):
    report = studio.configuration.convert_interop(
        {
            "documents": [value],
            "bindings": {
                "decisions": {"vision": {"backend": "openai", "model": "gpt-6-luna"}},
                "tools": {"record": "interop:image-scopes"},
            },
        },
        None,
    )
    assert report["valid"], report
    plan = studio.configuration.plan(report["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    return report["document"]


@pytest.mark.parametrize("backend", ["files", "sqlite"])
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_exit_image_capture_survives_approval_restart_and_rejects_failed_phase(
    tmp_path, monkeypatch, backend, failed, cancelled
):
    calls, observed = [], []

    async def record(label):
        calls.append(label)
        if label == "evidence" and failed:
            raise ValueError("Synthetic failed image producer")
        return {"photo": label + ".png:1"}

    async def decide(self, config, node_input, payload, run_id, identity, *, images):
        # Only the client boundary is replaced: real graph/reference resolution,
        # approval, failure and durable recovery paths execute unchanged.
        observed.append(images)
        return {"visible": True}

    monkeypatch.setattr(StudioTypedDecisions, "execute", decide)
    tool_registry.register("interop:image-scopes", record, "Local evidence fixture")
    options = (
        None
        if backend == "files"
        else {"url": "sqlite:///" + str(tmp_path / "images.sqlite"), "namespace": "image-scopes"}
    )
    studio = StudioService(tmp_path, state_options=options)
    try:
        gate(studio, "Review evidence")
        value = source()
        value["nodes"].insert(
            2, {"id": "review", "type": "workflow", "workflow_name": "Review evidence"}
        )
        value["on_exit"]["on_success"] = "review"
        value["nodes"][-1]["depends_on"] = ["review"]
        if cancelled:
            gate(studio, "Wait in main")
            value["nodes"].append(
                {
                    "id": "hold",
                    "type": "workflow",
                    "workflow_name": "Wait in main",
                    "depends_on": ["work"],
                }
            )
            value["on_exit"]["on_cancel"] = "review"
        install(studio, value)
        paused = run(studio, "cleanup-images", {}, "waiting")
        if cancelled:
            assert calls == ["work"]
            paused = wait(studio, studio.cancel_run(paused["id"]), "waiting")
        assert calls == ["work", "evidence"] and observed == []

        # Simulate a pre-upgrade receipt: derived image context was not stored.
        # Recovery must use scoped acknowledgements, never assume missing metadata
        # means the document is safe or force the completed producer to run again.
        def legacy(value):
            if isinstance(value, dict):
                return {k: legacy(v) for k, v in value.items() if k != "_image_capture_states"}
            if isinstance(value, list):
                return [legacy(v) for v in value]
            return value

        checkpoint = studio._path("graph-runs", paused["id"])
        studio._write(checkpoint, legacy(studio._read(checkpoint)))
        studio.close()
        studio = StudioService(tmp_path, state_options=options)
        result = wait(
            studio,
            studio.graph.resume(paused["id"], "approve"),
            "cancelled" if cancelled else "completed",
        )
        assert calls == ["work", "evidence"]
        if failed:
            assert observed == []
            assert len(result["workflow_warnings"]) == 2
        else:
            assert observed == [["work.png:1", "evidence.png:1"]]
            if not cancelled:
                assert json.loads(result["output"]) == {"answer": {"visible": True}}
    finally:
        studio.close()
        tool_registry.unregister("interop:image-scopes", func=record)


@pytest.mark.parametrize("status", ["completed", "failed", "skipped"])
def test_captured_image_uses_outer_acknowledgement_not_same_named_local_node(status):
    context = {
        "interop_nodes": {"producer": {"photo": "outer.png:2"}},
        "nodes": {"producer": {"photo": "local.png:3"}},
        "_image_capture_states": {
            "producer": {"status": status, "reason": "pruned", "output": {"photo": "outer.png:2"}}
        },
    }
    local = {"producer": {"status": "failed" if status == "completed" else "completed"}}
    entries = [{"ref": "interop_nodes.producer.photo"}]
    if status == "failed":
        with pytest.raises(ValueError, match="failed or unfinished"):
            image_references(entries, context, local, resolve)
    else:
        assert image_references(entries, context, local, resolve) == (
            ["outer.png:2"] if status == "completed" else []
        )


def test_local_image_never_borrows_outer_acknowledgement():
    context = {
        "nodes": {},
        "_image_capture_states": {"producer": {"status": "completed", "output": "old.png"}},
    }
    with pytest.raises(ValueError, match="no acknowledged"):
        image_references([{"ref": "nodes.producer"}], context, {}, resolve)


def test_missing_completed_value_or_loop_alias_is_not_an_acknowledged_nil():
    graph = {"nodes": [{"id": "producer", "interop_body_id": "body"}]}
    state = {"nodes": {"root/producer": "completed"}}
    missing = node_image_states(graph, "root", state, {})
    assert missing["producer"]["status"] == missing["body"]["status"] == "pending"
    nil = node_image_states(graph, "root", state, {"producer": None, "body": None})
    assert nil["producer"]["status"] == nil["body"]["status"] == "completed"


@pytest.mark.parametrize("unresolved", [False, True])
def test_exit_branch_pruning_omits_image_but_unresolved_route_blocks_client(
    tmp_path, monkeypatch, unresolved
):
    observed = []

    async def record(label):
        return {"photo": label + ".png:1"}

    async def decide(self, *args, images):
        observed.append(images)
        return {"visible": True}

    monkeypatch.setattr(StudioTypedDecisions, "execute", decide)
    tool_registry.register("interop:image-scopes", record, "Local evidence fixture")
    studio = StudioService(tmp_path)
    try:
        value = source()
        value["nodes"][1]["depends_on"] = ["route"]
        value["nodes"].extend(
            [
                {
                    "id": "route",
                    "type": "switch",
                    "cases": [
                        {
                            "condition": '{{work.output.missing}} == "x"'
                            if unresolved
                            else "false",
                            "node": "evidence",
                        }
                    ],
                    "default": "other",
                },
                {
                    "id": "other",
                    "type": "tool",
                    "tool_name": "record",
                    "depends_on": ["route"],
                    "input": {"label": "other"},
                },
            ]
        )
        value["on_exit"]["always"] = "route"
        install(studio, value)
        result = run(studio, "cleanup-images", {})
        assert observed == ([] if unresolved else [["work.png:1"]])
        assert len(result.get("workflow_warnings", [])) == int(unresolved)
    finally:
        studio.close()
        tool_registry.unregister("interop:image-scopes", func=record)


@pytest.mark.parametrize("collection", ["map", "loop"])
def test_collection_capture_does_not_collide_with_same_named_local_node(
    tmp_path, monkeypatch, collection
):
    observed = []

    async def record(label):
        return {"photo": label + ".png:1"}

    async def decide(self, *args, images):
        observed.append(images)
        return {"visible": True}

    monkeypatch.setattr(StudioTypedDecisions, "execute", decide)
    tool_registry.register("interop:image-scopes", record, "Local evidence fixture")
    studio = StudioService(tmp_path)
    try:
        value = source()
        value.pop("on_exit")
        value["nodes"] = [value["nodes"][0], value["nodes"][2]]
        value["nodes"][1]["images"] = ["{{work.output.photo}}"]
        value["nodes"].append(
            {
                "id": "each",
                "type": collection,
                "node": "inspect",
                "depends_on": ["work"],
                **(
                    {"items": [1, 2], "concurrency_limit": 2}
                    if collection == "map"
                    else {"condition": "false", "max_iterations": 2}
                ),
            }
        )
        value["output_mapping"] = {"result": "{{each.output}}"}
        report = studio.configuration.convert_interop(
            {
                "documents": [value],
                "bindings": {
                    "decisions": {"vision": {"backend": "openai", "model": "gpt-6-luna"}},
                    "tools": {"record": "interop:image-scopes"},
                },
            },
            None,
        )
        assert report["valid"], report
        definition = report["document"]["definitions"][0]["document"]
        body = next(n for n in definition["graph"]["nodes"] if n["id"] == "each")["body"]
        body["nodes"].append(
            {
                "id": "work",
                "kind": "tool",
                "tool_id": "interop:image-scopes",
                "kwargs": {"label": "local"},
            }
        )
        body["edges"] = [
            {"source": "START", "target": "work"},
            {"source": "work", "target": "inspect"},
        ]
        studio.save_definition(
            "workflows", {"name": "cleanup-images", "graph": definition["graph"]}
        )
        run(studio, "cleanup-images", {})
        assert observed == [["work.png:1"]] * (2 if collection == "map" else 1)
    finally:
        studio.close()
        tool_registry.unregister("interop:image-scopes", func=record)


def test_example_installs_without_model_use_then_captures_builtin_cleanup_output(
    tmp_path, monkeypatch
):
    observed = []

    async def decide(self, *args, images):
        observed.append(images)
        return {"visible": True}

    monkeypatch.setattr(StudioTypedDecisions, "execute", decide)
    example = runpy.run_path(
        str(Path(__file__).parents[1] / "examples/nexus-bot/src/studio_cleanup_images.py")
    )
    studio = StudioService(tmp_path)
    try:
        report = studio.configuration.convert_interop(example["document"]("chosen.png:2"), None)
        assert report["valid"], report
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        assert observed == [] and studio.records("runs") == []
        result = run(studio, "cleanup-image-review", {})
        assert observed == [["chosen.png:2"]]
        assert json.loads(result["output"]) == {"review": {"visible": True}}
    finally:
        studio.close()
