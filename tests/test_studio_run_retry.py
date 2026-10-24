"""Activity recovery exercises the real workflow engines and persistence."""

import time

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_team import team  # noqa: F401 -- shared authenticated HTTP fixture


def stopped(service, run):
    for _ in range(800):
        with service.lock:
            current = service.run(run["id"])
            request = service.sql_state.job_request(run["id"]) if service.sql_state else None
            if (
                current["status"] not in {"running", "queued"}
                and run["id"] not in service.active_tasks
                and (not request or request["status"] != "running")
            ):
                return current
        time.sleep(0.01)
    pytest.fail("Run did not stop")


@pytest.fixture(params=["files", "sqlite"])
def app(tmp_path, request):
    calls = []
    fail = {"beta": True}

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append((context.agent_name, context.prompt, context.effort))
            if fail.get(context.agent_name) and (
                not fail.get("match") or fail["match"] in context.prompt
            ):
                return AgentResult(success=False, output="", error="Fixture provider failed")
            return AgentResult(success=True, output=context.agent_name)

    options = (
        {"url": f"sqlite:///{tmp_path}/state.sqlite", "namespace": "retry-test", "workers": 1}
        if request.param == "sqlite"
        else None
    )
    service = StudioService(tmp_path, provider_factory=lambda _: Provider(), state_options=options)
    for name in ["alpha", "beta", "gamma"]:
        service.save_definition(
            "agents", {"name": name, "provider": "openai", "instructions": "Use supplied input."}
        )
    yield service, calls, fail
    service.close()


def workflow(service, form="v2", policy=False):
    if form == "yaml":
        return service.save_definition(
            "workflows",
            {
                "name": "Retry test",
                "steps": [
                    {"name": name, "agent_type": agent, "prompt": "{task}", "retry": 0}
                    for name, agent in [("First", "alpha"), ("Second", "beta"), ("Third", "gamma")]
                ],
            },
        )
    graph = {
        "nodes": [
            {
                "id": name,
                "name": name.title(),
                "kind": "agent",
                "agent_id": agent,
                "prompt": "{task}",
            }
            for name, agent in [("first", "alpha"), ("second", "beta"), ("third", "gamma")]
        ],
        "edges": [
            {"source": "START", "target": "first"},
            {"source": "first", "target": "second"},
            {"source": "second", "target": "third"},
        ],
    }
    if form == "v2":
        graph["version"] = 2
    if policy:
        graph["nodes"][1]["retry_policy"] = {
            "limit": 0,
            "policy": "OnError",
            "backoff": {"seconds": 0, "factor": 1, "max_seconds": 0},
        }
    return service.save_definition("workflows", {"name": "Retry test", "graph": graph})


def retry(service, run, step_id=None, **extra):
    plan = service.retry.describe(run["id"])
    assert plan["can_retry"], plan
    selected = step_id or plan["steps"][0]["id"]
    payload = {"digest": plan["digest"], "step_id": selected, **extra}
    return stopped(service, service.retry.queue(run["id"], payload)), payload


@pytest.mark.parametrize("form", ["v1", "v2", "yaml"])
def test_activity_retry_continues_workflow_without_repeating_completed_steps(app, form):
    service, calls, fail = app
    definition = workflow(service, form)
    run = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    assert run["status"] == "failed"
    plan = service.retry.describe(run["id"])
    assert len(plan["steps"]) == 1, plan
    assert plan["steps"][0]["id"] in {"second", "root/second", "step-2"}
    with pytest.raises(ValueError, match="changed"):
        service.retry.queue(run["id"], {"digest": "stale", "step_id": plan["steps"][0]["id"]})
    with pytest.raises(ValueError, match="failed step"):
        service.retry.queue(run["id"], {"digest": plan["digest"], "step_id": "root/first"})
    fail["beta"] = False
    finished, payload = retry(service, run)
    assert finished["status"] == "completed", finished
    assert [c[0] for c in calls] == ["alpha", "beta", "beta", "gamma"]
    assert service.retry.queue(run["id"], payload)["status"] == "completed"
    assert len(calls) == 4
    assert len(finished["step_retries"]) == 1
    assert not service.retry.describe(run["id"])["can_retry"]


def test_explicit_retry_clears_terminal_budget_and_sticky_failed_outcome(app):
    service, calls, fail = app
    definition = workflow(service, policy=True)
    run = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    state = service._read(service._path("graph-runs", run["id"]))
    assert state["outcome"]["status"] == "failed"
    assert state["retry_attempts"]["root/second"]["terminal"] == "failed"
    fail["beta"] = False
    finished, _ = retry(service, run, "root/second")
    assert finished["status"] == "completed", finished
    assert [c[0] for c in calls] == ["alpha", "beta", "beta", "gamma"]


def test_nested_map_retries_failed_item_and_retains_successful_items(app):
    service, calls, fail = app
    graph = {
        "version": 2,
        "nodes": [
            {
                "id": "each",
                "kind": "map",
                "items": ["one", "two"],
                "concurrency": 1,
                "body": {
                    "version": 2,
                    "nodes": [
                        {"id": "check", "kind": "agent", "agent_id": "beta", "prompt": "{task}"}
                    ],
                },
            }
        ],
    }
    definition = service.save_definition("workflows", {"name": "Map recovery", "graph": graph})
    fail["match"] = "two"
    run = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    fail["beta"] = False
    finished, _ = retry(service, run, "root/each/1/check")
    assert finished["status"] == "completed", finished
    assert len(calls) == 3
    assert (
        service._read(service._path("graph-runs", run["id"]))["nodes"]["root/each/1/check"]
        == "completed"
    )


def test_failed_tool_requires_effect_review_and_does_not_repeat_completed_tool(app):
    from nexus.tools import tool_registry

    service, _, _ = app
    calls = []
    fail = [True]

    def tool(label):
        calls.append(label)
        if label == "second" and fail[0]:
            raise RuntimeError("Failed tool")
        return label

    name = "retry-test:tool"
    tool_registry.register(name, tool, category="Testing")
    try:
        graph = {
            "version": 2,
            "nodes": [
                {"id": key, "kind": "tool", "tool_id": name, "kwargs": {"label": key}}
                for key in ["first", "second"]
            ],
            "edges": [
                {"source": "START", "target": "first"},
                {"source": "first", "target": "second"},
            ],
        }
        definition = service.save_definition("workflows", {"name": "Tool recovery", "graph": graph})
        run = stopped(
            service,
            service.start_run(
                {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
            ),
        )
        plan = service.retry.describe(run["id"])
        assert plan["steps"][0]["requires_effect_review"]
        with pytest.raises(ValueError, match="external effects"):
            service.retry.queue(run["id"], {"digest": plan["digest"], "step_id": "root/second"})
        assert calls == ["first", "second"]
        fail[0] = False
        finished, _ = retry(service, run, "root/second", confirm_effects=True)
        assert finished["status"] == "completed", finished
        assert calls == ["first", "second", "second"]
    finally:
        tool_registry.unregister(name, func=tool)


def test_uncertain_integration_requires_existing_review_before_retry(app):
    service, calls, fail = app
    definition = workflow(service)
    run = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    effect = {"id": "uncertain", "run_id": run["id"], "status": "attempting", "owner_id": None}
    service._write(service._path("integration-effects", "uncertain"), effect)
    plan = service.retry.describe(run["id"])
    assert not plan["can_retry"] and "uncertain" in plan["blocked_reason"]
    with pytest.raises(ValueError, match="uncertain"):
        service.retry.queue(
            run["id"], {"digest": plan["digest"], "step_id": "root/second", "confirm_effects": True}
        )
    assert len(calls) == 2
    effect["status"] = "reviewed-retry"
    service._write(service._path("integration-effects", "uncertain"), effect)
    fail["beta"] = False
    assert retry(service, run)[0]["status"] == "completed"


def test_failed_chat_reuses_original_effort_and_replaces_its_failed_turn(app):
    service, calls, fail = app
    run = stopped(
        service,
        service.start_run(
            {"kind": "chat", "agent_id": "beta", "task": "Evidence", "effort": "high"}
        ),
    )
    assert run["status"] == "failed"
    assert len(service.session(run["session_id"])["messages"]) == 2
    fail["beta"] = False
    finished, _ = retry(service, run)
    assert finished["status"] == "completed"
    session = service.session(run["session_id"])
    assert len(session["messages"]) == 2
    assert all(m.get("status") != "failed" for m in session["messages"])
    assert [c[2] for c in calls] == ["high", "high"]


def test_activity_retry_does_not_guess_old_standalone_request(app):
    service, _, _ = app
    service._write(
        service._path("runs", "legacy"),
        {
            "id": "legacy",
            "kind": "build",
            "status": "failed",
            "task": "Original",
            "provider": "codex",
        },
    )
    plan = service.retry.describe("legacy")
    assert not plan["can_retry"]
    assert "saved retry request" in plan["blocked_reason"]


def test_authenticated_activity_retry_rechecks_ownership_and_execution_role(team):  # noqa: F811
    service, policy, manager, request, calls = team

    class Broken:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=False, output="", error="Fixture failure")

    original = policy.provider
    policy.provider = lambda *args, **kwargs: Broken()
    code, queued = request("/api/runs", {"task": "Private retry"}, user="alice")
    assert code == 202
    run = stopped(service, queued)
    route = f"/api/studio/runs/{run['id']}/retry"
    assert request(route)[0] == 401
    assert request(route, user="bob")[0] == 403
    code, plan = request(route, user="alice")
    assert code == 200 and plan["can_retry"]
    payload = {"digest": plan["digest"], "step_id": "__run__"}
    assert request(route, payload, user="bob")[0] == 403
    assert request(route, payload, user="owner")[0] == 403
    policy.bindings["alice"] = "viewer"
    assert request(route, payload, user="alice")[0] == 403
    policy.bindings["alice"] = "builder"
    manager.revoked.add("alice")
    assert request(route, payload, user="alice")[0] == 401
    manager.revoked.remove("alice")
    policy.provider = original
    code, resumed = request(route, payload, user="alice")
    assert code == 202, resumed
    assert stopped(service, resumed)["status"] == "completed"
    assert len(calls) == 1


def test_failed_build_retries_original_request_with_effort_and_no_duplicate_dispatch(app):
    from tests.test_quick_build import Model

    service, _, _ = app
    model = Model()
    model.success = False
    service.builder.provider_factory = lambda _: model
    run = stopped(
        service,
        service.start_run(
            {"kind": "build", "task": "Create a team", "provider": "claude-code", "effort": "high"}
        ),
    )
    assert run["status"] == "failed"
    model.success = True
    finished, payload = retry(service, run)
    assert finished["status"] == "completed", finished
    assert service.builder.get(run["build_id"])["effort"] == "high"
    assert len(model.calls) == 2
    assert service.retry.queue(run["id"], payload)["status"] == "completed"
    assert len(model.calls) == 2


def test_failed_handler_can_retry_without_repeating_failed_body(app):
    service, calls, fail = app
    fail["gamma"] = True
    graph = {
        "version": 2,
        "nodes": [{"id": "body", "kind": "agent", "agent_id": "beta", "prompt": "{task}"}],
        "on_error": {
            "version": 2,
            "nodes": [{"id": "handle", "kind": "agent", "agent_id": "gamma", "prompt": "{task}"}],
        },
    }
    definition = service.save_definition("workflows", {"name": "Handler recovery", "graph": graph})
    run = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    assert run["status"] == "failed"
    fail["gamma"] = False
    still_failed, _ = retry(service, run, "on_error/handle")
    assert still_failed["status"] == "failed"
    assert [call[0] for call in calls] == ["beta", "gamma", "gamma"]
    fail["beta"] = False
    completed, _ = retry(service, still_failed, "root/body")
    assert completed["status"] == "completed", completed
    assert [call[0] for call in calls] == ["beta", "gamma", "gamma", "beta"]


def test_full_nested_step_paths_survive_bounded_execution_labels(app):
    service, calls, fail = app
    graph = {
        "version": 2,
        "nodes": [{"id": "deepcheck", "kind": "agent", "agent_id": "beta", "prompt": "{task}"}],
    }
    for index in range(5):
        graph = {
            "version": 2,
            "nodes": [
                {"id": chr(97 + index) * 60, "kind": "map", "items": ["item"], "body": graph}
            ],
        }
    definition = service.save_definition(
        "workflows", {"name": "Long scope recovery", "graph": graph}
    )
    run = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    plan = service.retry.describe(run["id"])
    step = next(step for step in plan["steps"] if step["id"].endswith("/deepcheck"))
    assert len(step["id"]) > 240
    fail["beta"] = False
    completed, _ = retry(service, run, step["id"])
    assert completed["status"] == "completed", completed
    assert len(calls) == 2


def test_enqueue_failure_restores_checkpoint_and_does_not_claim_other_active_session(
    app, monkeypatch
):
    service, _, _ = app
    definition = workflow(service)
    run = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    path = service._path("graph-runs", run["id"])
    before = service._read(path)
    plan = service.retry.describe(run["id"])

    def reject(*args):
        raise RuntimeError("Queue unavailable")

    monkeypatch.setattr(service, "resume_workflow", reject)
    with pytest.raises(RuntimeError, match="Queue unavailable"):
        service.retry.queue(run["id"], {"digest": plan["digest"], "step_id": "root/second"})
    assert service.run(run["id"])["status"] == "failed"
    assert service._read(path) == before
    assert service.workbench.checkpoint(run["id"], None)["state"] == before


def test_simultaneous_retry_clicks_queue_one_attempt(app):
    from concurrent.futures import ThreadPoolExecutor

    service, calls, fail = app
    definition = workflow(service)
    run = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    plan = service.retry.describe(run["id"])
    payload = {"digest": plan["digest"], "step_id": "root/second"}
    fail["beta"] = False
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: service.retry.queue(run["id"], payload), range(4)))
    assert {receipt["id"] for receipt in receipts} == {run["id"]}
    assert stopped(service, receipts[0])["status"] == "completed"
    assert [call[0] for call in calls] == ["alpha", "beta", "beta", "gamma"]


def test_retry_does_not_remove_another_chat_runs_active_session_guard(app):
    service, _, _ = app
    run = stopped(
        service, service.start_run({"kind": "chat", "agent_id": "beta", "task": "Evidence"})
    )
    service.active_sessions.add(run["session_id"])
    plan = service.retry.describe(run["id"])
    with pytest.raises(ValueError, match="already has a running task"):
        service.retry.queue(run["id"], {"digest": plan["digest"], "step_id": "__run__"})
    assert run["session_id"] in service.active_sessions


def test_retry_preserves_completed_approval_and_waits_for_new_gate(app):
    service, calls, fail = app
    graph = {
        "version": 2,
        "nodes": [
            {"id": "before", "kind": "approval"},
            {"id": "first", "kind": "agent", "agent_id": "alpha", "prompt": "{task}"},
            {"id": "second", "kind": "agent", "agent_id": "beta", "prompt": "{task}"},
            {"id": "after", "kind": "approval"},
            {"id": "last", "kind": "agent", "agent_id": "gamma", "prompt": "{task}"},
        ],
        "edges": [
            {"source": "START", "target": "before"},
            {"source": "before", "target": "first"},
            {"source": "first", "target": "second"},
            {"source": "second", "target": "after"},
            {"source": "after", "target": "last"},
        ],
    }
    definition = service.save_definition("workflows", {"name": "Gated retry", "graph": graph})
    waiting = stopped(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
        ),
    )
    assert waiting["status"] == "waiting"
    run = stopped(service, service.graph.resume(waiting["id"], "approve"))
    assert run["status"] == "failed"
    fail["beta"] = False
    resumed, _ = retry(service, run, "root/second")
    assert resumed["status"] == "waiting", resumed
    assert resumed["awaiting_approval"][0]["id"] == "root/after"
    assert [call[0] for call in calls] == ["alpha", "beta", "beta"]
    state = service._read(service._path("graph-runs", run["id"]))
    assert state["nodes"]["root/before"] == "completed"
    completed = stopped(service, service.graph.resume(run["id"], "approve"))
    assert completed["status"] == "completed"
    assert [call[0] for call in calls] == ["alpha", "beta", "beta", "gamma"]


def test_archived_conversation_cannot_be_retried(app):
    service, _, _ = app
    run = stopped(
        service, service.start_run({"kind": "chat", "agent_id": "beta", "task": "Evidence"})
    )
    session = service.session(run["session_id"])
    session["archived"] = True
    service._write(service._path("sessions", session["id"]), session)
    plan = service.retry.describe(run["id"])
    with pytest.raises(ValueError, match="archived conversation"):
        service.retry.queue(run["id"], {"digest": plan["digest"], "step_id": "__run__"})
    assert service.run(run["id"])["status"] == "failed"


def test_selecting_a_parallel_agent_does_not_repeat_failed_sibling_tool_without_review(app):
    from nexus.tools import tool_registry

    service, _, fail = app
    attempts = []

    def tool():
        attempts.append("attempted")
        raise RuntimeError("Uncertain sibling tool")

    name = "retry-test:parallel-tool"
    tool_registry.register(name, tool, category="Testing")
    try:
        definition = service.save_definition(
            "workflows",
            {
                "name": "Parallel retry",
                "graph": {
                    "nodes": [
                        {
                            "id": "model",
                            "kind": "agent",
                            "agent_id": "beta",
                            "prompt": "{task}",
                            "name": "Model",
                        },
                        {"id": "tool", "kind": "tool", "tool_id": name, "name": "Tool"},
                    ],
                    "edges": [
                        {"source": "START", "target": "model"},
                        {"source": "START", "target": "tool"},
                    ],
                },
            },
        )
        run = stopped(
            service,
            service.start_run(
                {"kind": "workflow", "workflow_id": definition["id"], "task": "Evidence"}
            ),
        )
        plan = service.retry.describe(run["id"])
        assert all(step["requires_effect_review"] for step in plan["steps"])
        fail["beta"] = False
        with pytest.raises(ValueError, match="external effects"):
            service.retry.queue(run["id"], {"digest": plan["digest"], "step_id": "model"})
        assert attempts == ["attempted"]
    finally:
        tool_registry.unregister(name, func=tool)
