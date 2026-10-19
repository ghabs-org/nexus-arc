"""SQL transactions, shared workers, lease fencing and checkpoint recovery."""

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_state import LostLeaseError, StudioSQLState, job_fence


def options(tmp_path, workers=0):
    return {
        "url": os.getenv("NEXUS_STUDIO_TEST_DATABASE_URL")
        or "sqlite:///" + str(tmp_path / "state.sqlite"),
        "namespace": "test-" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:24],
        "workers": workers,
    }


def done(app, run):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        value = app.run(run["id"])
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(0.02)
    pytest.fail("Durable job did not finish")


def test_queued_job_survives_controller_shutdown_and_another_process_consumes(tmp_path):
    app = StudioService(tmp_path / "project", state_options=options(tmp_path))
    app.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    run = app.start_run({"agent_id": "reviewer", "task": "Persist me"})
    assert app.run(run["id"])["status"] == "queued"
    app.close()

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=True, output="Durable output")

    worker = StudioService(
        tmp_path / "project",
        state_options=options(tmp_path, workers=2),
        provider_factory=lambda _: Provider(),
    )
    try:
        result = done(worker, run)
        assert result["status"] == "completed", result
        assert result["output"] == "Durable output"
        assert len(worker.session(result["session_id"])["messages"]) == 2
    finally:
        worker.close()


def test_claim_is_exclusive_and_expired_attempt_is_never_replayed(tmp_path, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr("nexus.core.studio_state.time.time", lambda: clock[0])
    one = StudioSQLState(options(tmp_path)["url"], options(tmp_path)["namespace"])
    two = StudioSQLState(options(tmp_path)["url"], options(tmp_path)["namespace"])
    one.put("runs", "run", {"id": "run", "status": "queued"})
    one.enqueue("run", {"task": "Review"}, "session")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda state: state.claim(0.05), [one, two]))
    claimed = [r for r in results if r]
    assert len(claimed) == 1
    token = job_fence.set(("run", claimed[0]["token"]))
    try:
        one.put("output", "before", {"safe": True})
        clock[0] += 0.07
        with pytest.raises(LostLeaseError):
            one.put("output", "after", {"stale": True})
    finally:
        job_fence.reset(token)
    assert two.reap() == 1
    assert one.get("runs", "run")["status"] == "interrupted"
    assert one.claim(60) is None
    assert not one.heartbeat("run", claimed[0]["token"], 60)
    one.engine.dispose()
    two.engine.dispose()


def test_sql_checkpoint_approval_resumes_in_a_new_service(tmp_path):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=True, output="Approved")

    app = StudioService(
        tmp_path / "project",
        state_options=options(tmp_path, 1),
        provider_factory=lambda _: Provider(),
    )
    app.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    app.save_definition(
        "workflows",
        {
            "name": "Approval",
            "graph": {
                "nodes": [
                    {"id": "gate", "name": "Approve", "kind": "approval"},
                    {"id": "review", "name": "Review", "agent_id": "reviewer", "prompt": "{task}"},
                ],
                "edges": [
                    {"source": "START", "target": "gate"},
                    {"source": "gate", "target": "review"},
                ],
            },
        },
    )
    run = done(
        app, app.start_run({"kind": "workflow", "workflow_id": "approval", "task": "Review"})
    )
    assert run["status"] == "waiting", run
    snapshot = app.workbench.checkpoint(run["id"], None)
    app.close()
    worker = StudioService(
        tmp_path / "project",
        state_options=options(tmp_path, 1),
        provider_factory=lambda _: Provider(),
    )
    try:
        resumed = worker.workbench.resume_checkpoint(
            run["id"], {"digest": snapshot["digest"], "decision": "approve"}, None
        )
        result = done(worker, resumed)
        assert result["status"] == "completed", result
        assert "Approved" in result["output"]
    finally:
        worker.close()


def test_project_lock_serializes_revision_updates(tmp_path):
    states = [
        StudioSQLState(options(tmp_path)["url"], options(tmp_path)["namespace"]) for _ in range(2)
    ]
    states[0].put("settings", "counter", {"value": 0})

    def update(state):
        for _ in range(20):
            with state.lock:
                data = state.get("settings", "counter")
                data["value"] += 1
                state.put("settings", "counter", data)

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(update, states))
    assert states[0].get("settings", "counter") == {"value": 40}
    for state in states:
        state.engine.dispose()


def test_sql_yaml_gate_and_runtime_survive_restart(tmp_path):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=True, output="YAML approved")

    app = StudioService(
        tmp_path / "project",
        state_options=options(tmp_path, 1),
        provider_factory=lambda _: Provider(),
    )
    app.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    app.save_definition(
        "workflows",
        {
            "name": "YAML gate",
            "steps": [{"id": "review", "agent_type": "reviewer", "require_human_approval": True}],
        },
    )
    run = done(
        app, app.start_run({"kind": "workflow", "workflow_id": "yaml-gate", "task": "Review"})
    )
    assert run["status"] == "waiting", run
    app.close()
    worker = StudioService(
        tmp_path / "project",
        state_options=options(tmp_path, 1),
        provider_factory=lambda _: Provider(),
    )
    try:
        run = done(worker, worker.graph.resume(run["id"], "approve"))
        assert run["status"] == "completed", run
        assert "YAML approved" in run["output"]
    finally:
        worker.close()
