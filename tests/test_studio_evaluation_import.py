"""Evaluation packages use explicit file text, bounded templates and native review."""

import csv
import io

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_evaluation_import import convert_package
from nexus.core.studio_judge_templates import render_template, validate_template
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def package():
    return {
        "dataset": {"kind": "dataset", "name": "Cases", "spec": {"examples_file": "cases.csv"}},
        "evaluator": {
            "kind": "evaluator",
            "name": "Judge",
            "spec": {
                "type": "llm_judge",
                "model": "account",
                "promptTemplate": "Question: {{ Prompt }}\nReference: {{ Expected Response }}\nAnswer: {{ Response }}\nChoose Pass or Fail.",
                "choiceScores": {"Pass": 1, "Fail": 0},
            },
        },
        "files": {
            "cases.csv": "sequence_number,prompt,expected_response\n4,Question,SECRET_REFERENCE\n2,Another question,OTHER_REFERENCE\n"
        },
        "binding": {"alias": "account", "provider": "codex"},
    }


def test_conversion_review_save_and_judge_reference_isolation(tmp_path):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            if "Evaluate the answer using only the rubric" in context.prompt:
                assert "SECRET_REFERENCE" in context.prompt
                assert '"Question"' in context.prompt and '"Target answer"' in context.prompt
                return AgentResult(
                    success=True, output='{"choice":"Pass","reason":"Matches reference"}'
                )
            assert "SECRET_REFERENCE" not in context.prompt
            return AgentResult(success=True, output="Target answer")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        report = studio.configuration.convert_evaluations(package(), None)
        assert report["source"]["sequence_numbers"] == [4, 2]
        assert (
            not calls
            and not studio.catalog.list("datasets")
            and not studio.records("configuration-plans")
        )
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        assert not calls and not studio.records("runs")
        dataset = studio.catalog.get("datasets", "cases")
        evaluator = studio.catalog.get("evaluators", "judge")
        assert (
            evaluator["data"]["prompt_template"] == package()["evaluator"]["spec"]["promptTemplate"]
        )
        assert dataset["data"]["cases"][0]["expected_response"] == "SECRET_REFERENCE"
        studio.save_definition(
            "agents",
            {"id": "reviewer", "name": "Reviewer", "instructions": "Answer supplied questions"},
        )
        suite = studio.catalog.save(
            "evaluations",
            {
                "name": "Imported cases",
                "data": {
                    "target_kind": "agent",
                    "target_id": "reviewer",
                    "cases": dataset["data"]["cases"][:1],
                },
            },
        )
        run = done(studio, studio.start_run({"kind": "evaluation", "evaluation_id": suite["id"]}))
        assert run["status"] == "completed", run
        assert len(calls) == 2
        result = studio._read(studio._path("evaluation-reports", run["id"]))
        assert result["passed"] == 1 and result["results"][0]["checks"][0]["choice"] == "Pass"
    finally:
        studio.close()


@pytest.mark.parametrize(
    "failure",
    [
        "path",
        "url",
        "missing_file",
        "extra_file",
        "duplicate_sequence",
        "unknown_header",
        "extra_cell",
        "unsupported_variable",
        "expression",
        "missing_variable",
        "mismatched_alias",
        "unknown_field",
        "invalid_score",
        "oversized",
        "yaml_alias",
    ],
)
def test_unsupported_package_input_rejects_without_reading_paths(failure):
    data = package()
    if failure in {"path", "url"}:
        data["dataset"]["spec"]["examples_file"] = (
            "../private/cases.csv" if failure == "path" else "https://example.invalid/cases.csv"
        )
    elif failure == "missing_file":
        data["files"] = {}
    elif failure == "extra_file":
        data["files"]["another.csv"] = "ignored"
    elif failure == "duplicate_sequence":
        data["files"]["cases.csv"] += "4,duplicate,reference\n"
    elif failure == "unknown_header":
        data["files"]["cases.csv"] = "sequence_number,prompt,expected_response,unknown\n1,x,y,z\n"
    elif failure == "extra_cell":
        data["files"]["cases.csv"] += "3,x,y,z\n"
    elif failure in {"unsupported_variable", "expression", "missing_variable"}:
        data["evaluator"]["spec"]["promptTemplate"] = {
            "unsupported_variable": "{{ Unknown }}",
            "expression": '{% include "secret" %}',
            "missing_variable": "{{ Prompt }}",
        }[failure]
    elif failure == "mismatched_alias":
        data["binding"]["alias"] = "different"
    elif failure == "unknown_field":
        data["evaluator"]["spec"]["run"] = True
    elif failure == "invalid_score":
        data["evaluator"]["spec"]["choiceScores"]["Pass"] = True
    elif failure == "oversized":
        data["files"]["cases.csv"] = "x" * (900 * 1024 + 1)
    else:
        data["dataset"] = (
            "kind: dataset\nname: Cases\nspec: &ref {examples_file: cases.csv}\nother: *ref"
        )
    with pytest.raises(ValueError):
        convert_package(data)


def test_utf8_bom_csv_quotes_and_one_pass_template_render():
    data = package()
    content = io.StringIO(newline="")
    writer = csv.writer(content)
    writer.writerow(["prompt", "expected_response", "sequence_number"])
    writer.writerow(["First line\nSecond line, café", "{{ Response }} literal reference", "7"])
    data["files"]["cases.csv"] = "\ufeff" + content.getvalue()
    report = convert_package(data)
    case = report["dataset"]["cases"][0]
    rendered = render_template(report["evaluator"]["prompt_template"], case, "Actual response")
    assert "{{ Response }} literal reference" in rendered
    assert rendered.count("Actual response") == 1
    assert "café" in rendered
    with pytest.raises(ValueError):
        validate_template("{{ Prompt }} {{ Prompt }}")


def test_http_owner_permissions_precede_parsing_and_conversion_has_no_writes(team):
    studio, _, _, request, calls = team
    path = "/api/studio/configuration/evaluations/convert"
    assert request(path, package())[0] == 401
    for user in ("alice", "bob", "viewer"):
        assert request(path, {"malformed": True}, user=user)[0] == 403
    assert request(path, package(), user="owner", csrf="bad")[0] == 403
    status, result = request(path, package(), user="owner")
    assert status == 200 and len(result["document"]["resources"]) == 2
    assert (
        not calls and not studio.catalog.list("datasets") and not studio.catalog.list("evaluators")
    )
    assert not studio.records("configuration-plans") and not studio.records("runs")
