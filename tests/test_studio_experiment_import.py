"""Bounded experiment imports copy reviewed resources, never worker semantics."""

import copy

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def seed(studio):
    dataset = studio.catalog.save(
        "datasets",
        {
            "name": "References",
            "data": {
                "cases": [
                    {
                        "input": "Question",
                        "expected_response": "SECRET_EXPECTED",
                        "contains": ["Approved"],
                    }
                ]
            },
        },
    )
    judge = studio.catalog.save(
        "evaluators",
        {
            "name": "Judge",
            "data": {
                "provider": "codex",
                "rubric": "Grade",
                "choices": {"Pass": 1, "Fail": 0},
                "prompt_template": "{{ Prompt }} {{ Expected Response }} {{ Response }}",
            },
        },
    )
    agent = studio.save_definition(
        "agents", {"id": "target", "name": "Target", "instructions": "Answer", "provider": "codex"}
    )
    return {
        "experiment": {
            "kind": "experiment",
            "name": "Imported run",
            "spec": {
                "datasetId": "external-data",
                "targetAgent": "external-agent",
                "evaluatorIds": ["external-judge"],
                "primaryEvaluatorId": "external-judge",
                "runsPerExample": 3,
                "maxWorkers": 1,
            },
        },
        "bindings": {
            "dataset": {"id": dataset["id"], "revision": dataset["revision"]},
            "evaluator": {"id": judge["id"], "revision": judge["revision"]},
            "target": {"id": agent["id"], "revision": agent["revision"]},
        },
        "pass_threshold": 1,
    }


def test_convert_plan_apply_copies_independent_suite_then_runs_native_repeats(tmp_path):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            if "Evaluate the answer using only the rubric" in context.prompt:
                assert "SECRET_EXPECTED" in context.prompt
                return AgentResult(success=True, output='{"choice":"Pass","reason":"Matches"}')
            assert "SECRET_EXPECTED" not in context.prompt
            return AgentResult(success=True, output="Approved")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        request = seed(studio)
        before = copy.deepcopy(request)
        report = studio.configuration.convert_experiment(request, None)
        assert report["planned_executions"] == 3 and request == before
        assert not studio.catalog.list("evaluations") and not studio.catalog.list("experiments")
        assert not studio.records("configuration-plans") and not calls
        resources = report["document"]["resources"]
        assert resources[1]["data"]["evaluation_id"] == resources[0]["id"]
        assert resources[1]["data"]["evaluation_revision"] == 1
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        assert not calls and not studio.records("runs")
        source = studio.catalog.get("datasets", "references")
        studio.catalog.save(
            "datasets", {**source, "data": {"cases": [{"input": "CHANGED", "contains": ["Wrong"]}]}}
        )
        judge = studio.catalog.get("evaluators", "judge")
        studio.catalog.save(
            "evaluators", {**judge, "data": {"provider": "codex", "rubric": "CHANGED_JUDGE"}}
        )
        run = done(
            studio, studio.start_run({"kind": "evaluation", "experiment_id": "imported-run"})
        )
        assert run["status"] == "completed", run
        assert len(calls) == 6 and all("CHANGED_JUDGE" not in context.prompt for context in calls)
        report = studio._read(studio._path("evaluation-reports", run["id"]))
        assert report["passed"] == report["total"] == 3
    finally:
        studio.close()


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


@pytest.mark.parametrize(
    "failure",
    [
        "workers",
        "boolean_workers",
        "float_workers",
        "multiple_judges",
        "primary_mismatch",
        "unknown",
        "stale_dataset",
        "stale_judge",
        "stale_target",
        "budget",
        "threshold_missing",
        "invalid_repeats",
    ],
)
def test_unsupported_or_stale_input_creates_nothing(studio, failure):
    data = seed(studio)
    spec = data["experiment"]["spec"]
    if failure == "workers":
        spec["maxWorkers"] = 5
    elif failure == "boolean_workers":
        spec["maxWorkers"] = True
    elif failure == "float_workers":
        spec["maxWorkers"] = 1.0
    elif failure == "multiple_judges":
        spec["evaluatorIds"].append("second")
    elif failure == "primary_mismatch":
        spec["primaryEvaluatorId"] = "different"
    elif failure == "unknown":
        spec["deploy"] = True
    elif failure.startswith("stale_"):
        data["bindings"][
            {"stale_dataset": "dataset", "stale_judge": "evaluator", "stale_target": "target"}[
                failure
            ]
        ]["revision"] = 999
    elif failure == "threshold_missing":
        del data["pass_threshold"]
    elif failure == "invalid_repeats":
        spec["runsPerExample"] = True
    else:
        source = studio.catalog.get("datasets", "references")
        source = studio.catalog.save(
            "datasets", {**source, "data": {"cases": source["data"]["cases"] * 100}}
        )
        data["bindings"]["dataset"]["revision"] = source["revision"]
        spec["runsPerExample"] = 5
    with pytest.raises(ValueError):
        studio.configuration.convert_experiment(data, None)
    assert not studio.catalog.list("evaluations") and not studio.catalog.list("experiments")
    assert not studio.records("configuration-plans") and not studio.records("runs")


def test_reimports_make_fresh_suites_and_archived_sources_reject(studio):
    data = seed(studio)
    first = studio.configuration.convert_experiment(data, None)
    plan = studio.configuration.plan(first["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    first_id = first["document"]["resources"][0]["id"]
    first_suite = studio.catalog.get("evaluations", first_id)
    second = studio.configuration.convert_experiment(data, None)
    second_id = second["document"]["resources"][0]["id"]
    assert first_id != second_id
    plan = studio.configuration.plan(second["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    assert studio.catalog.get("evaluations", first_id) == first_suite
    assert studio.catalog.get("evaluations", second_id)["revision"] == 1
    experiment = studio.catalog.get("experiments", "imported-run")
    assert experiment["revision"] == 2 and experiment["data"]["evaluation_id"] == second_id
    exported = studio.configuration.export(None)
    assert any(item["id"] == second_id for item in exported["resources"])
    assert not studio.records("runs")
    studio.catalog.archive("datasets", "references", {"revision": 1})
    with pytest.raises(ValueError, match="archived"):
        studio.configuration.convert_experiment(data, None)


@pytest.mark.parametrize(
    "source",
    [
        "kind: experiment\nkind: experiment\n",
        "kind: experiment\nname: &name Test\nspec: *name\n",
        "kind: [experiment\n",
        "[]",
    ],
)
def test_invalid_yaml_cannot_create_resources_or_plans(studio, source):
    data = seed(studio)
    data["experiment"] = source
    with pytest.raises(ValueError):
        studio.configuration.convert_experiment(data, None)
    assert not studio.records("configuration-plans") and not studio.records("runs")
    assert not studio.catalog.list("evaluations") and not studio.catalog.list("experiments")


def test_owner_http_permission_precedes_parsing_and_exact_revision_bindings(team):
    studio, _, _, request, calls = team
    data = seed(studio)
    path = "/api/studio/configuration/experiments/convert"
    assert request(path, data)[0] == 401
    for user in ("alice", "bob", "viewer"):
        assert request(path, {"bad": True}, user=user)[0] == 403
    assert request(path, data, user="owner", csrf="bad")[0] == 403
    status, report = request(path, data, user="owner")
    assert status == 200 and report["planned_executions"] == 3
    assert not calls and not studio.records("runs") and not studio.catalog.list("experiments")
