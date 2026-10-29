"""External declarations need an explicit native companion and exact dependencies."""

import copy
import json

import pytest
import yaml

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_configuration import digest
from nexus.core.studio_experiment_judges import copy_evaluator
from tests.test_studio_evaluation_files import artifact, reference
from tests.test_studio_experiment_import import seed
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def saved(studio):
    request = seed(studio)
    request.update(
        pass_threshold=0.75,
        variants=[
            {"name": "Baseline"},
            {"name": "Candidate", "provider": "codex", "model": "chosen-model", "effort": "high"},
        ],
    )
    request["experiment"]["spec"]["maxWorkers"] = 4
    report = studio.configuration.convert_experiment(
        request, None if not studio.team else {"nexus_id": "owner", "role": "owner"}
    )
    actor = None if not studio.team else {"nexus_id": "owner", "role": "owner"}
    plan = studio.configuration.plan(report["document"], actor)
    studio.configuration.apply(plan["id"], plan["digest"], actor)
    record = studio.catalog.get("experiments", "imported-run")
    return {
        "experiment_id": record["id"],
        "revision": record["revision"],
        "dataset": request["bindings"]["dataset"],
    }


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def handoff(report):
    return next(
        item["content"] for item in report["files"] if item["filename"] == "experiment-handoff.json"
    )


def test_export_reimport_preserves_experiment_semantics_without_state_writes_or_file_reads(
    studio, monkeypatch
):
    data = saved(studio)
    original = copy.deepcopy(studio.catalog.get("experiments", data["experiment_id"]))
    before = {
        str(path.relative_to(studio.state)): path.read_bytes()
        for path in studio.state.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        studio.artifacts, "get", lambda *a, **kw: pytest.fail("Export must not read artifact bytes")
    )
    monkeypatch.setattr(
        studio, "provider_factory", lambda *a: pytest.fail("Export must not invoke a provider")
    )
    report = studio.configuration.export_experiment(data, None)
    assert report["valid"] and not report["diagnostics"] and len(report["files"]) == 2
    assert (
        data["revision"] == 1
        and studio.catalog.get("experiments", data["experiment_id"]) == original
    )
    bundle = json.loads(handoff(report))
    assert yaml.safe_load(report["files"][0]["content"]) == bundle["request"]["experiment"]
    assert bundle["request"]["pass_threshold"] == 0.75
    assert bundle["request"]["variants"] == original["data"]["variants"]
    assert "SECRET_EXPECTED" not in json.dumps(
        report
    ) and "{{ Expected Response }}" not in json.dumps(report)
    converted = studio.configuration.convert_experiment_handoff({"handoff": handoff(report)}, None)
    updated = converted["document"]["resources"][1]["data"]
    assert {k: v for k, v in updated.items() if k != "evaluation_id"} == {
        k: v for k, v in original["data"].items() if k != "evaluation_id"
    }
    assert updated["evaluation_id"] != original["data"]["evaluation_id"]
    old_suite = studio.catalog.get("evaluations", original["data"]["evaluation_id"])
    assert converted["document"]["resources"][0]["data"] == old_suite["data"]
    assert converted["handoff_source"] == report["source"]
    after = {
        str(path.relative_to(studio.state)): path.read_bytes()
        for path in studio.state.rglob("*")
        if path.is_file()
    }
    assert after == before and not studio.records("runs")


@pytest.mark.parametrize(
    "change,code",
    [
        ("experiment", "stale_revision"),
        ("dataset", "stale_revision"),
        ("cases", "case_mismatch"),
        ("judge", "changed_evaluator"),
        ("archived", "unavailable"),
        ("suite", "stale_revision"),
        ("inline", "unsupported_inline_judges"),
        ("workflow", "unsupported_workflow"),
        ("identity", "identity_loss"),
        ("agent", "unavailable_agent"),
    ],
)
def test_incompatible_exports_return_diagnostics_and_no_partial_files(
    studio, monkeypatch, change, code
):
    data = saved(studio)
    record = studio.catalog.get("experiments", data["experiment_id"])
    if change in {"experiment", "dataset"}:
        (data if change == "experiment" else data["dataset"])["revision"] = 999
    elif change == "cases":
        item = studio.catalog.get("datasets", "references")
        changed = studio.catalog.save(
            "datasets", {**item, "data": {"cases": [{"input": "Changed", "contains": ["Changed"]}]}}
        )
        data["dataset"]["revision"] = changed["revision"]
    elif change == "judge":
        item = studio.catalog.get("evaluators", "judge")
        studio.catalog.save("evaluators", {**item, "data": {**item["data"], "rubric": "Changed"}})
    elif change == "archived":
        studio.catalog.archive("evaluators", "judge", {"revision": 1})
    elif change in {"suite", "workflow"}:
        item = studio.catalog.get("evaluations", record["data"]["evaluation_id"])
        content = copy.deepcopy(item["data"])
        if change == "workflow":
            studio.save_definition(
                "workflows",
                {
                    "name": "Reviewed",
                    "graph": {"version": 2, "nodes": [{"id": "gate", "kind": "approval"}]},
                },
            )
            content.update(target_kind="workflow", target_id="reviewed")
        updated = studio.catalog.save("evaluations", {**item, "data": content})
        if change == "workflow":
            record = studio.catalog.save(
                "experiments",
                {**record, "data": {**record["data"], "evaluation_revision": updated["revision"]}},
            )
            data["revision"] = record["revision"]
    elif change == "inline":
        content = {
            key: value
            for key, value in record["data"].items()
            if key not in {"evaluators", "evaluatorPolicy", "primaryEvaluatorId"}
        }
        data["revision"] = studio.catalog.save("experiments", {**record, "data": content})[
            "revision"
        ]
    elif change == "identity":
        copied = studio.catalog.save(
            "experiments", {**record, "id": "custom-identity", "revision": 0}
        )
        data.update(experiment_id=copied["id"], revision=copied["revision"])
    else:
        definitions = studio.definitions
        monkeypatch.setattr(
            studio, "definitions", lambda kind: [] if kind == "agents" else definitions(kind)
        )
    report = studio.configuration.export_experiment(data, None)
    assert not report["valid"] and report["files"] == []
    assert code in {issue["code"] for issue in report["diagnostics"]}
    assert not studio.records("runs")


def test_export_collects_multiple_diagnostics_instead_of_emitting_a_lossy_declaration(studio):
    data = saved(studio)
    data["revision"] = 999
    data["dataset"]["revision"] = 999
    studio.catalog.archive("evaluators", "judge", {"revision": 1})
    report = studio.configuration.export_experiment(data, None)
    assert len(report["diagnostics"]) == 3 and report["files"] == []


def test_reviewed_handoff_apply_then_executes_original_variants_repeats_and_judge_once(studio):
    data = saved(studio)
    original = studio.catalog.get("experiments", data["experiment_id"])
    old_suite = studio.catalog.get("evaluations", original["data"]["evaluation_id"])
    report = studio.configuration.export_experiment(data, None)
    converted = studio.configuration.convert_experiment_handoff({"handoff": handoff(report)}, None)
    assert not studio.records("runs")
    plan = studio.configuration.plan(converted["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    updated = studio.catalog.get("experiments", data["experiment_id"])
    assert updated["revision"] == 2 and updated["data"]["evaluation_id"] != old_suite["id"]
    assert studio.catalog.get("evaluations", old_suite["id"]) == old_suite
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

    studio.provider_factory = lambda _: Provider()
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": updated["id"]}))
    result = studio._read(studio._path("evaluation-reports", run["id"]))
    assert run["status"] == "completed" and len(calls) == 12
    assert result["expected_total"] == result["passed"] == 6 and result["max_workers"] == 4
    assert result["pass_threshold"] == 0.75 and result["acceptance"]


def test_multiple_evaluators_keep_primary_and_effective_case_semantics(studio):
    data = saved(studio)
    record = studio.catalog.get("experiments", data["experiment_id"])
    extra = copy_evaluator(
        studio.catalog.save(
            "evaluators",
            {"name": "Clarity", "data": {"provider": "codex", "rubric": "Grade clarity"}},
        )
    )
    content = {
        **record["data"],
        "evaluators": [*record["data"]["evaluators"], extra],
        "primaryEvaluatorId": extra["id"],
    }
    data["revision"] = studio.catalog.save("experiments", {**record, "data": content})["revision"]
    exported = studio.configuration.export_experiment(data, None)
    assert exported["valid"]
    converted = studio.configuration.convert_experiment_handoff(
        {"handoff": handoff(exported)}, None
    )
    native = converted["document"]["resources"][1]["data"]
    assert (
        native["evaluators"] == content["evaluators"]
        and native["primaryEvaluatorId"] == extra["id"]
    )
    assert native["evaluatorPolicy"] == "all"
    assert converted["document"]["resources"][0]["data"]["cases"][0]["judge"] == extra["judge"]


def test_export_references_file_roles_without_opening_or_embedding_artifacts(studio, monkeypatch):
    data = saved(studio)
    record = studio.catalog.get("experiments", data["experiment_id"])
    dataset = studio.catalog.get("datasets", data["dataset"]["id"])
    copied = copy.deepcopy(dataset["data"])
    copied["cases"][0].update(
        input_files=[reference(artifact(studio, "input", "SECRET_INPUT_BYTES"))],
        reference_files=[reference(artifact(studio, "reference", "SECRET_REFERENCE_BYTES"))],
        judge=record["data"]["evaluators"][0]["judge"],
    )
    dataset = studio.catalog.save("datasets", {**dataset, "data": copied})
    selected = studio.catalog.get("evaluations", record["data"]["evaluation_id"])
    selected = studio.catalog.save(
        "evaluations", {**selected, "data": {**selected["data"], "cases": copied["cases"]}}
    )
    record = studio.catalog.save(
        "experiments",
        {**record, "data": {**record["data"], "evaluation_revision": selected["revision"]}},
    )
    data.update(revision=record["revision"], dataset=reference(dataset))
    monkeypatch.setattr(
        studio.artifacts, "get", lambda *a, **kw: pytest.fail("Do not open referenced artifacts")
    )
    report = studio.configuration.export_experiment(data, None)
    assert (
        report["valid"]
        and "SECRET_INPUT_BYTES" not in json.dumps(report)
        and "SECRET_REFERENCE_BYTES" not in json.dumps(report)
    )


def test_target_edit_after_export_rejects_handoff_before_creating_a_plan(studio):
    report = studio.configuration.export_experiment(saved(studio), None)
    target = next(item for item in studio.definitions("agents") if item["id"] == "target")
    studio.update_definition(
        "agents",
        "target",
        {"revision": target["revision"], "name": "Target", "instructions": "Changed"},
    )
    before = studio.records("configuration-plans")
    with pytest.raises(ValueError, match="Target revision changed"):
        studio.configuration.convert_experiment_handoff({"handoff": handoff(report)}, None)
    assert studio.records("configuration-plans") == before and not studio.records("runs")


def test_handoff_source_identity_is_descriptive_and_never_fetches_other_experiments(
    studio, monkeypatch
):
    exported = studio.configuration.export_experiment(saved(studio), None)
    bundle = json.loads(handoff(exported))
    bundle["source"]["experiment_id"] = "unrelated"
    bundle["digest"] = digest({key: value for key, value in bundle.items() if key != "digest"})
    get = studio.catalog.get

    def guard(kind, identity, **kwargs):
        if kind == "experiments" and identity == "unrelated":
            pytest.fail("Descriptive source identity must not fetch another experiment")
        return get(kind, identity, **kwargs)

    monkeypatch.setattr(studio.catalog, "get", guard)
    report = studio.configuration.convert_experiment_handoff({"handoff": json.dumps(bundle)}, None)
    assert report["handoff_source"]["experiment_id"] == "unrelated"
    assert report["document"]["resources"][1]["id"] == "imported-run"
    assert not studio.records("runs")


@pytest.mark.parametrize(
    "change",
    [
        "digest",
        "duplicate",
        "nan",
        "overflow",
        "unknown",
        "version",
        "source",
        "request",
        "stale",
        "oversize",
        "surrogate",
    ],
)
def test_invalid_or_stale_handoff_cannot_create_plans_resources_or_runs(studio, change):
    report = studio.configuration.export_experiment(saved(studio), None)
    text = handoff(report)
    bundle = json.loads(text)
    if change == "digest":
        bundle["request"]["pass_threshold"] = 0
    elif change == "unknown":
        bundle["unexpected"] = True
    elif change == "version":
        bundle["format"] = "nexus-studio-experiment-handoff/v99"
    elif change == "source":
        bundle["source"]["revision"] = True
    elif change == "request":
        bundle["request"]["experiment"]["spec"]["maxWorkers"] = 5
    elif change == "stale":
        bundle["request"]["bindings"]["dataset"]["revision"] = 999
    if change in {"unknown", "version", "source", "request", "stale"}:
        bundle["digest"] = digest({k: v for k, v in bundle.items() if k != "digest"})
    text = json.dumps(bundle)
    if change == "duplicate":
        text = '{"format":"first",' + text[1:]
    elif change == "nan":
        text = '{"value":NaN}'
    elif change == "overflow":
        text = '{"value":1e999}'
    elif change == "oversize":
        text = "x" * (2 * 1024 * 1024 + 1)
    elif change == "surrogate":
        text = "\ud800"
    before = copy.deepcopy(studio.records("configuration-plans"))
    with pytest.raises(ValueError):
        studio.configuration.convert_experiment_handoff({"handoff": text}, None)
    assert studio.records("configuration-plans") == before and not studio.records("runs")


def test_owner_and_csrf_checks_precede_export_and_handoff_parsing(team):
    studio, _, _, request, calls = team
    data = saved(studio)
    route = "/api/studio/configuration/experiments/export"
    assert request(route, data)[0] == 401
    for user in ("alice", "bob", "viewer"):
        assert request(route, {"bad": True}, user=user)[0] == 403
    assert request(route, data, user="owner", csrf="bad")[0] == 403
    status, report = request(route, data, user="owner")
    assert status == 200 and report["valid"]
    route = "/api/studio/configuration/experiments/handoff/convert"
    payload = {"handoff": handoff(report)}
    assert request(route, payload)[0] == 401
    assert request(route, {"bad": True}, user="bob")[0] == 403
    assert request(route, payload, user="owner", csrf="bad")[0] == 403
    status, converted = request(route, payload, user="owner")
    assert status == 200 and converted["planned_executions"] == 6
    assert not calls and not studio.records("runs")
