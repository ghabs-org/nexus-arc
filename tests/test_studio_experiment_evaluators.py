"""All selected judges grade one answer, with pinned copies and strict release evidence."""

import asyncio
import copy
import threading

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_evaluation_gate import check_report
from nexus.core.studio_experiment_judges import copy_evaluator
from nexus.core.studio_experiments import validate_experiment
from tests.test_studio_durable import options
from tests.test_studio_experiment_continuation import approve, settle, workflow
from tests.test_studio_experiment_import import seed
from tests.test_studio_experiment_workers import settled
from tests.test_studio_experiments import experiment, suite
from tests.test_studio_resources import app as app
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def judges(studio):
    return [
        copy_evaluator(
            studio.catalog.save(
                "evaluators",
                {
                    "name": name,
                    "data": {"provider": "codex", "rubric": f"Grade {name}", "threshold": 0.8},
                },
            )
        )
        for name in ("Accuracy", "Clarity")
    ]


def selection(items):
    return {"evaluators": items, "primaryEvaluatorId": items[1]["id"], "evaluatorPolicy": "all"}


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_policy",
        "primary",
        "policy",
        "duplicate",
        "too_many",
        "empty",
        "revision",
        "judge",
        "unknown",
    ],
)
def test_invalid_selections_cannot_save_or_run(tmp_path, invalid):
    studio = StudioService(tmp_path)
    try:
        data = {
            "evaluation_id": "suite",
            "evaluation_revision": 1,
            "variants": [{"name": "A"}],
            **selection(judges(studio)),
        }
        if invalid == "missing_policy":
            del data["evaluatorPolicy"]
        elif invalid == "primary":
            data["primaryEvaluatorId"] = "missing"
        elif invalid == "policy":
            data["evaluatorPolicy"] = "primary"
        elif invalid == "duplicate":
            data["evaluators"].append(copy.deepcopy(data["evaluators"][0]))
        elif invalid == "too_many":
            data["evaluators"] = [{**data["evaluators"][0], "id": f"judge-{i}"} for i in range(5)]
        elif invalid == "empty":
            data["evaluators"] = []
        elif invalid == "revision":
            data["evaluators"][0]["revision"] = True
        elif invalid == "judge":
            data["evaluators"][0]["judge"]["provider"] = "unknown"
        else:
            data["evaluators"][0]["extra"] = True
        with pytest.raises(ValueError):
            validate_experiment(data)
        assert not studio.records("runs") and not studio.catalog.list("experiments")
    finally:
        studio.close()


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("weak", [False, True])
def test_each_judge_sees_same_answer_once_and_cannot_hide_behind_primary(app, workers, weak):
    studio, _ = app
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            await asyncio.sleep(0)
            if "Evaluate the answer using only the rubric" in context.prompt:
                assert "Approved" in context.prompt and "SECRET_REFERENCE" in context.prompt
                assert "OLD_JUDGE" not in context.prompt
                return AgentResult(
                    success=True,
                    output='{"score":0.2,"reason":"Weak"}'
                    if weak and "Grade Accuracy" in context.prompt
                    else '{"score":1,"reason":"Good"}',
                )
            assert "SECRET_REFERENCE" not in context.prompt
            return AgentResult(success=True, output="Approved")

    studio.provider_factory = lambda _: Provider()
    saved = experiment(
        studio,
        suite(
            studio,
            cases=[
                {
                    "input": "Question",
                    "contains": ["Approved"],
                    "contexts": ["SECRET_REFERENCE"],
                    "judge": {"provider": "codex", "rubric": "OLD_JUDGE"},
                }
            ],
        ),
        runsPerExample=2,
        maxWorkers=workers,
        **selection(judges(studio)),
    )
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert (
        len(calls) == 6
        and sum("Evaluate the answer using only the rubric" not in call for call in calls) == 2
    )
    assert report["acceptance"] is (not weak) and report["complete"]
    assert (run["status"] == "failed") == weak
    assert report["primaryEvaluatorId"] == "clarity" and report["evaluatorPolicy"] == "all"
    assert [
        (item["id"], item["passed"], item["total"], item["primary"])
        for item in report["evaluator_summaries"]
    ] == [("accuracy", 0 if weak else 2, 2, False), ("clarity", 2, 2, True)]
    assert all(len(entry["evaluators"]) == 2 for entry in report["results"])
    assert check_report(report)["passed"] is (not weak)


@pytest.mark.parametrize("failure", ["invalid", "runtime"])
def test_later_judge_failure_retains_prior_grade_and_never_passes_zero_threshold(app, failure):
    studio, _ = app
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            if "Grade Clarity" in context.prompt:
                return AgentResult(
                    success=failure != "runtime", output="bad grade", error="Synthetic failure"
                )
            return AgentResult(
                success=True,
                output='{"score":1,"reason":"Good"}'
                if "Evaluate the answer" in context.prompt
                else "Approved",
            )

    studio.provider_factory = lambda _: Provider()
    items = judges(studio)
    items.append({**copy.deepcopy(items[0]), "id": "third", "name": "Third"})
    saved = experiment(
        studio,
        suite(studio, cases=[{"input": "Question", "contains": ["Approved"]}]),
        runsPerExample=3,
        passThreshold=0,
        **selection(items),
    )
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert run["status"] == "failed" and len(calls) == 3
    entry = report["results"][0]
    assert entry["output"] == "Approved" and entry["evaluators"][0]["passed"]
    assert entry["evaluators"][1]["error"] and len(entry["evaluators"]) == 2
    assert not entry["passed"] and not report["complete"] and report["acceptance"] is None
    assert not check_report(report)["passed"]


def test_copied_judges_survive_source_edits_and_forged_queued_settings(app, monkeypatch):
    studio, calls = app
    items = judges(studio)
    saved = experiment(studio, suite(studio), runsPerExample=1, **selection(items))
    queued, submit = [], studio.executor.submit
    monkeypatch.setattr(studio.executor, "submit", lambda *args: queued.append(args))
    run = studio.start_run(
        {
            "kind": "evaluation",
            "experiment_id": saved["id"],
            "_experiment_settings": {"evaluators": []},
        }
    )
    for item in items:
        record = studio.catalog.get("evaluators", item["id"])
        studio.catalog.save(
            "evaluators", {**record, "data": {**record["data"], "rubric": "CHANGED"}}
        )
    pinned = queued[0][2]["_experiment_settings"]
    assert [item["judge"]["rubric"] for item in pinned["evaluators"]] == [
        "Grade Accuracy",
        "Grade Clarity",
    ]
    assert studio.catalog.get("experiments", saved["id"])["data"]["evaluators"] == items
    monkeypatch.setattr(studio.executor, "submit", submit)
    submit(*queued[0])
    done(studio, run)
    assert all("CHANGED" not in context.prompt for context in calls)


def test_import_all_aliases_retains_primary_and_independent_native_export(tmp_path):
    studio = StudioService(tmp_path)
    try:
        data = seed(studio)
        extra = judges(studio)[1]
        first = data["bindings"].pop("evaluator")
        data["bindings"]["evaluators"] = {
            "external-judge": first,
            "extra": {"id": extra["id"], "revision": extra["revision"]},
        }
        data["experiment"]["spec"].update(
            evaluatorIds=["external-judge", "extra"], primaryEvaluatorId="extra"
        )
        report = studio.configuration.convert_experiment(data, None)
        native = report["document"]["resources"][1]["data"]
        assert native["primaryEvaluatorId"] == "clarity" and len(native["evaluators"]) == 2
        assert not studio.records("runs") and not studio.records("configuration-plans")
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        exported = studio.configuration.export(None)
        assert (
            next(item for item in exported["resources"] if item["kind"] == "experiments")["data"]
            == native
        )
        for key in ("missing", "extra", "stale", "duplicate"):
            broken = copy.deepcopy(data)
            bindings = broken["bindings"]["evaluators"]
            if key == "missing":
                del bindings["extra"]
            elif key == "extra":
                bindings["unknown"] = first
            elif key == "stale":
                bindings["extra"]["revision"] = 999
            else:
                bindings["extra"] = first
            with pytest.raises(ValueError):
                studio.configuration.convert_experiment(broken, None)
    finally:
        studio.close()


def test_secondary_template_requirements_reject_before_queuing(app):
    studio, calls = app
    items = judges(studio)
    items[1]["judge"]["prompt_template"] = "{{ Prompt }} {{ Expected Response }} {{ Response }}"
    saved = experiment(studio, suite(studio), **selection(items))
    with pytest.raises(ValueError, match="expected response"):
        studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
    assert not calls and not studio.records("runs")


def test_target_failure_requires_no_grades_and_produces_a_blocked_partial_gate(app):
    studio, _ = app
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(success=False, error="Synthetic target failure")

    studio.provider_factory = lambda _: Provider()
    saved = experiment(studio, suite(studio), **selection(judges(studio)))
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert run["status"] == "failed" and len(calls) == 1
    assert report["results"][0]["error"] and not report["results"][0].get("evaluators")
    assert check_report(report)["status"] == "blocked"


def valid_report(app):
    studio, _ = app

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(
                success=True,
                output='{"score":1,"reason":"Good"}'
                if "Evaluate the answer" in context.prompt
                else "Approved",
            )

    studio.provider_factory = lambda _: Provider()
    saved = experiment(
        studio,
        suite(studio, cases=[{"input": "Question", "contains": ["Approved"]}]),
        runsPerExample=1,
        **selection(judges(studio)),
    )
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    return studio._read(studio._path("evaluation-reports", run["id"]))


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "duplicate",
        "revision",
        "primary",
        "manifest",
        "flat",
        "score",
        "passed",
        "policy",
    ],
)
def test_offline_gate_rejects_missing_or_contradictory_evaluator_evidence(app, tamper):
    report = valid_report(app)
    assert check_report(report)["passed"]
    entry = report["results"][0]
    if tamper == "missing":
        entry["evaluators"].pop()
    elif tamper == "duplicate":
        entry["evaluators"][1] = copy.deepcopy(entry["evaluators"][0])
    elif tamper == "revision":
        entry["evaluators"][0]["revision"] += 1
    elif tamper == "primary":
        report["primaryEvaluatorId"] = "missing"
    elif tamper == "manifest":
        del report["evaluators"]
        del report["primaryEvaluatorId"]
        del report["evaluatorPolicy"]
    elif tamper == "flat":
        entry["checks"].pop()
    elif tamper == "score":
        entry["evaluators"][0]["checks"][0]["score"] = 0
    elif tamper == "passed":
        entry["evaluators"][0]["passed"] = False
    else:
        report["evaluatorPolicy"] = "primary"
    with pytest.raises(ValueError):
        check_report(report)


@pytest.mark.parametrize("sql", [False, True])
def test_cancel_during_second_judge_preserves_dispatch_without_repeating_target(tmp_path, sql):
    waiting, calls = threading.Event(), []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            if "Grade Clarity" in context.prompt:
                waiting.set()
                await asyncio.Event().wait()
            return AgentResult(
                success=True,
                output='{"score":1,"reason":"Good"}'
                if "Evaluate the answer" in context.prompt
                else "Approved",
            )

    settings = options(tmp_path, 1) if sql else None
    root = tmp_path / "project"
    studio = StudioService(root, state_options=settings, provider_factory=lambda _: Provider())
    studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    saved = experiment(
        studio, suite(studio), runsPerExample=2, maxWorkers=2, **selection(judges(studio))
    )
    run = studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
    try:
        assert waiting.wait(5)
        settled(studio, studio.cancel_run(run["id"]))
    finally:
        studio.close()
    count = len(calls)
    restored = StudioService(root, state_options=settings, provider_factory=lambda _: Provider())
    try:
        assert restored.run(run["id"])["status"] == "cancelled"
        partial = restored._read(restored._path("evaluation-reports", run["id"]))
        assert partial["pending_grading"]
        for item in partial["pending_grading"].values():
            assert item["output"] == "Approved"
            assert item["evaluators"][0]["id"] == "accuracy" and item["evaluators"][0]["passed"]
        with pytest.raises(RuntimeError, match="automatic replay"):
            asyncio.run(restored.operations.evaluate(run["id"], {}))
        assert len(calls) == count
    finally:
        restored.close()


def test_workflow_pause_retains_multiple_judges_without_grading_unfinished_target(app):
    studio, calls = app

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(
                success=True,
                output='{"score":1,"reason":"Good"}'
                if "Evaluate the answer" in context.prompt
                else "Evidence",
            )

    studio.provider_factory = lambda _: Provider()
    selected = workflow(studio, "v1")
    saved = experiment(
        studio,
        suite(studio, target_kind="workflow", target_id=selected["id"]),
        runsPerExample=1,
        **selection(judges(studio)),
    )
    run = settle(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    assert run["status"] == "waiting" and len(calls) == 1
    run = settle(studio, approve(studio, run))
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert len(calls) == 4 and len(report["results"][0]["evaluators"]) == 2


def test_http_multi_import_uses_existing_owner_and_csrf_checks(team):
    studio, _, _, request, calls = team
    data = seed(studio)
    extra = judges(studio)[1]
    first = data["bindings"].pop("evaluator")
    data["bindings"]["evaluators"] = {
        "external-judge": first,
        "extra": {"id": extra["id"], "revision": extra["revision"]},
    }
    data["experiment"]["spec"]["evaluatorIds"].append("extra")
    route = "/api/studio/configuration/experiments/convert"
    assert request(route, data)[0] == 401
    assert request(route, data, user="bob")[0] == 403
    assert request(route, data, user="owner", csrf="bad")[0] == 403
    status, report = request(route, data, user="owner")
    assert status == 200 and len(report["document"]["resources"][1]["data"]["evaluators"]) == 2
    assert not calls and not studio.records("runs")
