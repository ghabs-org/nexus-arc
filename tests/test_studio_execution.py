"""Read-only execution replay, durable events and privacy boundaries."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_features import StudioFeatures
from nexus.core.studio_team import NexusTeamPolicy


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.team = None
    service.close()


def saved_run(studio, identity="run-one", **fields):
    record = {
        "id": identity,
        "kind": "workflow",
        "status": "running",
        "task": "private task",
        "provider": "fixture",
        "created_at": "2026-10-18T09:00:00+00:00",
        **fields,
    }
    studio._write(studio._path("runs", identity), record)
    return record


def test_events_are_durable_monotonic_and_content_free(studio):
    saved_run(studio)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(
            pool.map(
                lambda index: studio.execution.record(
                    "run-one",
                    "node",
                    node_id=str(index),
                    status="running",
                    arguments={"token": "private"},
                    output="hidden",
                    message="Bearer private token=secret",
                ),
                range(20),
            )
        )
    assert sorted(row["sequence"] for row in records) == list(range(1, 21))
    snapshot = studio.execution.snapshot("run-one", None)
    assert len(snapshot["timeline"]) == 20
    assert snapshot["replay"]["read_only"] is True
    assert "private" not in json.dumps(snapshot)
    assert "hidden" not in json.dumps(snapshot)
    assert "[redacted]" in json.dumps(snapshot)
    assert studio.execution.snapshot("run-one", None) == snapshot


def test_run_and_child_trace_ownership_are_independently_enforced(studio):
    saved_run(studio, requester_nexus_id="alice")
    studio._write(
        studio._path("node-traces", "foreign"),
        {
            "id": "foreign",
            "run_id": "run-one",
            "owner_id": "bob",
            "node_id": "secret-bob-node",
            "status": "completed",
        },
    )
    studio._write(
        studio._path("node-traces", "own"),
        {
            "id": "own",
            "run_id": "run-one",
            "owner_id": "alice",
            "node_id": "alice-node",
            "status": "completed",
            "output": "private-output",
        },
    )
    studio.team = type("Policy", (), {"visible": staticmethod(NexusTeamPolicy.visible)})()
    with pytest.raises(PermissionError):
        studio.execution.snapshot("run-one", {"nexus_id": "bob", "role": "operator"})
    for actor in ({"nexus_id": "alice", "role": "builder"}, {"nexus_id": "admin", "role": "owner"}):
        snapshot = studio.execution.snapshot("run-one", actor)
        assert "alice-node" in json.dumps(snapshot)
        assert "secret-bob-node" not in json.dumps(snapshot)
        assert "private-output" not in json.dumps(snapshot)


def test_legacy_unordered_traces_are_honestly_labeled_and_stable(studio):
    saved_run(studio)
    for identity in ("b", "a"):
        studio._write(
            studio._path("node-traces", identity),
            {
                "id": identity,
                "run_id": "run-one",
                "node_id": identity,
                "kind": "tool",
                "status": "completed",
                "duration_seconds": 0.2,
            },
        )
    snapshot = studio.execution.snapshot("run-one", None)
    assert [row["id"] for row in snapshot["timeline"]] == ["a", "b"]
    assert all(
        row["at"] is None and row["time_source"] == "unavailable" for row in snapshot["timeline"]
    )
    assert snapshot["coverage"]["missing_timestamps"] == 2


def test_structured_map_graph_and_branch_states_are_reconstructed(studio):
    saved_run(studio)
    studio._write(
        studio._path("graph-runs", "run-one"),
        {
            "id": "run-one",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "each",
                        "kind": "map",
                        "body": {"nodes": [{"id": "item", "kind": "tool"}], "edges": []},
                    },
                    {"id": "other", "kind": "switch"},
                ],
                "edges": [
                    {
                        "source": "each",
                        "target": "other",
                        "choice": "done",
                        "contains": "private branch condition",
                    }
                ],
            },
            "nodes": {
                "root/each": "completed",
                "root/each/0/item": "completed",
                "root/each/1/item": "completed",
                "root/other": "skipped",
            },
            "started": {"root/each/0/item": 1000},
            "outputs": {"private": "hidden"},
        },
    )
    snapshot = studio.execution.snapshot("run-one", None)
    assert {row["id"] for row in snapshot["graph"]["nodes"]} == {
        "root/each",
        "root/each/0/item",
        "root/each/1/item",
        "root/other",
    }
    assert snapshot["graph"]["edges"] == [
        {"source": "root/each", "target": "root/other", "choice": "done"}
    ]
    assert snapshot["states"]["root/other"] == "skipped"
    assert "private" not in json.dumps(snapshot)


def test_yaml_snapshot_does_not_require_saved_graph_run(studio):
    saved_run(studio)
    studio._write(
        studio._path("yaml-runs", "run-one"),
        {
            "graph": {"nodes": [{"id": "step-1", "name": "Review", "kind": "agent"}], "edges": []},
            "nodes": {"step-1": "completed"},
            "outputs": {"step-1": "private"},
        },
    )
    snapshot = StudioFeatures(studio).get(["runs", "run-one", "execution"], {}, None)
    assert snapshot["coverage"]["format"] == "yaml"
    assert snapshot["graph"]["nodes"][0]["id"] == "step-1"
    assert "private" not in json.dumps(snapshot)


def test_canonical_nodes_and_invocations_do_not_duplicate_trace_fallback(studio):
    saved_run(studio)
    for kind, collection, source in (
        ("node", "node-traces", "node-source"),
        ("invocation", "invocation-traces", "invoke-source"),
    ):
        studio._write(
            studio._path(collection, source),
            {"id": source, "run_id": "run-one", "node_id": "one", "status": "completed"},
        )
        studio.execution.record("run-one", kind, source_id=source, node_id="one", status="running")
        studio.execution.record(
            "run-one", kind, source_id=source, node_id="one", status="completed"
        )
    snapshot = studio.execution.snapshot("run-one", None)
    assert len(snapshot["timeline"]) == 4
    assert [row["status"] for row in snapshot["timeline"]] == [
        "running",
        "completed",
        "running",
        "completed",
    ]


def test_pagination_is_bounded_and_retrieves_entire_read_only_history(studio):
    saved_run(studio)
    for index in range(7):
        studio.execution.record("run-one", "node", node_id=str(index), status="completed")
    latest = studio.execution.snapshot("run-one", None, limit=3)
    earlier = studio.execution.snapshot(
        "run-one", None, limit=3, before=latest["replay"]["previous_cursor"]
    )
    first = studio.execution.snapshot(
        "run-one", None, limit=3, before=earlier["replay"]["previous_cursor"]
    )
    assert latest["replay"]["event_count"] == 7
    assert [
        row["sequence"] for row in [*first["timeline"], *earlier["timeline"], *latest["timeline"]]
    ] == list(range(1, 8))
    assert first["replay"]["previous_cursor"] is None
    with pytest.raises(ValueError, match="page size"):
        studio.execution.snapshot("run-one", None, limit=1001)
    with pytest.raises(ValueError, match="cursor"):
        studio.execution.snapshot("run-one", None, before="missing")
    with patch.object(studio, "start_run", side_effect=AssertionError("Replay dispatched a task")):
        assert (
            StudioFeatures(studio).get(["runs", "run-one", "execution"], {"limit": ["2"]}, None)[
                "replay"
            ]["event_count"]
            == 7
        )


def test_real_structured_tool_nodes_emit_start_and_completion(studio):
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Replay count",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "count",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": "two words"},
                    }
                ],
            },
        },
    )
    run = studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Count"})
    for _ in range(300):
        if studio.run(run["id"])["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    assert studio.run(run["id"])["status"] == "completed"
    snapshot = studio.execution.snapshot(run["id"], None)
    nodes = [event for event in snapshot["timeline"] if event["kind"] == "node"]
    assert [event["status"] for event in nodes] == ["running", "completed"]
    assert all(event["at"] and event["node_id"] == "root/count" for event in nodes)
    assert all(event["tool"] == "text:wordcount" for event in nodes)


def test_node_failure_is_recorded_without_private_exception(studio):
    from nexus.core.studio_nodes import StudioNodeTypes

    saved_run(studio)

    async def fail(*_):
        raise RuntimeError("private credential secret")

    wrapped = StudioNodeTypes(studio).wrapper(
        fail, {"id": "bad", "name": "Bad", "kind": "tool"}, "run-one", {}
    )
    with pytest.raises(RuntimeError):
        asyncio.run(wrapped(None))
    events = studio.execution.snapshot("run-one", None)["timeline"]
    assert [event["status"] for event in events] == ["running", "failed"]
    assert "private" not in json.dumps(events)


def test_cancelled_node_is_never_reported_completed_or_retried(studio):
    from nexus.core.studio_nodes import StudioNodeTypes

    saved_run(studio)
    calls = []

    async def cancelled(*_):
        calls.append(True)
        raise asyncio.CancelledError()

    wrapped = StudioNodeTypes(studio).wrapper(
        cancelled, {"id": "cancel", "kind": "tool", "retries": 3}, "run-one", {}
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(wrapped(None))
    assert len(calls) == 1
    assert [
        event["status"] for event in studio.execution.snapshot("run-one", None)["timeline"]
    ] == ["running", "cancelled"]


def test_trace_completion_survives_interrupted_canonical_event_write(studio):
    saved_run(studio)
    studio.execution.record(
        "run-one", "node", node_id="one", source_id="node-one", status="running"
    )
    studio._write(
        studio._path("node-traces", "node-one"),
        {"id": "node-one", "run_id": "run-one", "node_id": "one", "status": "completed"},
    )
    assert [
        event["status"] for event in studio.execution.snapshot("run-one", None)["timeline"]
    ] == ["running", "completed"]


def wait_status(studio, run_id, expected):
    for _ in range(300):
        record = studio.run(run_id)
        if record["status"] not in {"queued", "running"}:
            assert record["status"] == expected, record
            return record
        time.sleep(0.01)
    pytest.fail("Workflow did not reach its expected state")


@pytest.mark.parametrize("version", [1, 2])
def test_new_approval_run_has_no_fake_untimed_gate_completion(studio, version):
    graph = {
        "nodes": [
            {"id": "review", "kind": "approval", "name": "Review"},
            {
                "id": "count",
                "name": "Count",
                "kind": "tool",
                "tool_id": "text:wordcount",
                "args": ["two words"],
            },
        ],
        "edges": [{"source": "START", "target": "review"}, {"source": "review", "target": "count"}],
    }
    if version == 2:
        graph["version"] = 2
    workflow = studio.save_definition(
        "workflows", {"name": f"Approval replay {version}", "graph": graph}
    )
    run = studio.start_run(
        {"kind": "workflow", "workflow_id": workflow["id"], "task": "Count after approval"}
    )
    wait_status(studio, run["id"], "waiting")
    pending = studio.execution.snapshot(run["id"], None)
    assert pending["coverage"]["missing_timestamps"] == 0
    assert all(
        "node_status" not in event
        for event in pending["timeline"]
        if event["kind"] == "approval" and event["status"] == "waiting"
    )
    studio.graph.resume(run["id"], "approve")
    wait_status(studio, run["id"], "completed")
    snapshot = studio.execution.snapshot(run["id"], None)
    gate = "root/review" if version == 2 else "review"
    resolved = [
        event
        for event in snapshot["timeline"]
        if event["kind"] == "approval" and event["status"] == "resolved"
    ]
    assert len(resolved) == 1
    assert resolved[0]["node_id"] == gate and resolved[0]["node_status"] == "completed"
    assert not any(event["id"] == "checkpoint:" + gate for event in snapshot["timeline"])
    assert snapshot["coverage"]["missing_timestamps"] == 0
    assert all(event["at"] for event in snapshot["timeline"])


def test_rejected_approval_does_not_project_completed_node(studio):
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Rejected replay",
            "graph": {
                "nodes": [{"id": "review", "name": "Review", "kind": "approval"}],
                "edges": [{"source": "START", "target": "review"}],
            },
        },
    )
    run = studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Review"})
    wait_status(studio, run["id"], "waiting")
    studio.graph.resume(run["id"], "reject")
    snapshot = studio.execution.snapshot(run["id"], None)
    resolved = next(
        event
        for event in snapshot["timeline"]
        if event["kind"] == "approval" and event["status"] == "resolved"
    )
    assert resolved["node_status"] == "cancelled"
    assert "review" not in snapshot["states"]
    assert snapshot["coverage"]["missing_timestamps"] == 0


def test_recorded_branch_skips_do_not_gain_fake_untimed_checkpoints(studio):
    saved_run(studio)
    studio._write(
        studio._path("graph-runs", "run-one"),
        {
            "graph": {"nodes": [{"id": "branch", "kind": "tool"}], "edges": []},
            "nodes": {"branch": "skipped"},
        },
    )
    studio.execution.record("run-one", "branch", node_id="branch", status="skipped")
    snapshot = studio.execution.snapshot("run-one", None)
    assert len(snapshot["timeline"]) == 1
    assert snapshot["coverage"]["missing_timestamps"] == 0


def test_supplied_input_projects_completed_at_resolution_without_fake_checkpoint(studio):
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Input replay",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "answer",
                        "name": "Answer",
                        "kind": "input",
                        "output_schema": {"type": "string"},
                    }
                ],
            },
        },
    )
    run = studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Ask"})
    wait_status(studio, run["id"], "waiting")
    studio.graph.structured.resume_input(run["id"], {"root/answer": "private answer"})
    wait_status(studio, run["id"], "completed")
    snapshot = studio.execution.snapshot(run["id"], None)
    inputs = [event for event in snapshot["timeline"] if event["kind"] == "input"]
    assert [event["status"] for event in inputs] == ["waiting", "resolved"]
    assert "node_status" not in inputs[0]
    assert inputs[1]["node_status"] == "completed"
    assert snapshot["coverage"]["missing_timestamps"] == 0
    assert "private answer" not in json.dumps(snapshot)


def test_artifact_events_retain_positive_revision_and_legacy_snapshots_include_archived(studio):
    import base64

    artifact = studio.catalog.save(
        "artifacts",
        {
            "name": "Original",
            "data": {
                "filename": "result.txt",
                "content_base64": base64.b64encode(b"original").decode(),
            },
        },
    )
    saved_run(studio, artifact_id=artifact["id"])
    event = studio.execution.record(
        "run-one",
        "artifact",
        artifact_id=artifact["id"],
        artifact_revision=artifact["revision"],
        status="created",
    )
    studio.catalog.save(
        "artifacts",
        {
            "id": artifact["id"],
            "revision": artifact["revision"],
            "name": "Edited",
            "data": {
                "filename": "result.txt",
                "content_base64": base64.b64encode(b"edited").decode(),
            },
        },
    )
    snapshot = studio.execution.snapshot("run-one", None)
    pinned = next(row for row in snapshot["timeline"] if row["id"] == event["id"])
    assert pinned["artifact_revision"] == 1
    assert (
        studio.artifacts.get(studio.artifacts.revision(artifact["id"], pinned["artifact_revision"]))
        == b"original"
    )
    current = studio.catalog.get("artifacts", artifact["id"])
    archived = studio.catalog.archive(
        "artifacts", artifact["id"], {"revision": current["revision"]}
    )
    saved_run(studio, "legacy-artifact", artifact_id=artifact["id"])
    fallback = studio.execution.snapshot("legacy-artifact", None)["timeline"]
    assert len(fallback) == 1
    assert fallback[0]["artifact_revision"] == archived["revision"]


@pytest.mark.parametrize("revision", [None, 0, -1, True, "1", 1.2])
def test_artifact_revision_metadata_rejects_invalid_types_without_exposing_them(studio, revision):
    saved_run(studio)
    event = studio.execution.record(
        "run-one", "artifact", artifact_id="result", artifact_revision=revision
    )
    assert "artifact_revision" not in event


def test_chat_projects_recorded_invocation_graph_and_replay_state(tmp_path):
    from nexus.core.models import AgentResult

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=True, output="Answer", metadata={})

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        run = studio.start_run({"kind": "chat", "task": "Question", "provider": "codex"})
        wait_status(studio, run["id"], "completed")
        snapshot = studio.execution.snapshot(run["id"], None)
        graph = snapshot["graph"]
        assert graph["projection"] == "recorded-participants"
        assert [node["kind"] for node in graph["nodes"]] == ["run", "invocation"]
        invocation = graph["nodes"][1]
        assert snapshot["states"][invocation["id"]] == "completed"
        assert graph["edges"] == [
            {
                "source": "execution:run",
                "target": invocation["id"],
                "relationship": "run-membership",
            }
        ]
        events = [event for event in snapshot["timeline"] if event["kind"] == "invocation"]
        assert [event["status"] for event in events] == ["running", "completed"]
        assert all(event["graph_node_id"] == invocation["id"] for event in events)
    finally:
        studio.close()


def test_parallel_delegates_have_no_invented_sequence_or_invocation_parent(studio):
    saved_run(studio, kind="delegation")
    for agent in ("alpha", "beta"):
        studio.execution.record("run-one", "delegation", agent_id=agent, status="running")
        studio.execution.record(
            "run-one", "invocation", agent=agent, source_id="call-" + agent, status="running"
        )
    for agent in ("beta", "alpha"):
        studio.execution.record(
            "run-one", "invocation", agent=agent, source_id="call-" + agent, status="completed"
        )
        studio.execution.record("run-one", "delegation", agent_id=agent, status="completed")
    snapshot = studio.execution.snapshot("run-one", None)
    assert len(snapshot["graph"]["nodes"]) == 5
    assert len(snapshot["graph"]["edges"]) == 4
    assert all(
        edge["source"] == "execution:run" and edge["relationship"] == "run-membership"
        for edge in snapshot["graph"]["edges"]
    )
    assert "not execution order" in snapshot["graph"]["relationship_note"]


def test_tool_parent_edge_requires_explicit_unambiguous_recorded_reference(studio):
    saved_run(studio, kind="chat")
    studio.execution.record(
        "run-one", "invocation", agent="Assistant", source_id="parent-call", status="running"
    )
    studio.execution.record(
        "run-one",
        "tool",
        tool="text:wordcount",
        source_id="tool-call",
        parent_id="parent-call",
        status="completed",
    )
    snapshot = studio.execution.snapshot("run-one", None)
    nodes = {node["kind"]: node for node in snapshot["graph"]["nodes"]}
    assert {
        "source": nodes["invocation"]["id"],
        "target": nodes["tool"]["id"],
        "relationship": "recorded-parent",
    } in snapshot["graph"]["edges"]
    assert not any(
        edge["source"] == "execution:run" and edge["target"] == nodes["tool"]["id"]
        for edge in snapshot["graph"]["edges"]
    )
    studio.execution.record(
        "run-one",
        "invocation",
        agent="Other",
        source_id="other",
        agent_id="ambiguous",
        status="completed",
    )
    studio.execution.record("run-one", "delegation", agent_id="ambiguous", status="completed")
    studio.execution.record(
        "run-one",
        "tool",
        tool="other-tool",
        source_id="other-tool",
        parent_id="ambiguous",
        status="completed",
    )
    snapshot = studio.execution.snapshot("run-one", None)
    tool = next(node for node in snapshot["graph"]["nodes"] if node["name"] == "other-tool")
    assert {
        "source": "execution:run",
        "target": tool["id"],
        "relationship": "run-membership",
    } in snapshot["graph"]["edges"]


def test_legacy_invocation_traces_project_graph_without_inventing_time(studio):
    saved_run(studio, kind="chat")
    studio._write(
        studio._path("invocation-traces", "old-call"),
        {
            "id": "old-call",
            "run_id": "run-one",
            "agent": "Legacy",
            "provider": "codex",
            "status": "completed",
        },
    )
    snapshot = studio.execution.snapshot("run-one", None)
    assert snapshot["graph"]["nodes"][1]["name"] == "Legacy"
    assert snapshot["timeline"][0]["at"] is None
    assert snapshot["coverage"]["missing_timestamps"] == 1


def test_same_tool_in_distinct_recorded_calls_projects_distinct_participants(studio):
    saved_run(studio, kind="chat")
    for identity in ("tool-one", "tool-two"):
        studio._write(
            studio._path("tool-traces", identity),
            {"id": identity, "run_id": "run-one", "tool": "text:wordcount", "ok": True},
        )
    snapshot = studio.execution.snapshot("run-one", None)
    tools = [node for node in snapshot["graph"]["nodes"] if node["kind"] == "tool"]
    assert len(tools) == 2 and tools[0]["id"] != tools[1]["id"]
    assert len(snapshot["graph"]["edges"]) == 2
