"""Experiment pauses resume the current case through existing workflow checkpoints."""

import copy
import threading
import time

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_experiments import checkpoint_id
from tests.test_studio_durable import options
from tests.test_studio_experiments import experiment, suite
from tests.test_studio_interop_cancellation import (
    child,
    install,
    source,
)
from tests.test_studio_interop_cancellation import (
    fixture as fixture,
)
from tests.test_studio_resources import app as app
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def settle(studio, run):
    """Wait for the paused worker to release its checkpoint before restarting Studio."""
    run = done(studio, run)
    until = time.monotonic() + 5
    while run["id"] in studio.active_tasks and time.monotonic() < until:
        time.sleep(0.01)
    assert run["id"] not in studio.active_tasks
    return run


def workflow(studio, style="v2"):
    if style == "yaml":
        return studio.save_definition(
            "workflows",
            {
                "name": "Reviewed cases",
                "steps": [
                    {"id": "prepare", "agent_type": "reviewer"},
                    {"id": "finish", "agent_type": "reviewer", "require_human_approval": True},
                ],
            },
        )
    if style == "v1":
        graph = {
            "nodes": [
                {
                    "id": "prepare",
                    "name": "Prepare",
                    "kind": "agent",
                    "agent_id": "reviewer",
                    "prompt": "{task}",
                },
                {"id": "gate", "name": "Gate", "kind": "approval"},
                {
                    "id": "finish",
                    "name": "Finish",
                    "kind": "agent",
                    "agent_id": "reviewer",
                    "prompt": "{task}",
                },
            ],
            "edges": [
                {"source": "START", "target": "prepare"},
                {"source": "prepare", "target": "gate"},
                {"source": "gate", "target": "finish"},
            ],
        }
    else:
        graph = {
            "version": 2,
            "nodes": [
                {"id": "prepare", "kind": "agent", "agent_id": "reviewer"},
                {"id": "gate", "kind": "approval", "depends_on": ["prepare"]},
                {
                    "id": "answer",
                    "kind": "input",
                    "depends_on": ["gate"],
                    "output_schema": {"type": "integer", "minimum": 1},
                },
                {
                    "id": "finish",
                    "kind": "agent",
                    "agent_id": "reviewer",
                    "depends_on": ["answer"],
                },
            ],
            "output_mapping": {"ref": "nodes.finish"},
        }
    return studio.save_definition("workflows", {"name": "Reviewed cases", "graph": graph})


def launch(studio, selected, *, repeats=2, variants=None, cases=None):
    saved = experiment(
        studio,
        suite(studio, target_kind="workflow", target_id=selected["id"], cases=cases),
        runsPerExample=repeats,
        variants=variants or [{"name": "Baseline"}],
    )
    return studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})


def report(studio, run):
    return studio._read(studio._path("evaluation-reports", run["id"]))


def approve(studio, run):
    return studio.graph.resume(run["id"], "approve", checkpoint=run["evaluation_checkpoint"])


def answer(studio, run, value=7):
    return studio.graph.structured.resume_input(
        run["id"],
        {run["awaiting_input"][0]["id"]: value},
        checkpoint=run["evaluation_checkpoint"],
    )


@pytest.mark.parametrize("style", ["v1", "v2", "yaml"])
def test_every_case_and_repeat_requires_its_own_decision_without_replaying_prior_nodes(app, style):
    studio, calls = app
    selected = workflow(studio, style)
    run = settle(
        studio,
        launch(
            studio,
            selected,
            variants=[{"name": "A"}, {"name": "B"}],
            cases=[
                {"input": "First", "contains": ["Evidence"]},
                {"input": "Second", "contains": ["Evidence"]},
            ],
        ),
    )
    previous = None
    for ordinal in range(8):
        assert run["status"] == "waiting" and run["awaiting_approval"], run
        assert report(studio, run)["total"] == ordinal
        assert not report(studio, run)["complete"] and report(studio, run)["acceptance"] is None
        if previous:
            with pytest.raises(ValueError, match="checkpoint changed"):
                studio.graph.resume(run["id"], "approve", checkpoint=previous)
        previous = run["evaluation_checkpoint"]
        run = settle(studio, approve(studio, run))
        if style == "v2":
            assert run["status"] == "waiting" and run["awaiting_input"]
            run = settle(studio, answer(studio, run))
    result = report(studio, run)
    assert run["status"] == "completed", run
    assert result["complete"] and result["acceptance"]
    assert result["total"] == result["passed"] == 8
    assert len(calls) == 16
    assert [(r["variant"], r["case"], r["repetition"]) for r in result["results"]] == [
        (variant, case, repeat) for variant in ("A", "B") for case in (1, 2) for repeat in (1, 2)
    ]
    execution = studio.execution.snapshot(run["id"], None)
    assert execution["graph"]["nodes"]
    cases = [
        event
        for event in execution["timeline"]
        if event["kind"] == "evaluation-case" and event["status"] == "completed"
    ]
    assert len(cases) == 8 and len({event["node_id"] for event in cases}) == 8


def test_bad_input_and_stale_approval_leave_pending_case_unchanged(app):
    studio, calls = app
    run = settle(studio, launch(studio, workflow(studio)))
    with pytest.raises(ValueError, match="checkpoint changed"):
        studio.graph.resume(run["id"], "approve")
    old_token = run["evaluation_checkpoint"]
    run = settle(studio, approve(studio, run))
    before = studio._read(studio._path("graph-runs", checkpoint_id(studio, run["id"])))
    with pytest.raises(ValueError):
        answer(studio, run, "wrong type")
    with pytest.raises(ValueError, match="checkpoint changed"):
        studio.graph.resume(run["id"], "approve", checkpoint=old_token)
    assert studio._read(studio._path("graph-runs", checkpoint_id(studio, run["id"]))) == before
    assert studio.run(run["id"])["status"] == "waiting"
    assert len(calls) == 1 and report(studio, run)["total"] == 0


def test_review_inbox_contains_current_case_definition_and_rejects_old_repetition(app):
    studio, _ = app
    run = settle(studio, launch(studio, workflow(studio, "v1")))
    item = studio.workbench.inbox.items(None)[0]
    assert len(item["detail"]["workflow"]["graph"]["nodes"]) == 3
    decision = {"digest": item["digest"], "decision": "approve", "confirm": True}
    run = settle(studio, studio.workbench.inbox.review(item["id"], decision, None))
    assert run["status"] == "waiting" and report(studio, run)["total"] == 1
    with pytest.raises(ValueError, match="changed"):
        studio.workbench.inbox.review(item["id"], decision, None)
    latest = studio.workbench.inbox.items(None)[0]
    run = settle(
        studio,
        studio.workbench.inbox.review(latest["id"], {**decision, "digest": latest["digest"]}, None),
    )
    assert run["status"] == "completed" and report(studio, run)["acceptance"]


@pytest.mark.parametrize("sql", [False, True])
@pytest.mark.parametrize("pause", ["approval", "input"])
def test_restart_retains_completed_cases_and_pinned_workflow_without_new_provider_calls(
    app, tmp_path, sql, pause
):
    original, calls = app
    root = tmp_path / "durable" if sql else original.root
    settings = options(tmp_path, 1) if sql else None
    studio = (
        StudioService(root, state_options=settings, provider_factory=original.provider_factory)
        if sql
        else original
    )
    try:
        if sql:
            studio.save_definition(
                "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Review"}
            )
        selected = workflow(studio)
        run = settle(studio, launch(studio, selected))
        run = settle(studio, approve(studio, run))
        run = settle(studio, answer(studio, run))
        assert report(studio, run)["total"] == 1 and len(calls) == 3
        if pause == "input":
            run = settle(studio, approve(studio, run))
        changed = {
            "name": "Reviewed cases",
            "revision": selected["revision"],
            "graph": copy.deepcopy(studio.definition("workflows", selected["id"])["graph"]),
        }
        changed["graph"] = {"version": 2, "nodes": [{"id": "new_gate", "kind": "approval"}]}
        studio.update_definition("workflows", selected["id"], changed)
        studio.close()
        restored = StudioService(
            root, state_options=settings, provider_factory=original.provider_factory
        )
        try:
            resumed = restored.run(run["id"])
            assert resumed["status"] == "waiting"
            if pause == "approval":
                resumed = settle(restored, approve(restored, resumed))
            resumed = settle(restored, answer(restored, resumed))
            assert resumed["status"] == "completed", resumed
            assert report(restored, resumed)["passed"] == 2 and len(calls) == 4
        finally:
            restored.close()
    finally:
        if sql:
            studio.close()


@pytest.mark.parametrize("action", ["reject", "cancel"])
def test_reject_or_cancel_stops_future_repeats_and_never_accepts_partial_report(app, action):
    studio, calls = app
    run = settle(studio, launch(studio, workflow(studio)))
    stopped = (
        studio.cancel_run(run["id"])
        if action == "cancel"
        else studio.graph.resume(run["id"], "reject", checkpoint=run["evaluation_checkpoint"])
    )
    stopped = settle(studio, stopped)
    assert stopped["status"] == "cancelled", stopped
    assert len(calls) == 1 and report(studio, run)["total"] == 0
    assert not report(studio, run)["complete"] and report(studio, run)["acceptance"] is None


def test_nested_map_gates_use_current_case_checkpoint(app):
    studio, calls = app
    child = studio.save_definition(
        "workflows",
        {
            "name": "Nested review",
            "graph": {
                "version": 2,
                "nodes": [
                    {"id": "prepare", "kind": "agent", "agent_id": "reviewer"},
                    {"id": "gate", "kind": "approval", "depends_on": ["prepare"]},
                ],
                "output_mapping": {"ref": "nodes.prepare"},
            },
        },
    )
    selected = studio.save_definition(
        "workflows",
        {
            "name": "Batch review",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "batch",
                        "kind": "map",
                        "items": ["one", "two"],
                        "body": {
                            "version": 2,
                            "nodes": [
                                {"id": "child", "kind": "workflow", "workflow_id": child["id"]}
                            ],
                            "output_mapping": {"ref": "nodes.child"},
                        },
                    },
                ],
                "output_mapping": {"ref": "nodes.batch"},
            },
        },
    )
    run = settle(studio, launch(studio, selected))
    for _ in range(2):
        assert run["status"] == "waiting" and len(run["awaiting_approval"]) == 2, run
        run = settle(studio, approve(studio, run))
    assert run["status"] == "completed" and report(studio, run)["passed"] == 2
    assert len(calls) == 4


def test_http_continuations_enforce_owner_csrf_input_schema_and_checkpoint_token(team):
    studio, policy, _, request, _ = team
    selected = workflow(studio)
    saved = experiment(studio, suite(studio, target_kind="workflow", target_id=selected["id"]))
    code, queued = request(
        "/api/runs", {"kind": "evaluation", "experiment_id": saved["id"]}, user="bob"
    )
    assert code == 202
    run = settle(studio, queued)
    route = "/api/runs/" + run["id"]
    body = {"decision": "approve", "evaluation_checkpoint": run["evaluation_checkpoint"]}
    assert request(route + "/resume", body)[0] == 401
    assert request(route + "/resume", body, user="bob", csrf="wrong")[0] == 403
    for user in ("alice", "viewer"):
        assert request(route + "/resume", body, user=user)[0] == 403
    assert request(route + "/resume", {"decision": "approve"}, user="bob")[0] == 400
    code, resumed = request(route + "/resume", body, user="bob")
    assert code == 202, resumed
    run = settle(studio, resumed)
    assert request(route + "/resume", body, user="bob")[0] == 400
    input_body = {
        "values": {run["awaiting_input"][0]["id"]: 7},
        "evaluation_checkpoint": run["evaluation_checkpoint"],
    }
    input_route = "/api/studio/runs/" + run["id"] + "/input"
    assert request(input_route, input_body, user="alice")[0] == 403
    policy.bindings["bob"] = "viewer"
    assert request(input_route, input_body, user="bob")[0] == 403
    policy.bindings["bob"] = "operator"
    code, resumed = request(input_route, input_body, user="bob")
    assert code == 202, resumed
    assert settle(studio, resumed)["status"] == "waiting"
    assert report(studio, run)["total"] == 1


@pytest.mark.parametrize("kind", ["approval", "input"])
def test_cancellation_cleanup_can_pause_and_resume_without_granting_case_gate(fixture, kind):
    studio, calls, _, _ = fixture
    child(studio)
    child(studio, "cleanup-child", kind=kind, final="cleanup-approved")
    install(studio, source(body="workflow"))
    run = settle(studio, launch(studio, {"id": "cancel-example"}))
    run = settle(studio, studio.cancel_run(run["id"]))
    assert run["status"] == "waiting" and run["cleanup_status"] == "waiting", run
    run = settle(studio, approve(studio, run) if kind == "approval" else answer(studio, run))
    assert run["status"] == "cancelled" and run["cleanup_status"] == "completed", run
    assert [label for label, _ in calls] == ["before", "before", "cleanup-approved", "cancel"]
    assert report(studio, run)["total"] == 0 and report(studio, run)["acceptance"] is None
    assert report(studio, run)["evaluation_status"] == "cancelled"


@pytest.mark.parametrize("sql", [False, True])
def test_decision_waits_for_finishing_worker_before_parent_experiment_continues(
    app, tmp_path, monkeypatch, sql
):
    original, calls = app
    studio = (
        StudioService(
            tmp_path / "sql-race",
            state_options=options(tmp_path, 1),
            provider_factory=original.provider_factory,
        )
        if sql
        else original
    )
    finishing, release = threading.Event(), threading.Event()
    update = studio._update_run

    def hold(identity, **values):
        update(identity, **values)
        if values.get("status") == "waiting" and not finishing.is_set():
            finishing.set()
            assert release.wait(5)

    monkeypatch.setattr(studio, "_update_run", hold)
    try:
        if sql:
            studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
        run = launch(studio, workflow(studio))
        assert finishing.wait(5)
        run = studio.run(run["id"])
        queued = approve(studio, run)
        assert queued["status"] == "queued"
        assert studio._path("job-resumptions", run["id"]).exists()
        release.set()
        run = settle(studio, queued)
        assert run["status"] == "waiting" and run["awaiting_input"], run
        assert len(calls) == 1 and report(studio, run)["total"] == 0
    finally:
        release.set()
        if sql:
            studio.close()
