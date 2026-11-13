"""Real evaluation jobs exercise bounded dispatch, durable partials and owned cleanup."""

import asyncio
import threading
import time

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_evaluation_gate import check_report
from nexus.core.studio_evaluation_workers import current_evaluation_slot
from nexus.core.studio_experiments import validate_experiment
from nexus.core.studio_state import job_fence
from tests.test_studio_durable import options
from tests.test_studio_experiment_import import seed
from tests.test_studio_experiments import experiment, suite
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def agent(studio):
    studio.save_definition(
        "agents", {"name": "Reviewer", "instructions": "Review", "provider": "codex"}
    )


def settled(studio, run):
    run = done(studio, run)
    until = time.monotonic() + 5
    while run["id"] in studio.active_tasks and time.monotonic() < until:
        time.sleep(0.01)
    assert run["id"] not in studio.active_tasks
    return run


@pytest.mark.parametrize("workers", [0, 5, True, 1.5, "2", None])
def test_invalid_worker_counts_reject_before_execution(workers):
    with pytest.raises(ValueError, match="maxWorkers"):
        validate_experiment(
            {
                "evaluation_id": "suite",
                "evaluation_revision": 1,
                "variants": [{"name": "A"}],
                "maxWorkers": workers,
            }
        )


@pytest.mark.parametrize("workers", [2, 4])
@pytest.mark.parametrize("sql", [False, True])
def test_bounded_target_and_judge_pairs_keep_canonical_order_and_case_traces(
    tmp_path, workers, sql
):
    active, maximum, count = 0, 0, 0
    first_batch, later_finished = asyncio.Event(), asyncio.Event()
    completions, calls, fences = [], [], []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent_streaming(self, *args):
            pytest.fail("Parallel cases must not overwrite their shared parent's text stream")

        async def execute_agent(self, context):
            nonlocal active, maximum, count
            slot = current_evaluation_slot.get()
            assert slot
            calls.append(slot)
            fences.append(job_fence.get())
            judge = "Evaluate the answer using only the rubric" in context.prompt
            active += 1
            maximum = max(maximum, active)
            try:
                if judge:
                    assert "SECRET_REFERENCE" in context.prompt
                    if not slot.endswith("-0-0-repeat-0"):
                        later_finished.set()
                    completions.append(slot)
                    return AgentResult(success=True, output='{"score":1,"reason":"Matches"}')
                assert "SECRET_REFERENCE" not in context.prompt
                count += 1
                if count >= workers:
                    first_batch.set()
                await first_batch.wait()
                if slot.endswith("-0-0-repeat-0"):
                    await later_finished.wait()
                return AgentResult(success=True, output="Approved")
            finally:
                active -= 1

    studio = StudioService(
        tmp_path / "project",
        state_options=options(tmp_path, 2) if sql else None,
        provider_factory=lambda _: Provider(),
    )
    try:
        agent(studio)
        cases = [
            {
                "input": f"Question {i}",
                "contains": ["Approved"],
                "contexts": ["SECRET_REFERENCE"],
                "judge": {"provider": "codex", "rubric": "Grade"},
            }
            for i in range(4)
        ]
        saved = experiment(
            studio,
            suite(studio, cases=cases),
            maxWorkers=workers,
            runsPerExample=2,
            variants=[{"name": "A"}, {"name": "B"}],
        )
        run = settled(
            studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
        )
        report = studio._read(studio._path("evaluation-reports", run["id"]))
        assert run["status"] == "completed", run
        assert maximum == workers and active == 0 and len(calls) == 32
        assert len(set(fences)) == 1 and (fences[0] is not None) == sql
        assert report["max_workers"] == workers and report["acceptance"]
        assert report["dispatched_slots"] == list(range(16))
        expected = [(v, c, r) for v in (1, 2) for c in range(1, 5) for r in (1, 2)]
        assert [
            (r["variant_index"], r["case"], r["repetition"]) for r in report["results"]
        ] == expected
        assert not completions[0].endswith("-0-0-repeat-0")
        traces = studio.records("invocation-traces", limit=None)
        assert len(traces) == 32 and {trace["parent_id"] for trace in traces} == set(calls)
        assert not studio.run(run["id"]).get("stream") and check_report(report)["passed"]
    finally:
        studio.close()


def test_error_stops_new_dispatch_but_retains_other_inflight_result(tmp_path):
    second_started, failing = asyncio.Event(), asyncio.Event()
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            if context.prompt.endswith("Question 0"):
                await second_started.wait()
                failing.set()
                return AgentResult(success=False, error="Synthetic provider failure")
            second_started.set()
            await failing.wait()
            return AgentResult(success=True, output="Approved")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        agent(studio)
        saved = experiment(
            studio,
            suite(
                studio,
                cases=[{"input": f"Question {i}", "contains": ["Approved"]} for i in range(5)],
            ),
            maxWorkers=2,
            runsPerExample=1,
        )
        run = settled(
            studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
        )
        report = studio._read(studio._path("evaluation-reports", run["id"]))
        assert run["status"] == "failed" and len(calls) == 2
        assert report["total"] == 2 and report["passed"] == 1
        assert report["dispatched_slots"] == [0, 1]
        assert report["evaluation_status"] == "error" and report["acceptance"] is None
        assert not check_report(report)["passed"]
    finally:
        studio.close()


@pytest.mark.parametrize("sql", [False, True])
@pytest.mark.parametrize("stop", ["cancel", "shutdown"])
def test_cancel_and_restart_retain_results_and_never_replay_uncertain_slots(tmp_path, sql, stop):
    two_pending = threading.Event()
    active, cleaned, calls = 0, 0, []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            nonlocal active, cleaned
            calls.append(context.prompt)
            if context.prompt.endswith("Question 0"):
                return AgentResult(success=True, output="Approved")
            active += 1
            if active == 2:
                two_pending.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                active -= 1
                cleaned += 1

    settings = options(tmp_path, 1) if sql else None
    root = tmp_path / "project"
    studio = StudioService(root, state_options=settings, provider_factory=lambda _: Provider())
    agent(studio)
    saved = experiment(
        studio,
        suite(
            studio, cases=[{"input": f"Question {i}", "contains": ["Approved"]} for i in range(6)]
        ),
        maxWorkers=2,
        runsPerExample=1,
    )
    run = studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
    try:
        assert two_pending.wait(5)
        before = studio._read(studio._path("evaluation-reports", run["id"]))
        assert before["total"] == 1 and before["dispatched_slots"] == [0, 1, 2]
        if stop == "cancel":
            stopped = settled(studio, studio.cancel_run(run["id"]))
            assert stopped["status"] == "cancelled"
    finally:
        studio.close()
    assert active == 0 and cleaned == 2 and len(calls) == 3
    restored = StudioService(root, state_options=settings, provider_factory=lambda _: Provider())
    try:
        assert restored.run(run["id"])["status"] == (
            "cancelled" if stop == "cancel" else "interrupted"
        )
        report = restored._read(restored._path("evaluation-reports", run["id"]))
        assert report["results"] == before["results"] and not report["complete"]
        assert report["acceptance"] is None and not check_report(report)["passed"]
        # Even accidental executor reentry cannot replay a dispatched target or judge.
        with pytest.raises(RuntimeError, match="automatic replay is disabled"):
            asyncio.run(restored.operations.evaluate(run["id"], {}))
        assert len(calls) == 3
        assert (
            restored._read(restored._path("evaluation-reports", run["id"]))["results"]
            == before["results"]
        )
    finally:
        restored.close()


def test_workflow_workers_reject_before_queue_and_import_preserves_agent_workers(tmp_path):
    studio = StudioService(tmp_path)
    try:
        data = seed(studio)
        data["experiment"]["spec"]["maxWorkers"] = 4
        report = studio.configuration.convert_experiment(data, None)
        assert report["document"]["resources"][1]["data"]["maxWorkers"] == 4
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        exported = studio.configuration.export(None)
        assert (
            next(item for item in exported["resources"] if item["kind"] == "experiments")["data"][
                "maxWorkers"
            ]
            == 4
        )
        studio.save_definition(
            "workflows",
            {
                "name": "Gated",
                "graph": {"version": 2, "nodes": [{"id": "gate", "kind": "approval"}]},
            },
        )
        saved = experiment(
            studio, suite(studio, target_kind="workflow", target_id="gated"), maxWorkers=2
        )
        with pytest.raises(ValueError, match="workflow suites require"):
            studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
        assert not studio.records("runs")
    finally:
        studio.close()


def test_queued_worker_limit_is_pinned_and_caller_settings_cannot_expand_it(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    try:
        agent(studio)
        saved = experiment(studio, suite(studio), maxWorkers=2)
        queued = []
        monkeypatch.setattr(studio.executor, "submit", lambda *args: queued.append(args))
        studio.start_run(
            {
                "kind": "evaluation",
                "experiment_id": saved["id"],
                "maxWorkers": 4,
                "_experiment_settings": {"workers": 4},
            }
        )
        studio.catalog.save("experiments", {**saved, "data": {**saved["data"], "maxWorkers": 1}})
        pinned = queued[0][2]
        assert pinned["_experiment_settings"]["workers"] == 2
        assert pinned["experiment_snapshot"]["data"]["maxWorkers"] == 2
        ad_hoc = {
            "evaluation_id": saved["data"]["evaluation_id"],
            "_experiment_settings": {"workers": 4},
        }
        studio.operations.evaluation_request(ad_hoc)
        assert ad_hoc["_experiment_settings"]["workers"] == 1
    finally:
        studio.close()


def test_http_owner_revision_and_csrf_rules_apply_to_worker_configuration(team):
    studio, _, _, request, calls = team
    selected = suite(studio)
    payload = {
        "name": "Parallel",
        "data": {
            "evaluation_id": selected["id"],
            "evaluation_revision": selected["revision"],
            "maxWorkers": 4,
            "variants": [{"name": "A"}],
        },
    }
    route = "/api/catalog/experiments"
    assert request(route, payload)[0] == 401
    assert request(route, payload, user="viewer")[0] == 403
    assert request(route, payload, user="owner", csrf="bad")[0] == 403
    status, saved = request(route, payload, user="owner")
    assert status == 201 and saved["data"]["maxWorkers"] == 4
    assert not calls and not studio.records("runs")
