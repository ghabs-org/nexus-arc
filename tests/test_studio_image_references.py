"""Image evidence distinguishes absence/pruning from unresolved or failed inputs."""

import pytest

from nexus.core.studio_interop_values import import_value
from nexus.core.studio_workflow_artifacts import image_references, skipped_reason
from nexus.core.studio_workflow_v2 import resolve


def run(entry, *, context=None, states=None):
    context = context or {
        "_mapping_language": "interop-template/v1",
        "interop_input": {},
        "nodes": {},
    }
    return image_references([import_value(entry)], context, states or {}, resolve)


@pytest.mark.parametrize("value", [None, "", []])
def test_nil_optional_image_omits_whole_reference_even_with_suffix(value):
    context = {"_mapping_language": "interop-template/v1", "interop_input": {"photo": value}}
    assert run("{{workflow.input.photo}}:2", context=context) == []


def test_absent_optional_input_and_completed_nil_node_omit_image():
    assert run("{{workflow.input.photo}}") == []
    assert (
        run(
            "{{producer.output.photo}}",
            states={"producer": {"status": "completed", "output": None}},
        )
        == []
    )
    assert (
        run(
            "{{producer.output.photo}}",
            states={"producer": {"status": "skipped", "reason": "pruned"}},
        )
        == []
    )


@pytest.mark.parametrize("status", ["failed", "running", "waiting", "skipped"])
def test_failed_unfinished_or_unresolved_node_cannot_silently_drop_image(status):
    with pytest.raises(ValueError):
        run(
            "{{producer.output.photo}}",
            states={"producer": {"status": status, "reason": "unresolved", "output": {}}},
        )


def test_completed_missing_field_fails_but_explicit_nil_is_optional():
    context = {
        "_mapping_language": "interop-template/v1",
        "nodes": {"producer": {}},
        "interop_input": {},
    }
    with pytest.raises(ValueError, match="missing upstream"):
        run(
            "{{producer.output.photo}}",
            context=context,
            states={"producer": {"status": "completed", "output": {}}},
        )
    context["nodes"]["producer"]["photo"] = None
    assert (
        run(
            "{{producer.output.photo}}",
            context=context,
            states={"producer": {"status": "completed", "output": {"photo": None}}},
        )
        == []
    )
    context["nodes"]["producer"]["photo"] = "photo.png:2"
    assert run(
        "{{producer.output.photo}}",
        context=context,
        states={"producer": {"status": "completed", "output": {"photo": "photo.png:2"}}},
    ) == ["photo.png:2"]


def test_switch_unresolved_target_remains_distinct_from_intentionally_pruned_target():
    from nexus.core.studio_interop_conditions import parse_condition

    graph = {
        "nodes": [
            {
                "id": "route",
                "kind": "switch",
                "result_mode": "interop",
                "cases": [
                    {
                        "condition": parse_condition('{{producer.output.missing}} == "x"'),
                        "value": "uncertain",
                    },
                    {"condition": parse_condition("true"), "value": "chosen"},
                    {
                        "condition": parse_condition('{{producer.output.missing}} == "y"'),
                        "value": "pruned",
                    },
                ],
            },
            {"id": "producer", "kind": "tool"},
        ]
    }
    outputs = {
        "producer": {},
        "route": {"selected_case_index": 1, "selected_branch": "chosen", "cases_unresolved": 1},
    }
    for target, expected in [("uncertain", "unresolved"), ("pruned", "pruned")]:
        assert (
            skipped_reason(
                graph,
                target,
                [{"source": "route", "target": target, "choice": target}],
                {"producer": "completed", "route": "completed"},
                outputs,
                {},
                "root",
                {},
            )
            == expected
        )


def test_broader_artifact_reference_normalization_and_multi_artifact_tool_output(tmp_path):
    from nexus.core.studio import StudioService
    from nexus.core.studio_decision_images import normalize_artifact_reference
    from nexus.core.studio_workflow_artifacts import tool_output

    assert normalize_artifact_reference("artifact://workspace/scan.png@2") == ("scan.png", 2)
    assert normalize_artifact_reference("artifact://session/scan.png?version=3") == (
        "scan.png",
        3,
    )
    assert normalize_artifact_reference("artifacts/scan.png#v4") == ("scan.png", 4)
    assert normalize_artifact_reference({"filename": "scan.png", "revision": 5}) == (
        "scan.png",
        5,
    )
    assert normalize_artifact_reference({"uri": "artifact://user/scan.png:6"}) == ("scan.png", 6)
    for invalid in (
        "artifact://workspace/../secret.png",
        "artifact://workspace/scan.png:1?version=2",
        {"id": "scan", "filename": "scan.png"},
        {"id": "scan", "revision": 0},
    ):
        with pytest.raises(ValueError):
            normalize_artifact_reference(invalid)

    studio = StudioService(tmp_path)
    try:
        first = studio.catalog.save(
            "artifacts",
            {
                "name": "First",
                "data": {"filename": "first.png", "content_base64": "MQ=="},
            },
        )
        second = studio.catalog.save(
            "artifacts",
            {
                "name": "Second",
                "data": {"filename": "second.png", "content_base64": "Mg=="},
            },
        )
        for item in (first, second):
            item["generated_by_run"] = "run-1"
            studio._write(studio.catalog._path("artifacts", item["id"]), item)
        out = tool_output(
            studio,
            {
                "artifacts": [
                    {"id": first["id"], "revision": first["revision"]},
                    {"id": second["id"], "revision": second["revision"]},
                ]
            },
            "run-1",
            None,
        )
        assert out["created_artifacts"] == [
            f"{first['id']}:{first['revision']}",
            f"{second['id']}:{second['revision']}",
        ]
    finally:
        studio.close()
