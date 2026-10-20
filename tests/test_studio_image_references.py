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
