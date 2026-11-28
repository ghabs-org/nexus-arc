"""Bounded evaluation declarations preserve supported data and reject lossy exports."""

import copy
import csv
import io
import json

import pytest
import yaml

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_configuration import digest
from tests.test_studio_evaluation_import import package
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def seed(studio, *, empty_description=False, provider="codex"):
    request = package()
    request["binding"].update(provider=provider, model="chosen-model")
    request["threshold"] = 0.84
    if empty_description:
        request["dataset"]["description"] = request["evaluator"]["description"] = ""
    actor = {"nexus_id": "owner", "role": "owner"} if studio.team else None
    report = studio.configuration.convert_evaluations(request, actor)
    plan = studio.configuration.plan(report["document"], actor)
    studio.configuration.apply(plan["id"], plan["digest"], actor)
    return {
        "dataset": {"id": "cases", "revision": 1},
        "evaluator": {"id": "judge", "revision": 1},
        "model_alias": "portable-judge",
    }


def handoff(report):
    return next(
        file["content"] for file in report["files"] if file["filename"] == "evaluation-handoff.json"
    )


@pytest.mark.parametrize("provider", ["codex", "claude-code"])
@pytest.mark.parametrize("empty_description", [False, True])
def test_package_roundtrip_is_self_contained_and_preserves_data_metadata_and_binding(
    studio, tmp_path, monkeypatch, provider, empty_description
):
    request = seed(studio, empty_description=empty_description, provider=provider)
    original = {
        kind: studio.catalog.get(kind, ref["id"])
        for kind, ref in (("datasets", request["dataset"]), ("evaluators", request["evaluator"]))
    }
    before = {
        str(path.relative_to(studio.state)): path.read_bytes()
        for path in studio.state.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        studio, "provider_factory", lambda *a: pytest.fail("Export must not call AI")
    )
    exported = studio.configuration.export_evaluations(request, None)
    assert exported["valid"] and len(exported["files"]) == 4
    assert exported["binding"] == {
        "alias": "portable-judge",
        "provider": provider,
        "model": "chosen-model",
    }
    assert exported["threshold"] == 0.84
    bundle = json.loads(handoff(exported))
    assert yaml.safe_load(exported["files"][0]["content"]) == bundle["request"]["dataset"]
    assert yaml.safe_load(exported["files"][1]["content"]) == bundle["request"]["evaluator"]
    assert "SECRET_REFERENCE" in exported["files"][2]["content"]
    target = StudioService(tmp_path / "fresh-destination")
    try:
        converted = target.configuration.convert_evaluation_handoff(
            {"handoff": handoff(exported)}, None
        )
        assert converted["source"]["sequence_numbers"] == [1, 2]
        for resource in converted["document"]["resources"]:
            source = original[resource["kind"]]
            assert all(
                resource[key] == source[key] for key in ("id", "name", "description", "data")
            )
        assert (
            not target.records("configuration-plans")
            and not target.catalog.list("datasets")
            and not target.records("runs")
        )
        plan = target.configuration.plan(converted["document"], None)
        target.configuration.apply(plan["id"], plan["digest"], None)
        assert target.catalog.get("datasets", "cases")["data"] == original["datasets"]["data"]
        assert target.catalog.get("evaluators", "judge")["data"] == original["evaluators"]["data"]
        assert not target.records("runs")
    finally:
        target.close()
    after = {
        str(path.relative_to(studio.state)): path.read_bytes()
        for path in studio.state.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_unicode_quotes_multiline_cells_and_row_order_roundtrip_exactly(studio):
    request = seed(studio)
    dataset = studio.catalog.get("datasets", "cases")
    content = copy.deepcopy(dataset["data"])
    content["cases"][0].update(
        input='Question, "quoted"\n日本語 😀',
        expected_response='Reference\r\n{{ Response }} and "quotes"',
    )
    dataset = studio.catalog.save("datasets", {**dataset, "data": content})
    request["dataset"]["revision"] = dataset["revision"]
    report = studio.configuration.export_evaluations(request, None)
    assert report["valid"], report
    csv_text = next(file["content"] for file in report["files"] if file["filename"] == "cases.csv")
    rows = list(csv.DictReader(io.StringIO(csv_text, newline="")))
    assert rows[0]["prompt"] == content["cases"][0]["input"]
    assert rows[0]["expected_response"] == content["cases"][0]["expected_response"]
    converted = studio.configuration.convert_evaluation_handoff({"handoff": handoff(report)}, None)
    assert converted["dataset"] == dataset["data"]


@pytest.mark.parametrize(
    "change,code",
    [
        ("stale", "stale_revision"),
        ("archived", "unavailable"),
        ("checks", "unsupported_phrase_checks"),
        ("structured", "unsupported_case_field"),
        ("contexts", "unsupported_case_field"),
        ("files", "unsupported_case_field"),
        ("reference", "missing_reference"),
        ("judge", "judge_mismatch"),
        ("rubric", "unsupported_rubric"),
        ("template", "unsupported_template"),
        ("numeric", "unsupported_numeric_judge"),
        ("identity", "identity_loss"),
    ],
)
def test_unsupported_state_returns_diagnostics_with_no_partial_files(
    studio, monkeypatch, change, code
):
    request = seed(studio)
    dataset = studio.catalog.get("datasets", "cases")
    evaluator = studio.catalog.get("evaluators", "judge")
    data = copy.deepcopy(dataset["data"])
    if change == "stale":
        request["dataset"]["revision"] = 999
    elif change == "archived":
        studio.catalog.archive("evaluators", "judge", {"revision": 1})
    elif change == "identity":
        record = studio.catalog.save("datasets", {**dataset, "id": "custom-id", "revision": 0})
        request["dataset"] = {"id": record["id"], "revision": record["revision"]}
    elif change in {"rubric", "template", "numeric"}:
        judge = copy.deepcopy(evaluator["data"])
        if change == "rubric":
            judge["rubric"] = "Custom rubric must not be dropped"
        elif change == "template":
            judge["prompt_template"] = "{{ Prompt }} {{ Response }}"
        else:
            del judge["choices"]
        updated = studio.catalog.save("evaluators", {**evaluator, "data": judge})
        request["evaluator"]["revision"] = updated["revision"]
    else:
        case = data["cases"][0]
        if change == "checks":
            case["contains"] = ["Extra check"]
        elif change == "structured":
            case["checks"] = [{"type": "exact", "expected": "Extra"}]
        elif change == "contexts":
            case["contexts"] = ["Extra context"]
        elif change == "files":
            case["input_files"] = [{"id": "private-file", "revision": 1}]
        elif change == "reference":
            del case["expected_response"]
            case["judge"].pop("prompt_template")
        else:
            case["judge"]["threshold"] = 0.25
        updated = studio.catalog.save("datasets", {**dataset, "data": data})
        request["dataset"]["revision"] = updated["revision"]
    monkeypatch.setattr(
        studio.artifacts, "get", lambda *a, **kw: pytest.fail("Do not read referenced artifacts")
    )
    report = studio.configuration.export_evaluations(request, None)
    assert not report["valid"] and report["files"] == []
    assert code in {issue["code"] for issue in report["diagnostics"]}
    assert not studio.records("runs")


def test_handoff_reimport_after_source_archive_preserves_reference_isolation_when_run(studio):
    request = seed(studio)
    exported = studio.configuration.export_evaluations(request, None)
    studio.catalog.archive("datasets", "cases", {"revision": 1})
    studio.catalog.archive("evaluators", "judge", {"revision": 1})
    report = studio.configuration.convert_evaluation_handoff({"handoff": handoff(exported)}, None)
    studio.save_definition(
        "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Answer"}
    )
    selected = studio.catalog.save(
        "evaluations",
        {
            "name": "Portable cases",
            "data": {
                "target_kind": "agent",
                "target_id": "reviewer",
                "cases": report["dataset"]["cases"],
            },
        },
    )
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            if "Evaluate the answer using only the rubric" in context.prompt:
                assert "REFERENCE" in context.prompt
                return AgentResult(success=True, output='{"choice":"Pass","reason":"Matches"}')
            assert "REFERENCE" not in context.prompt
            return AgentResult(success=True, output="Target answer")

    studio.provider_factory = lambda _: Provider()
    run = done(studio, studio.start_run({"kind": "evaluation", "evaluation_id": selected["id"]}))
    assert run["status"] == "completed" and len(calls) == 4
    assert studio._read(studio._path("evaluation-reports", run["id"]))["passed"] == 2


def test_prepared_package_limit_rejects_large_quoted_data_without_partial_files(studio):
    request = seed(studio)
    record = studio.catalog.get("datasets", "cases")
    case = copy.deepcopy(record["data"]["cases"][0])
    case.update(input='"' * 12000, expected_response='"' * 6000)
    updated = studio.catalog.save("datasets", {**record, "data": {"cases": [case] * 20}})
    request["dataset"]["revision"] = updated["revision"]
    report = studio.configuration.export_evaluations(request, None)
    assert not report["valid"] and not report["files"]
    assert any(issue["code"] == "size_limit" for issue in report["diagnostics"])


def test_native_metadata_outside_declaration_byte_limits_is_not_truncated(studio):
    request = seed(studio)
    record = studio.catalog.get("datasets", "cases")
    updated = studio.catalog.save("datasets", {**record, "description": "😀" * 700})
    request["dataset"]["revision"] = updated["revision"]
    report = studio.configuration.export_evaluations(request, None)
    assert not report["valid"] and not report["files"]
    assert any(issue["code"] == "conversion_preflight" for issue in report["diagnostics"])
    assert studio.catalog.get("datasets", "cases")["description"] == "😀" * 700


@pytest.mark.parametrize(
    "failure",
    [
        "duplicate",
        "digest",
        "version",
        "source",
        "unknown",
        "nan",
        "overflow",
        "oversized",
        "surrogate",
        "request",
    ],
)
def test_malformed_handoff_cannot_create_resources_or_plans(studio, failure):
    report = studio.configuration.export_evaluations(seed(studio), None)
    bundle = json.loads(handoff(report))
    if failure == "digest":
        bundle["request"]["threshold"] = 0
    elif failure == "version":
        bundle["format"] = "nexus-studio-evaluation-handoff/v99"
    elif failure == "source":
        bundle["source"]["dataset"]["revision"] = True
    elif failure == "unknown":
        bundle["extra"] = "unexpected"
    elif failure == "request":
        bundle["request"]["path"] = "/private/do-not-read"
    elif failure == "overflow":
        bundle["request"]["threshold"] = float("inf")
    if failure in {"source", "request", "overflow"}:
        bundle["digest"] = digest({key: value for key, value in bundle.items() if key != "digest"})
    text = json.dumps(bundle)
    if failure == "duplicate":
        text = '{"source":{},' + text[1:]
    elif failure == "nan":
        text = '{"value":NaN}'
    elif failure == "oversized":
        text = "x" * (2 * 1024 * 1024 + 1)
    elif failure == "surrogate":
        text = "\ud800"
    before = studio.records("configuration-plans")
    with pytest.raises(ValueError):
        studio.configuration.convert_evaluation_handoff({"handoff": text}, None)
    assert studio.records("configuration-plans") == before and not studio.records("runs")


def test_export_and_handoff_enforce_owner_csrf_before_parsing(team):
    studio, _, _, request, calls = team
    payload = seed(studio)
    route = "/api/studio/configuration/evaluations/export"
    assert request(route, payload)[0] == 401
    for user in ("alice", "bob", "viewer"):
        assert request(route, {"bad": True}, user=user)[0] == 403
    assert request(route, payload, user="owner", csrf="bad")[0] == 403
    status, exported = request(route, payload, user="owner")
    assert status == 200 and exported["valid"]
    route = "/api/studio/configuration/evaluations/handoff/convert"
    payload = {"handoff": handoff(exported)}
    assert request(route, payload)[0] == 401
    assert request(route, payload, user="bob")[0] == 403
    assert request(route, payload, user="owner", csrf="bad")[0] == 403
    status, converted = request(route, payload, user="owner")
    assert status == 200 and len(converted["dataset"]["cases"]) == 2
    assert not calls and not studio.records("runs")
