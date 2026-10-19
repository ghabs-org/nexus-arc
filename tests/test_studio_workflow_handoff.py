"""Approval continuations wait for quiescence; expired workers remain fenced."""

import json
import threading
import time

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_state import LostLeaseError, job_fence
from nexus.tools import tool_registry
from tests.test_studio_durable import options
from tests.test_studio_workflow_v2 import wait


def approval_workflow(studio):
    return studio.save_definition(
        "workflows",
        {
            "name": "Handoff",
            "graph": {
                "version": 2,
                "nodes": [
                    {"id": "gate", "kind": "approval"},
                    {
                        "id": "count",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": "one two"},
                        "depends_on": ["gate"],
                    },
                ],
                "output_mapping": {"ref": "nodes.count"},
            },
        },
    )


def execute_claimed(studio, job):
    fence = job_fence.set((job["run_id"], job["token"]))
    try:
        studio._execute(job["run_id"], job["payload"], job["session_id"])
    finally:
        job_fence.reset(fence)


def test_sql_pause_boundary_has_one_continuation_and_preserves_fence(tmp_path):
    studio = StudioService(tmp_path / "workspace", state_options=options(tmp_path))
    try:
        workflow = approval_workflow(studio)
        run = studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Count"})
        store = studio.sql_state
        first = store.claim(60)
        execute_claimed(studio, first)
        assert studio.run(run["id"])["status"] == "waiting"
        studio.graph.resume(run["id"], "approve")
        assert store.get("job-resumptions", run["id"])["source_token"] == first["token"]
        assert store.claim(60) is None
        store.finish(run["id"], "wrong-token")
        assert store.claim(60) is None
        store.finish(run["id"], first["token"])
        assert store.get("job-resumptions", run["id"], missing=True) is None
        second = store.claim(60)
        assert second["token"] != first["token"] and second["payload"]["resume_graph"] is True
        fence = job_fence.set((run["id"], first["token"]))
        try:
            with pytest.raises(LostLeaseError):
                store.put("runs", run["id"], {"status": "unsafe"})
        finally:
            job_fence.reset(fence)
        execute_claimed(studio, second)
        store.finish(run["id"], second["token"])
        assert studio.run(run["id"])["output"] == "2"
        assert store.claim(60) is None
    finally:
        studio.close()


def test_expired_handoff_never_automatically_replays_work(tmp_path, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("nexus.core.studio_state.time.time", lambda: clock[0])
    studio = StudioService(tmp_path / "workspace", state_options=options(tmp_path))
    try:
        workflow = approval_workflow(studio)
        run = studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Count"})
        store = studio.sql_state
        original = store.claim(60)
        execute_claimed(studio, original)
        studio.graph.resume(run["id"], "approve")
        clock[0] += 61
        assert store.reap() == 1
        assert studio.run(run["id"])["status"] == "interrupted"
        store.finish(run["id"], original["token"])
        assert store.claim(60) is None
        # An explicit operator recovery can reuse approved checkpoints afterward.
        studio._update_run(run["id"], status="queued")
        payload = {**original["payload"], "resume_graph": True}
        studio.executor.submit(studio._execute, run["id"], payload, "")
        assert store.get("job-resumptions", run["id"], missing=True) is None
        recovered = store.claim(60)
        execute_claimed(studio, recovered)
        store.finish(run["id"], recovered["token"])
        assert studio.run(run["id"])["output"] == "2"
    finally:
        studio.close()


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_published_pause_waits_for_finishing_attempt(tmp_path, backend, monkeypatch):
    published, release, resumed = threading.Event(), threading.Event(), threading.Event()
    calls = []

    async def work(label):
        calls.append(label)
        if label == "resumed":
            resumed.set()
        return label

    tool_registry.register("interop:handoff-effect", work, "Local handoff ordering fixture")
    studio = StudioService(
        tmp_path / "workspace",
        state_options=None if backend == "files" else options(tmp_path, workers=1),
    )
    update = studio._update_run

    def hold_pause(identity, **values):
        update(identity, **values)
        if values.get("status") == "waiting" and not published.is_set():
            published.set()
            release.wait(timeout=8)

    monkeypatch.setattr(studio, "_update_run", hold_pause)
    try:
        workflow = studio.save_definition(
            "workflows",
            {
                "name": "Concurrent handoff",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {
                            "id": "work",
                            "kind": "tool",
                            "tool_id": "interop:handoff-effect",
                            "kwargs": {"label": "work"},
                        },
                        {"id": "gate", "kind": "approval", "depends_on": ["work"]},
                        {
                            "id": "after",
                            "kind": "tool",
                            "tool_id": "interop:handoff-effect",
                            "kwargs": {"label": "resumed"},
                            "depends_on": ["gate"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.after"},
                },
            },
        )
        run = studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Wait"})
        assert published.wait(5)
        wait(studio, run, "waiting")
        studio.graph.resume(run["id"], "approve")
        assert studio._path("job-resumptions", run["id"]).exists()
        assert not resumed.wait(0.05)
        release.set()
        assert json.loads(wait(studio, run)["output"]) == "resumed"
        assert calls == ["work", "resumed"]
    finally:
        release.set()
        studio.close()
        tool_registry.unregister("interop:handoff-effect", func=work)


def test_file_restart_discards_unfinished_handoff_without_replay(tmp_path):
    studio = StudioService(tmp_path)
    try:
        workflow = approval_workflow(studio)
        run = studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Wait"})
        paused = wait(studio, run, "waiting")
        # Persist the exact approval/receipt boundary, without starting continuation.
        with studio.lock:
            path = studio._path("graph-runs", run["id"])
            state = studio._read(path)
            key = paused["awaiting_approval"][0]["id"]
            state["nodes"][key] = "completed"
            state["outputs"][key] = {"approved": True}
            studio._write(path, state)
            studio._update_run(run["id"], status="waiting", awaiting_approval=[])
            payload = {**state["payload"], "resume_graph": True}
            studio._write(
                studio._path("job-resumptions", run["id"]),
                {
                    "format": "nexus-workflow-resumption/v1",
                    "run_id": run["id"],
                    "source_token": None,
                    "payload": payload,
                    "session_id": "",
                    "owner_id": None,
                },
            )
        studio.close()
        studio = StudioService(tmp_path)
        assert studio.run(run["id"])["status"] == "interrupted"
        assert not studio._path("job-resumptions", run["id"]).exists()
        assert studio._read(path)["nodes"].get("root/count") != "completed"
        # Only explicit recovery dispatches the approved continuation.
        studio._update_run(run["id"], status="queued")
        studio.resume_workflow(run["id"], payload)
        assert wait(studio, run)["output"] == "2"
    finally:
        studio.close()
