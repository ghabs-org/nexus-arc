"""Cancellation is a saved workflow outcome, not replay or compensation."""

import asyncio
import copy
import threading
import time

import pytest

from nexus.core.studio import StudioService
from nexus.tools import tool_registry
from tests.test_studio_interop_workflows import apply
from tests.test_studio_team import team as team
from tests.test_studio_workflow_v2 import wait


def source(body="body", name="cancel-example", child="body-child", cleanup="cleanup-child"):
    return {
        "agent_name": name,
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "record"}],
        "on_exit": {
            "always": "always",
            "on_cancel": "cancel",
            "on_success": "success",
            "on_failure": "failure",
        },
        "nodes": [
            {"id": "body", "type": "workflow", "workflow_name": child}
            if body == "workflow"
            else {"id": "body", "type": "tool", "tool_name": "record", "input": {"label": body}},
            {"id": "always", "type": "workflow", "workflow_name": cleanup}
            if cleanup
            else {
                "id": "always",
                "type": "tool",
                "tool_name": "record",
                "input": {
                    "label": "always",
                    "status": "{{workflow.status}}",
                    "original": "{{workflow.input}}",
                },
            },
            *[
                {
                    "id": phase,
                    "type": "tool",
                    "tool_name": "record",
                    "input": {"label": phase, "status": "{{workflow.status}}"},
                }
                for phase in ("cancel", "success", "failure")
            ],
        ],
        "output_mapping": {"body": "{{body.output}}"},
    }


@pytest.fixture
def fixture(tmp_path):
    calls, started, cleaning = [], threading.Event(), threading.Event()

    async def record(label, **kwargs):
        calls.append((label, kwargs))
        if label == "slow":
            started.set()
            await asyncio.sleep(60)
        if label == "slow-cleanup":
            cleaning.set()
            await asyncio.sleep(0.15)
        if label == "broken":
            raise ValueError("Synthetic failure; no external account")
        return label

    tool_registry.register("interop:cancel-record", record, "Local cancellation fixture")
    studio = StudioService(tmp_path)
    try:
        yield studio, calls, started, cleaning
    finally:
        studio.close()
        tool_registry.unregister("interop:cancel-record", func=record)


def install(studio, document):
    return apply(studio, document, {"record": "interop:cancel-record"})


def child(studio, name="body-child", kind="approval", final="after-gate", owner=None):
    nodes = [
        {
            "id": "before",
            "kind": "tool",
            "tool_id": "interop:cancel-record",
            "kwargs": {"label": "before"},
        },
        {"id": "gate", "kind": kind, "depends_on": ["before"]},
        {
            "id": "after",
            "kind": "tool",
            "tool_id": "interop:cancel-record",
            "kwargs": {"label": final},
            "depends_on": ["gate"],
        },
    ]
    studio.save_definition(
        "workflows",
        {
            "name": name,
            **({"requester_nexus_id": owner} if owner else {}),
            "graph": {"version": 2, "nodes": nodes, "output_mapping": {"ref": "nodes.after"}},
        },
    )


def start(studio, name="cancel-example"):
    return studio.start_run(
        {
            "kind": "workflow",
            "workflow_id": name,
            "task": "Local cancellation",
            "workflow_input": {"topic": "local"},
        }
    )


def labels(calls):
    return [label for label, _ in calls]


@pytest.mark.parametrize("kind", ["approval", "input"])
@pytest.mark.parametrize("action", ["cancel", "reject"])
def test_paused_body_cleanup_without_body_replay(fixture, kind, action):
    studio, calls, _, _ = fixture
    child(studio, kind=kind)
    native = install(studio, source(body="workflow", cleanup=None))
    task = wait(studio, start(studio), "waiting")
    task = (
        studio.graph.resume(task["id"], "reject")
        if action == "reject" and kind == "approval"
        else studio.cancel_run(task["id"])
    )
    result = wait(studio, task, "cancelled")
    assert labels(calls) == ["before", "always", "cancel"]
    assert calls[1][1] == {"status": "cancelled", "original": {"topic": "local"}}
    assert result["cleanup_status"] == "completed"
    assert not result["awaiting_approval"] and not result["awaiting_input"]
    history = studio.execution.snapshot(result["id"], None)
    assert history["states"]["root/body"] == "cancelled"
    assert any(
        event.get("node_id") == "root/body/gate" and event["status"] == "cancelled"
        for event in history["timeline"]
    )
    exported = studio.configuration.export_interop({"document": native}, None)
    assert exported["valid"], exported
    assert (
        studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)["document"]
        == native
    )
    with pytest.raises(ValueError, match="terminal"):
        studio.cancel_run(result["id"])


def test_running_cancel_is_idempotent_and_waits_for_cleanup(fixture):
    studio, calls, started, cleaning = fixture
    document = source(body="slow", cleanup=None)
    document["nodes"][1]["input"]["label"] = "slow-cleanup"
    install(studio, document)
    task = start(studio)
    assert started.wait(3)
    studio.cancel_run(task["id"])
    assert cleaning.wait(3)
    assert studio.run(task["id"])["status"] == "running"
    for _ in range(5):
        studio.cancel_run(task["id"])
    result = wait(studio, task, "cancelled")
    assert labels(calls) == ["slow", "slow-cleanup", "cancel"]
    assert result["cleanup_status"] == "completed"


@pytest.mark.parametrize("action", ["approve", "reject", "stop"])
@pytest.mark.parametrize("kind", ["approval", "input"])
def test_cleanup_gates_survive_restart_and_never_grant_body_approval(
    fixture, tmp_path, action, kind
):
    studio, calls, _, _ = fixture
    child(studio)
    child(studio, "cleanup-child", kind=kind, final="cleanup-approved")
    install(studio, source(body="workflow"))
    task = wait(studio, start(studio), "waiting")
    cleanup = wait(studio, studio.cancel_run(task["id"]), "waiting")
    assert cleanup["cleanup_status"] == "waiting"
    assert labels(calls) == ["before", "before"]
    studio.close()
    # Both body and cleanup definitions are pinned, even when removed before restart.
    (tmp_path / "workflows" / "body-child.yaml").unlink()
    (tmp_path / "workflows" / "cleanup-child.yaml").unlink()
    restarted = StudioService(tmp_path)
    try:
        if action == "approve":
            if kind == "approval":
                resumed = restarted.graph.resume(task["id"], "approve")
            else:
                resumed = restarted.graph.structured.resume_input(
                    task["id"], {cleanup["awaiting_input"][0]["id"]: "allowed"}
                )
        elif action == "reject" and kind == "approval":
            resumed = restarted.graph.resume(task["id"], "reject")
        else:
            resumed = restarted.cancel_run(task["id"])
        result = wait(restarted, resumed, "cancelled")
        assert labels(calls) == [
            "before",
            "before",
            *(["cleanup-approved"] if action == "approve" else []),
            "cancel",
        ]
        assert bool(result.get("workflow_warnings")) == (action != "approve")
        assert result["cleanup_status"] == "completed"
        assert not result["awaiting_approval"] and not result["awaiting_input"]
        if action != "approve":
            assert any(
                event.get("kind") == "cleanup" and event.get("status") == "skipped"
                for event in restarted.execution.snapshot(task["id"], None)["timeline"]
            )
    finally:
        restarted.close()


def test_nested_cleanup_runs_deepest_first_and_completed_siblings_stay_closed(fixture):
    studio, calls, _, _ = fixture
    child(studio)
    nested = source(body="workflow", name="nested", cleanup=None)
    install(studio, nested)
    completed = source(body="complete", name="finished", cleanup=None)
    install(studio, completed)
    outer = source(body="workflow", child="nested", cleanup=None)
    outer["nodes"].insert(0, {"id": "done", "type": "workflow", "workflow_name": "finished"})
    outer["nodes"][1]["depends_on"] = ["done"]
    install(studio, outer)
    task = wait(studio, start(studio), "waiting")
    baseline = copy.deepcopy(calls)
    assert labels(baseline) == ["complete", "always", "success", "before"]
    result = wait(studio, studio.cancel_run(task["id"]), "cancelled")
    assert labels(calls[len(baseline) :]) == ["always", "cancel", "always", "cancel"]
    progress = studio._read(studio._path("graph-runs", task["id"]))["cancellation"]
    assert progress["done"] == ["root/body", "root"]
    assert not result.get("workflow_warnings")


def test_map_cancellation_keeps_each_entered_item_context(fixture):
    studio, calls, _, _ = fixture
    child(studio)
    install(studio, source(body="workflow", name="item", cleanup=None))
    outer = source(cleanup=None)
    outer["nodes"][0] = {
        "id": "body",
        "type": "map",
        "node": "entry",
        "items": "{{workflow.input.items}}",
        "concurrency_limit": 2,
    }
    outer["nodes"].append(
        {
            "id": "entry",
            "type": "workflow",
            "workflow_name": "item",
            "input": {"value": "{{_map_item}}"},
        }
    )
    install(studio, outer)
    task = wait(
        studio,
        studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": "cancel-example",
                "task": "Map fixture",
                "workflow_input": {"items": ["a", "b", "c"]},
            }
        ),
        "waiting",
    )
    result = wait(studio, studio.cancel_run(task["id"]), "cancelled")
    cleanup = [kwargs["original"] for label, kwargs in calls if label == "always"]
    assert cleanup == [{"value": "a"}, {"value": "b"}, {"value": "c"}, {"items": ["a", "b", "c"]}]
    assert labels(calls).count("cancel") == 4
    assert not result.get("workflow_warnings")


def test_cleanup_failure_visible_without_changing_cancelled_outcome(fixture):
    studio, calls, _, _ = fixture
    child(studio)
    document = source(body="workflow", cleanup=None)
    document["nodes"][1]["input"]["label"] = "broken"
    install(studio, document)
    task = wait(studio, start(studio), "waiting")
    result = wait(studio, studio.cancel_run(task["id"]), "cancelled")
    assert labels(calls) == ["before", "broken", "cancel"]
    assert result["workflow_warnings"] == [
        {"node_id": "root/interop_exit/always", "error_type": "ValueError"}
    ]


def test_shutdown_is_interruption_and_does_not_start_cancellation_handlers(fixture):
    studio, calls, started, _ = fixture
    install(studio, source(body="slow", cleanup=None))
    task = start(studio)
    assert started.wait(3)
    studio.close()
    assert studio.run(task["id"])["status"] == "interrupted"
    assert labels(calls) == ["slow"]


def test_cancellation_during_cleanup_shutdown_exposes_incomplete_progress(fixture):
    studio, calls, started, cleaning = fixture
    document = source(body="slow", cleanup=None)
    document["nodes"][1]["input"]["label"] = "slow-cleanup"
    install(studio, document)
    task = start(studio)
    assert started.wait(3)
    studio.cancel_run(task["id"])
    assert cleaning.wait(3)
    studio.close()
    result = studio.run(task["id"])
    assert result["status"] == "interrupted" and result["cleanup_status"] == "interrupted"
    assert labels(calls) == ["slow", "slow-cleanup"]


def test_explicit_interrupted_cleanup_continuation_never_replays_uncertain_tool(fixture, tmp_path):
    studio, calls, started, cleaning = fixture
    document = source(body="slow", cleanup=None)
    document["nodes"][1]["input"]["label"] = "slow-cleanup"
    install(studio, document)
    task = start(studio)
    assert started.wait(3)
    studio.cancel_run(task["id"])
    assert cleaning.wait(3)
    studio.close()
    restarted = StudioService(tmp_path)
    try:
        result = wait(restarted, restarted.cancel_run(task["id"]), "cancelled")
        assert labels(calls) == ["slow", "slow-cleanup", "cancel"]
        assert result["cleanup_status"] == "completed"
        assert result["workflow_warnings"] == [
            {"node_id": "root/interop_exit/always", "error_type": "CleanupEffectUncertainError"}
        ]
    finally:
        restarted.close()


def test_pre_feature_checkpoint_root_cleanup_is_explicit_about_missing_nested_captures(fixture):
    studio, calls, _, _ = fixture
    child(studio)
    install(studio, source(body="workflow", cleanup=None))
    task = wait(studio, start(studio), "waiting")
    path = studio._path("graph-runs", task["id"])
    state = studio._read(path)
    state.pop("exit_scopes")
    studio._write(path, state)
    result = wait(studio, studio.cancel_run(task["id"]), "cancelled")
    assert labels(calls) == ["before", "always", "cancel"]
    assert result["workflow_warnings"][0]["error_type"] == "LegacyScopeCapturesUnavailable"


def test_cancel_queued_resumption_reuses_existing_job(fixture):
    studio, calls, _, _ = fixture
    child(studio)
    install(studio, source(body="workflow", cleanup=None))
    task = wait(studio, start(studio), "waiting")
    # This case targets an already queued continuation. A visible pause may still
    # have its prior worker finalizing; that handoff has separate race coverage.
    deadline = time.monotonic() + 2
    while task["id"] in studio.active_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    assert task["id"] not in studio.active_tasks
    submitted = []
    submit = studio.executor.submit
    studio.executor.submit = lambda *args: submitted.append(args)
    try:
        studio.graph.resume(task["id"], "approve")
        assert len(submitted) == 1
        studio.cancel_run(task["id"])
        assert len(submitted) == 1
    finally:
        studio.executor.submit = submit
    callback, *args = submitted[0]
    callback(*args)
    result = studio.run(task["id"])
    assert result["status"] == "cancelled"
    assert labels(calls) == ["before", "always", "cancel"]


def test_shared_http_cancel_and_cleanup_preserve_owner_identity(team):
    from nexus.core.studio_connections import execution_identity

    studio, _, _, request, _ = team
    studio.team.allowed_tools.add("interop:cancel-record")
    identities = []

    async def record(**kwargs):
        identities.append(execution_identity.get())
        return "local"

    tool_registry.register("interop:cancel-record", record, "Local identity fixture")
    try:
        child(studio, owner="alice")
        code, converted = request(
            "/api/studio/configuration/interop/convert",
            {
                "documents": [source(body="workflow", cleanup=None)],
                "bindings": {"tools": {"record": "interop:cancel-record"}},
            },
            user="owner",
        )
        assert code == 200 and converted["valid"], converted
        graph = converted["document"]["definitions"][0]["document"]["graph"]
        code, _ = request(
            "/api/workflows",
            {"name": "cancel-example", "graph": graph},
            user="alice",
        )
        assert code == 201
        code, task = request(
            "/api/runs",
            {"kind": "workflow", "workflow_id": "cancel-example", "task": "Private cancellation"},
            user="alice",
        )
        assert code == 202
        wait(studio, task, "waiting")
        assert request(f"/api/studio/runs/{task['id']}/cancel", {}, user="bob")[0] == 403
        assert request(f"/api/studio/runs/{task['id']}/cancel", {}, user="viewer")[0] == 403
        assert request(f"/api/studio/runs/{task['id']}/cancel", {}, user="alice")[0] == 202
        assert wait(studio, task, "cancelled")["cleanup_status"] == "completed"
        assert identities == ["alice", "alice", "alice"]
        assert request(f"/api/studio/runs/{task['id']}/execution", user="bob")[0] == 403
    finally:
        tool_registry.unregister("interop:cancel-record", func=record)


def test_shared_sql_remote_stop_does_not_reinterrupt_cleanup(tmp_path):
    from tests.test_studio_durable import options

    calls, started, cleaning = [], threading.Event(), threading.Event()

    async def record(label, **kwargs):
        calls.append(label)
        if label == "slow":
            started.set()
            await asyncio.sleep(60)
        if label == "slow-cleanup":
            cleaning.set()
            await asyncio.sleep(1.2)
        return label

    tool_registry.register("interop:cancel-record", record, "SQL cancellation fixture")
    worker = StudioService(tmp_path, state_options=options(tmp_path, workers=1))
    remote = StudioService(tmp_path, state_options=options(tmp_path, workers=0))
    worker.executor.lease_seconds = 1.5
    try:
        document = source(body="slow", cleanup=None)
        document["nodes"][1]["input"]["label"] = "slow-cleanup"
        install(worker, document)
        task = start(remote)
        assert started.wait(3)
        remote.cancel_run(task["id"])
        assert cleaning.wait(3)
        for _ in range(5):
            remote.cancel_run(task["id"])
        result = wait(remote, task, "cancelled")
        assert result["cleanup_status"] == "completed"
        assert calls == ["slow", "slow-cleanup", "cancel"]
    finally:
        remote.close()
        worker.close()
        tool_registry.unregister("interop:cancel-record", func=record)
