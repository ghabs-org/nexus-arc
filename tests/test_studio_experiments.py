"""Repeated regressions use existing jobs, isolated checkpoints and pinned grading plans."""

import asyncio
import copy
import threading

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio_experiments import summarize, validate_experiment
from tests.test_studio_resources import app as app
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def suite(studio, *, cases=None, target_kind="agent", target_id="reviewer"):
    return studio.catalog.save(
        "evaluations",
        {
            "name": "Repeat cases",
            "data": {
                "target_kind": target_kind,
                "target_id": target_id,
                "cases": cases or [{"input": "Review evidence", "contains": ["Evidence"]}],
            },
        },
    )


def experiment(studio, selected, **settings):
    return studio.catalog.save(
        "experiments",
        {
            "name": "Repeat regression",
            "data": {
                "evaluation_id": selected["id"],
                "evaluation_revision": selected["revision"],
                "runsPerExample": 3,
                "variants": [{"name": "Baseline"}],
                **settings,
            },
        },
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "wrong"},
        {"version": 2},
        {"version": True},
        {"extra": 1},
        {"evaluation_revision": 0},
        {"evaluation_revision": True},
        {"evaluation_id": None},
        {"runsPerExample": True},
        {"runsPerExample": 0},
        {"runsPerExample": 21},
        {"runsPerExample": 1.5},
        {"passThreshold": True},
        {"passThreshold": -0.1},
        {"passThreshold": float("nan")},
        {"variants": []},
        {"variants": [{"name": "A"}, {"name": "A"}]},
        {"variants": [{"name": "A", "provider": "unknown"}]},
    ],
)
def test_invalid_saved_plans_never_pass_validation(changes):
    with pytest.raises(ValueError):
        validate_experiment(
            {
                "evaluation_id": "suite",
                "evaluation_revision": 1,
                "variants": [{"name": "A"}],
                **changes,
            }
        )


def test_missing_results_cannot_finalize_even_with_zero_threshold():
    report = {
        "variants": [{"name": "Baseline"}],
        "expected_total": 2,
        "results": [],
        "pass_threshold": 0,
    }
    summarize(report, complete=True)
    assert report["complete"] is False and report["acceptance"] is None
    assert report["variant_results"][0]["accepted"] is None


def test_repeats_run_in_order_with_independent_variant_thresholds(app):
    studio, calls = app
    selected = suite(studio)
    saved = experiment(studio, selected, variants=[{"name": "A"}, {"name": "B"}])
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert run["status"] == "completed" and report["complete"] and report["acceptance"]
    assert report["expected_total"] == report["total"] == report["passed"] == len(calls) == 6
    assert [(r["variant"], r["repetition"]) for r in report["results"]] == [
        ("A", 1),
        ("A", 2),
        ("A", 3),
        ("B", 1),
        ("B", 2),
        ("B", 3),
    ]
    assert report["experiment_revision"] == 1 and all(
        v["accepted"] for v in report["variant_results"]
    )


def test_weak_variant_cannot_hide_behind_aggregate_pass_rate(app):
    studio, _ = app
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(
                success=True,
                output="No support" if context.model_override == "weak" else "Evidence",
            )

    studio.provider_factory = lambda _: Provider()
    saved = experiment(
        studio,
        suite(studio),
        passThreshold=0.5,
        variants=[{"name": "Strong"}, {"name": "Weak", "model": "weak"}],
    )
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert run["status"] == "failed" and "pass threshold" in run["error"]
    assert (
        report["complete"]
        and report["acceptance"] is False
        and report["passed"] / report["total"] == 0.5
    )
    assert [v["accepted"] for v in report["variant_results"]] == [True, False]
    assert len(calls) == 6


def test_runtime_failure_stops_repetitions_and_never_accepts_partial_report(app):
    studio, _ = app
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return (
                AgentResult(success=False, error="Synthetic quota failure")
                if len(calls) == 2
                else AgentResult(success=True, output="Evidence")
            )

    studio.provider_factory = lambda _: Provider()
    saved = experiment(studio, suite(studio), passThreshold=0)
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert run["status"] == "failed" and len(calls) == report["total"] == 2
    assert (
        report["expected_total"] == 3
        and report["complete"] is False
        and report["acceptance"] is None
    )
    assert (
        report["evaluation_status"] == "error" and report["variant_results"][0]["accepted"] is None
    )


def test_queued_plan_is_pinned_and_new_launch_rejects_stale_suite(app, monkeypatch):
    studio, calls = app
    selected = suite(studio)
    saved = experiment(studio, selected)
    queued, submit = [], studio.executor.submit
    monkeypatch.setattr(studio.executor, "submit", lambda *args: queued.append(args))
    run = studio.start_run(
        {
            "kind": "evaluation",
            "experiment_id": saved["id"],
            "experiment_snapshot": {"id": "FORGED"},
            "_experiment_settings": {"repeats": 20},
        }
    )
    changed = copy.deepcopy(selected["data"])
    changed["cases"][0]["contains"] = ["MISSING"]
    studio.catalog.save("evaluations", {**selected, "data": changed})
    with pytest.raises(ValueError, match="pinned revision"):
        studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
    studio.catalog.save("experiments", {**saved, "data": {**saved["data"], "runsPerExample": 1}})
    studio.catalog.archive("experiments", saved["id"], {"revision": 2})
    monkeypatch.setattr(studio.executor, "submit", submit)
    submit(*queued[0])
    assert done(studio, run)["status"] == "completed"
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert (
        report["passed"] == len(calls) == 3
        and report["suite_revision"] == report["experiment_revision"] == 1
    )
    assert report["experiment_id"] == saved["id"] and report["acceptance"]


def test_limits_and_forged_adhoc_settings_never_dispatch_extra_cases(app):
    studio, calls = app
    selected = suite(studio, cases=[{"input": "Review", "contains": ["Evidence"]}] * 100)
    saved = experiment(
        studio, selected, runsPerExample=2, variants=[{"name": str(i)} for i in range(4)]
    )
    with pytest.raises(ValueError, match="400"):
        studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
    for override in [{"evaluation_id": selected["id"]}, {"variants": [{"name": "Override"}]}]:
        with pytest.raises(ValueError, match="cannot override"):
            studio.start_run({"kind": "evaluation", "experiment_id": saved["id"], **override})
    assert studio.records("runs") == [] and calls == []
    payload = {
        "evaluation_id": selected["id"],
        "_experiment_settings": {"repeats": 20},
        "experiment_snapshot": {"id": "FORGED"},
    }
    studio.operations.evaluation_request(payload)
    assert payload["_experiment_settings"]["repeats"] == 1 and "experiment_snapshot" not in payload


def test_workflow_repetitions_use_distinct_real_checkpoints(app):
    studio, _ = app
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Word count",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "count",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": {"ref": "input"}},
                    }
                ],
                "output_mapping": {"ref": "nodes.count"},
            },
        },
    )
    selected = suite(
        studio,
        target_kind="workflow",
        target_id=workflow["id"],
        cases=[{"input": "one two", "contains": ["2"]}],
    )
    saved = experiment(studio, selected)
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    assert run["status"] == "completed", run
    checkpoints = studio.records("graph-runs")
    assert len(checkpoints) == 3
    assert all(studio._path("graph-runs", f"{run['id']}-0-0-repeat-{i}").exists() for i in range(3))


def test_cancelled_experiment_never_accepts_partial_results(app):
    studio, _ = app
    started, calls = threading.Event(), []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            if len(calls) == 2:
                started.set()
                await asyncio.Event().wait()
            return AgentResult(success=True, output="Evidence")

    studio.provider_factory = lambda _: Provider()
    saved = experiment(studio, suite(studio), passThreshold=0)
    run = studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
    assert started.wait(2)
    studio.cancel_run(run["id"])
    assert done(studio, run)["status"] == "cancelled"
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert report["total"] == 1 and report["acceptance"] is None and not report["complete"]
    assert len(calls) == 2


def test_interactive_workflow_pauses_before_any_repeated_effect(app):
    studio, calls = app
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Reviewed action",
            "graph": {"version": 2, "nodes": [{"id": "gate", "kind": "approval"}]},
        },
    )
    saved = experiment(studio, suite(studio, target_kind="workflow", target_id=workflow["id"]))
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    assert run["status"] == "waiting" and run["awaiting_approval"]
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert calls == [] and report["total"] == 0
    assert report["complete"] is False and report["acceptance"] is None
    assert report["evaluation_status"] == "waiting"


def test_configuration_import_preserves_experiment_and_suite_dependencies(app):
    studio, _ = app
    selected = suite(studio)
    data = {
        "evaluation_id": selected["id"],
        "evaluation_revision": selected["revision"],
        "variants": [{"name": "Baseline"}],
    }
    doc = {
        "format": "nexus-studio-config/v1",
        "definitions": [],
        "resources": [{"kind": "experiments", "id": "imported", "name": "Imported", "data": data}],
    }
    plan = studio.configuration.plan(doc, None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    saved = studio.catalog.get("experiments", "imported")
    assert saved["data"] == validate_experiment(data)
    assert studio.export_definitions()["catalog"]["experiments"][0]["id"] == "imported"
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": "imported"}))
    assert run["status"] == "completed"


def test_http_saved_experiments_share_definitions_but_not_run_reports(team):
    studio, _, manager, request, _ = team
    selected = suite(studio)
    path = "/api/catalog/experiments"
    data = {
        "evaluation_id": selected["id"],
        "evaluation_revision": 1,
        "variants": [{"name": "A"}],
        "runsPerExample": 2,
    }
    assert request(path)[0] == 401
    assert request(path, {"name": "Denied", "data": data}, user="bob")[0] == 403
    code, saved = request(path, {"name": "Shared experiment", "data": data}, user="alice")
    assert code == 201
    assert request(path, user="viewer")[1]["items"][0]["id"] == saved["id"]
    assert (
        request("/api/runs", {"kind": "evaluation", "experiment_id": saved["id"]}, user="viewer")[0]
        == 403
    )
    code, queued = request(
        "/api/runs", {"kind": "evaluation", "experiment_id": saved["id"]}, user="bob"
    )
    assert code == 202
    done(studio, queued)
    assert request("/api/evaluation-reports", user="alice")[1]["items"] == []
    assert len(request("/api/evaluation-reports", user="bob")[1]["items"]) == 1
    manager.revoked.add("bob")
    assert (
        request("/api/runs", {"kind": "evaluation", "experiment_id": saved["id"]}, user="bob")[0]
        == 401
    )
