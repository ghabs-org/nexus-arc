"""External control semantics on the shared engine, including effect and recovery bounds."""

import copy
import json

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_interop_conditions import evaluate_condition, parse_condition
from nexus.core.studio_interop_values import MissingSAMValueError, import_value, resolve_value
from nexus.tools import tool_registry
from tests.test_studio_workflow_v2 import wait


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def apply(studio, source, bindings):
    report = studio.configuration.convert_interop(
        {"documents": [source], "bindings": {"tools": bindings}}, None
    )
    assert report["valid"], report
    plan = studio.configuration.plan(report["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    return report["document"]


def run(studio, name, value, status="completed"):
    return wait(
        studio,
        studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": name,
                "task": "Synthetic external fixture",
                "workflow_input": value,
            }
        ),
        status,
    )


def test_map_preserves_original_input_outer_capture_order_and_aliases(studio):
    source = {
        "agent_name": "mapped",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count_words"}],
        "nodes": [
            {
                "id": "prefix",
                "type": "tool",
                "tool_name": "count_words",
                "input": {"text": "prefix"},
            },
            {
                "id": "each",
                "type": "map",
                "depends_on": ["prefix"],
                "node": "body",
                "items": "{{workflow.input.entries}}",
                "concurrency_limit": 2,
            },
            {
                "id": "body",
                "type": "tool",
                "tool_name": "count_words",
                "input": {
                    "text": {
                        "concat": [
                            "{{_map_item.text}}",
                            " ",
                            "{{workflow.input.suffix}}",
                            " {{prefix.output}}",
                        ]
                    }
                },
            },
        ],
        "output_mapping": {"counts": "{{each.output}}"},
    }
    native = apply(studio, source, {"count_words": "text:wordcount"})
    result = run(
        studio, "mapped", {"entries": [{"text": "one two"}, {"text": "three"}], "suffix": "tail"}
    )
    assert json.loads(result["output"]) == {"counts": [4, 3]}
    exported = studio.configuration.export_interop({"document": native}, None)
    assert exported["valid"], exported
    assert exported["bundle"]["documents"][0]["spec"]["appConfig"] == source
    assert (
        studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)["document"]
        == native
    )


def test_shared_map_template_prunes_each_item_before_resolving_its_input(studio):
    source = {
        "agent_name": "shared-map",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count_words"}],
        "nodes": [
            {
                "id": "first",
                "type": "map",
                "node": "body",
                "items": "{{workflow.input.first}}",
                "concurrency_limit": 2,
            },
            {
                "id": "second",
                "type": "map",
                "node": "body",
                "items": "{{workflow.input.second}}",
                "concurrency_limit": 2,
            },
            {
                "id": "body",
                "type": "tool",
                "tool_name": "count_words",
                "when": "{{_map_item.enabled}} == true",
                "input": {"text": "{{_map_item.text}}"},
            },
        ],
        "output_mapping": {"first": "{{first.output}}", "second": "{{second.output}}"},
    }
    native = apply(studio, source, {"count_words": "text:wordcount"})
    result = run(
        studio,
        "shared-map",
        {
            "first": [{"enabled": True, "text": "one two"}, {"enabled": False}],
            "second": [{"enabled": False}, {"enabled": True, "text": "three"}],
        },
    )
    assert json.loads(result["output"]) == {"first": [2, None], "second": [None, 1]}
    scopes = studio._read(studio._path("graph-runs", result["id"]))["nodes"]
    assert scopes["root/first/1/body"] == "skipped"
    assert scopes["root/second/0/body"] == "skipped"
    assert scopes["root/first/0/body"] == scopes["root/second/1/body"] == "completed"
    exported = studio.configuration.export_interop({"document": native}, None)
    assert exported["valid"], exported


def test_loop_body_when_pruned_returns_nil_alias_without_resolving_input(studio):
    source = {
        "agent_name": "pruned-loop",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count_words"}],
        "nodes": [
            {"id": "repeat", "type": "loop", "node": "body", "condition": "false"},
            {
                "id": "body",
                "type": "tool",
                "tool_name": "count_words",
                "when": "false",
                "input": {"text": "{{workflow.input.absent}}"},
            },
        ],
        "output_mapping": {"loop": "{{repeat.output}}", "body": "{{body.output}}"},
    }
    apply(studio, source, {"count_words": "text:wordcount"})
    assert json.loads(run(studio, "pruned-loop", {})["output"]) == {
        "loop": {"iterations_completed": 1, "stopped_reason": "condition_false"},
        "body": None,
    }


def test_loop_summary_body_alias_and_max_limit(studio):
    source = {
        "agent_name": "repeated",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count_words"}],
        "nodes": [
            {
                "id": "repeat",
                "type": "loop",
                "node": "body",
                "condition": "{{_loop_iteration}} < 2",
                "max_iterations": 3,
            },
            {
                "id": "body",
                "type": "tool",
                "tool_name": "count_words",
                "input": {"text": "{{workflow.input.text}}"},
            },
        ],
        "output_mapping": {"loop": "{{repeat.output}}", "body": "{{body.output}}"},
    }
    apply(studio, source, {"count_words": "text:wordcount"})
    result = run(studio, "repeated", {"text": "one two"})
    assert json.loads(result["output"]) == {
        "loop": {"iterations_completed": 3, "stopped_reason": "condition_false"},
        "body": 2,
    }
    source["nodes"][0]["condition"] = "true"
    source["agent_name"] = "at-limit"
    apply(studio, source, {"count_words": "text:wordcount"})
    result = run(studio, "at-limit", {"text": "one"})
    assert json.loads(result["output"])["loop"] == {
        "iterations_completed": 3,
        "stopped_reason": "max_iterations",
    }


def test_switch_gates_over_unconditional_dependency_and_coalesces(studio):
    calls = []

    async def record(label):
        calls.append(label)
        return label

    tool_registry.register("interop:record", record, "Synthetic selected branch")
    try:
        source = {
            "agent_name": "branching",
            "agent_type": "workflow",
            "tools": [{"tool_type": "builtin", "tool_name": "record"}],
            "nodes": [
                {"id": "ready", "type": "tool", "tool_name": "record", "input": {"label": "ready"}},
                {
                    "id": "select",
                    "type": "switch",
                    "cases": [
                        {"condition": "'{{workflow.input.kind}}' == 'first'", "node": "first"}
                    ],
                    "default": "second",
                },
                {
                    "id": "first",
                    "type": "tool",
                    "tool_name": "record",
                    "depends_on": ["select", "ready"],
                    "input": {"label": "first"},
                },
                {
                    "id": "second",
                    "type": "tool",
                    "tool_name": "record",
                    "depends_on": ["select", "ready"],
                    "input": {"label": "second"},
                },
            ],
            "output_mapping": {
                "selected": "{{select.output}}",
                "result": {"coalesce": ["{{first.output}}", "{{second.output}}"]},
            },
        }
        apply(studio, source, {"record": "interop:record"})
        result = run(studio, "branching", {"kind": "second"})
        assert calls == ["ready", "second"]
        assert json.loads(result["output"]) == {
            "selected": {
                "selected_branch": "second",
                "selected_case_index": -1,
                "cases_unresolved": 0,
            },
            "result": "second",
        }
    finally:
        tool_registry.unregister("interop:record", func=record)


@pytest.mark.parametrize(
    "expression,inputs,expected",
    [
        ("'{{workflow.input.kind}}' == 'yes'", {"kind": "yes"}, True),
        ("'{{workflow.input.missing}}' != ''", {}, None),
        ("not ('{{workflow.input.missing}}' == 'yes')", {}, None),
        ("'{{workflow.input.missing}}' == 'yes' or true", {}, True),
        ("'{{workflow.input.missing}}' == 'yes' and false", {}, False),
        (
            "{{workflow.input.n}} >= 2 and '{{workflow.input.kind}}' in ['a','b']",
            {"n": 3, "kind": "a"},
            True,
        ),
    ],
)
def test_deterministic_condition_subset_and_optional_input(expression, inputs, expected):
    parsed = parse_condition(expression)
    answer = evaluate_condition(parsed["interop_condition"], {"input": inputs, "nodes": {}})
    assert answer.value is expected and not answer.unresolved


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('bad')",
        "a.b == 1",
        "{{workflow.input.n}} + 1 > 3",
        "{{workflow.input.n}} == null",
        "1 < 2 < 3",
        "x[0] == 1",
        "{{workflow.input.n}} | default('x')",
        "[0]*100000",
    ],
)
def test_unsupported_expression_is_rejected_before_execution(source):
    with pytest.raises(ValueError):
        parse_condition(source)


def test_extended_sam_conditions_and_indexed_references_roundtrip(studio):
    from nexus.core.studio_interop_conditions import evaluate_condition

    context = {
        "input": {
            "title": "nexus-release-rc1",
            "tags": ["alpha", "beta"],
            "items": [{"name": "first-item"}],
        },
        "nodes": {},
    }
    for expr, expected in (
        ('"gamma" not in {{workflow.input.tags}}', True),
        ('contains({{workflow.input.title}}, "release")', True),
        ('startswith({{workflow.input.title}}, "nexus-")', True),
        ('endswith({{workflow.input.title}}, "-rc1")', True),
        ('{{workflow.input.items[0].name}} == "first-item"', True),
    ):
        tree = parse_condition(expr)["interop_condition"]
        assert evaluate_condition(tree, context).value is expected

    source = {
        "agent_name": "indexed-workflow",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count"}],
        "nodes": [
            {
                "id": "first",
                "type": "tool",
                "tool_name": "count",
                "when": 'startswith({{workflow.input.items[0].text}}, "hello") and "skip" not in {{workflow.input.tags}}',
                "input": {"text": "{{workflow.input.items[0].text}}"},
            }
        ],
        "output_mapping": {"words": "{{first.output}}"},
    }
    native = apply(studio, source, {"count": "text:wordcount"})
    out = json.loads(
        run(
            studio,
            "indexed-workflow",
            {"items": [{"text": "hello world"}], "tags": ["ok"]},
        )["output"]
    )
    assert out == {"words": 2}
    exported = studio.configuration.export_interop({"document": native}, None)
    assert exported["valid"], exported


def test_mapping_missing_input_omission_null_wrong_shape_and_stale_field():
    context = {"input": {}, "nodes": {"done": {}}, "_interop_input_mapping": True}
    assert resolve_value(
        import_value({"optional": "{{workflow.input.optional}}", "node": "{{skipped.output}}"}),
        context,
    ) == {"node": None}
    assert resolve_value(
        import_value({"value": "{{workflow.input.optional}}"}),
        {**context, "input": {"optional": None}},
    ) == {"value": None}
    assert resolve_value(
        import_value({"value": "{{workflow.input.user.id}}"}),
        {**context, "input": {"user": "wrong shape"}},
    ) == {"value": None}
    with pytest.raises(MissingSAMValueError):
        resolve_value(import_value({"coalesce": ["{{done.output.missing}}", "fallback"]}), context)
    assert (
        resolve_value(
            import_value("{{done.output.missing}}"), {**context, "_interop_output_mapping": True}
        )
        == "{{done.output.missing}}"
    )


def test_edited_compiled_graph_cannot_export_stale_origin(studio):
    source = {
        "agent_name": "edited",
        "agent_type": "workflow",
        "nodes": [
            {"id": "route", "type": "switch", "cases": [{"condition": "true", "node": "end"}]},
            {
                "id": "end",
                "type": "tool",
                "depends_on": ["route"],
                "tool_name": "count",
                "input": {"text": "one"},
            },
        ],
        "tools": [{"tool_type": "builtin", "tool_name": "count"}],
        "output_mapping": {"result": "{{end.output}}"},
    }
    native = apply(studio, source, {"count": "text:wordcount"})
    changed = copy.deepcopy(native)
    changed["definitions"][0]["document"]["graph"]["nodes"][1]["kwargs"]["text"] = "different words"
    result = studio.configuration.export_interop({"document": changed}, None)
    assert not result["valid"] and result["bundle"] is None
    assert any(d["code"] == "source_graph_mismatch" for d in result["diagnostics"])


def test_loop_previous_result_and_stale_condition_are_distinct(studio):
    async def increase(previous=None, iteration=0):
        return {"total": (previous or 0) + 1, "iteration": iteration}

    tool_registry.register("interop:increase", increase, "Synthetic loop result")
    try:
        source = {
            "agent_name": "previous",
            "agent_type": "workflow",
            "tools": [{"tool_type": "builtin", "tool_name": "increase"}],
            "nodes": [
                {
                    "id": "repeat",
                    "type": "loop",
                    "node": "body",
                    "condition": "{{body.output.total}} < 2",
                    "max_iterations": 3,
                },
                {
                    "id": "body",
                    "type": "tool",
                    "tool_name": "increase",
                    "input": {
                        "previous": "{{body.output.total}}",
                        "iteration": "{{_loop_iteration}}",
                    },
                },
            ],
            "output_mapping": {"loop": "{{repeat.output}}", "body": "{{body.output}}"},
        }
        apply(studio, source, {"increase": "interop:increase"})
        result = json.loads(run(studio, "previous", {})["output"])
        assert result == {
            "loop": {"iterations_completed": 2, "stopped_reason": "condition_false"},
            "body": {"total": 2, "iteration": 1},
        }
        source["agent_name"] = "unresolved"
        source["nodes"][0]["condition"] = "{{body.output.missing}} != 0"
        apply(studio, source, {"increase": "interop:increase"})
        result = json.loads(run(studio, "unresolved", {})["output"])
        assert result["loop"] == {
            "iterations_completed": 1,
            "stopped_reason": "condition_unresolved",
        }
        source["agent_name"] = "optional-loop"
        source["nodes"][0]["condition"] = "'{{workflow.input.optional}}' != 'stop'"
        apply(studio, source, {"increase": "interop:increase"})
        result = json.loads(run(studio, "optional-loop", {})["output"])
        assert result["loop"] == {"iterations_completed": 1, "stopped_reason": "condition_false"}
    finally:
        tool_registry.unregister("interop:increase", func=increase)


def test_when_skips_before_tool_input_resolution_and_reports_stale_switch_case(studio):
    async def payload():
        return {}

    tool_registry.register("interop:payload", payload, "Synthetic empty output")
    try:
        source = {
            "agent_name": "optional-branch",
            "agent_type": "workflow",
            "tools": [
                {"tool_type": "builtin", "tool_name": "payload"},
                {"tool_type": "builtin", "tool_name": "count"},
            ],
            "nodes": [
                {"id": "data", "type": "tool", "tool_name": "payload", "input": {}},
                {
                    "id": "choose",
                    "type": "switch",
                    "depends_on": ["data"],
                    "cases": [{"when": "'{{data.output.missing}}' != 'no'", "then": "unsafe"}],
                    "default": "safe",
                },
                {
                    "id": "unsafe",
                    "type": "tool",
                    "tool_name": "count",
                    "depends_on": ["choose"],
                    "input": {"text": "{{data.output.missing}}"},
                },
                {
                    "id": "safe",
                    "type": "tool",
                    "tool_name": "count",
                    "depends_on": ["choose"],
                    "input": {"text": "safe"},
                },
                {
                    "id": "optional",
                    "type": "tool",
                    "tool_name": "count",
                    "when": "'{{workflow.input.enabled}}' != ''",
                    "input": {"text": "{{data.output.missing}}"},
                    "depends_on": ["data"],
                },
            ],
            "output_mapping": {
                "branch": "{{choose.output}}",
                "value": {"coalesce": ["{{optional.output}}", "{{safe.output}}"]},
            },
        }
        apply(studio, source, {"payload": "interop:payload", "count": "text:wordcount"})
        result = json.loads(run(studio, "optional-branch", {})["output"])
        assert result == {
            "branch": {"selected_branch": "safe", "selected_case_index": -1, "cases_unresolved": 1},
            "value": 1,
        }
    finally:
        tool_registry.unregister("interop:payload", func=payload)


@pytest.mark.parametrize(
    "items_key", ["items", "with_items", "withItems", "with_param", "withParam"]
)
def test_all_map_items_spellings(studio, items_key):
    source = {
        "agent_name": "item-alias",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count"}],
        "nodes": [
            {"id": "map", "type": "map", "node": "body", items_key: ["one", "two three"]},
            {
                "id": "body",
                "type": "tool",
                "tool_name": "count",
                "input": {"text": "{{_map_item}}"},
            },
        ],
        "output_mapping": {"result": "{{map.output}}"},
    }
    apply(studio, source, {"count": "text:wordcount"})
    assert json.loads(run(studio, "item-alias", {})["output"]) == {"result": [1, 2]}


def test_compiled_parallel_map_checkpoint_recovers_without_repeating_effect(tmp_path):
    calls = []

    async def record(value):
        calls.append(value)
        return value

    tool_registry.register("interop:checkpoint", record, "Synthetic reviewed effect")
    studio = StudioService(tmp_path)
    try:
        studio.save_definition(
            "workflows",
            {
                "name": "Review child",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {"id": "review", "kind": "approval"},
                        {
                            "id": "effect",
                            "kind": "tool",
                            "tool_id": "interop:checkpoint",
                            "kwargs": {"value": {"ref": "input.value"}},
                            "depends_on": ["review"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.effect"},
                },
            },
        )
        source = {
            "agent_name": "recover-map",
            "agent_type": "workflow",
            "nodes": [
                {"id": "each", "type": "map", "node": "body", "with_items": ["first", "second"]},
                {
                    "id": "body",
                    "type": "workflow",
                    "workflow_name": "Review child",
                    "input": {"value": "{{_map_item}}"},
                },
            ],
            "output_mapping": {"values": "{{each.output}}"},
        }
        apply(studio, source, {})
        paused = run(studio, "recover-map", {}, "waiting")
        assert len(paused["awaiting_approval"]) == 2 and calls == []
        studio.close()
        (tmp_path / "workflows" / "review-child.yaml").unlink()
        studio = StudioService(tmp_path, workflow_limits={"map_concurrency": 1})
        result = wait(studio, studio.graph.resume(paused["id"], "approve"), "completed")
        assert json.loads(result["output"]) == {"values": ["first", "second"]}
        assert sorted(calls) == ["first", "second"]
    finally:
        studio.close()
        tool_registry.unregister("interop:checkpoint", func=record)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["nodes"][0].update(concurrency_limit=0),
        lambda s: s["nodes"][0].update(with_items=["x"]),
        lambda s: s["nodes"][0].update(node="missing"),
        lambda s: s["nodes"][1].update(depends_on=["each"]),
        lambda s: s["nodes"][0].update(items=["x"] * 101),
    ],
)
def test_bad_collection_constructs_fail_without_plan_or_effect(studio, change):
    source = {
        "agent_name": "bad-map",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count"}],
        "nodes": [
            {"id": "each", "type": "map", "node": "body", "items": ["x"]},
            {
                "id": "body",
                "type": "tool",
                "tool_name": "count",
                "input": {"text": "{{_map_item}}"},
            },
        ],
        "output_mapping": {"result": "{{each.output}}"},
    }
    change(source)
    report = studio.configuration.convert_interop(
        {"documents": [source], "bindings": {"tools": {"count": "text:wordcount"}}}, None
    )
    assert not report["valid"] and report["document"] is None
    assert not studio.records("runs") and not studio.records("configuration-plans")


@pytest.mark.parametrize(
    "source,expected",
    [
        ("false and (1 > 'x')", False),
        ("true or (1 > 'x')", True),
        ("{{workflow.input.missing}} and (1 > 'x')", None),
    ],
)
def test_conditions_do_not_evaluate_an_unreachable_error_as_a_match(source, expected):
    parsed = parse_condition(source)
    answer = evaluate_condition(parsed["interop_condition"], {"input": {}, "nodes": {}})
    assert answer.value is expected


@pytest.mark.parametrize("source", ["True", "false # Python comment", "r'raw' == 'raw'"])
def test_python_only_condition_syntax_is_rejected(source):
    with pytest.raises(ValueError):
        parse_condition(source)


def test_missing_array_entries_remain_null_and_text_is_bounded():
    context = {"input": {}, "nodes": {}, "_interop_input_mapping": True}
    assert resolve_value(import_value(["{{workflow.input.missing}}"]), context) == [None]
    with pytest.raises(ValueError, match="64 KiB"):
        resolve_value(import_value({"concat": ["x" * 65536, "y"]}), context)


def test_named_interop_child_uses_its_own_workflow_input_in_each_map(studio):
    child = {
        "agent_name": "child-input",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count"}],
        "nodes": [
            {
                "id": "count",
                "type": "tool",
                "tool_name": "count",
                "input": {
                    "text": {"coalesce": ["{{workflow.input.missing}}", "{{workflow.input.value}}"]}
                },
            }
        ],
        "output_mapping": {"count": "{{count.output}}"},
    }
    parent = {
        "agent_name": "parent-input",
        "agent_type": "workflow",
        "nodes": [
            {"id": "each", "type": "map", "node": "child", "with_items": ["one two", "three"]},
            {
                "id": "child",
                "type": "workflow",
                "workflow_name": "child-input",
                "input": {"value": "{{_map_item}}"},
            },
        ],
        "output_mapping": {"counts": "{{each.output}}"},
    }
    report = studio.configuration.convert_interop(
        {"documents": [parent, child], "bindings": {"tools": {"count": "text:wordcount"}}}, None
    )
    assert report["valid"], report
    plan = studio.configuration.plan(report["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    result = json.loads(run(studio, "parent-input", {"value": "wrong parent input"})["output"])
    assert result == {"counts": [{"count": 2}, {"count": 1}]}


def test_loop_inside_map_keeps_each_item_index_and_iteration_scoped(studio):
    calls = []

    async def inspect(item, index, iteration):
        calls.append((item, index, iteration))
        return iteration

    tool_registry.register("interop:inspect", inspect, "Synthetic nested control context")
    try:
        source = {
            "agent_name": "nested-controls",
            "agent_type": "workflow",
            "tools": [{"tool_type": "builtin", "tool_name": "inspect"}],
            "nodes": [
                {"id": "each", "type": "map", "node": "repeat", "with_items": ["first", "second"]},
                {
                    "id": "repeat",
                    "type": "loop",
                    "node": "step",
                    "condition": "{{_loop_iteration}} < 1",
                    "max_iterations": 2,
                },
                {
                    "id": "step",
                    "type": "tool",
                    "tool_name": "inspect",
                    "input": {
                        "item": "{{_map_item}}",
                        "index": "{{_map_index}}",
                        "iteration": "{{_loop_iteration}}",
                    },
                },
            ],
            "output_mapping": {"summary": "{{each.output}}"},
        }
        apply(studio, source, {"inspect": "interop:inspect"})
        result = json.loads(run(studio, "nested-controls", {})["output"])
        assert result == {
            "summary": [{"iterations_completed": 2, "stopped_reason": "condition_false"}] * 2
        }
        assert sorted(calls) == [
            ("first", 0, 0),
            ("first", 0, 1),
            ("second", 1, 0),
            ("second", 1, 1),
        ]
    finally:
        tool_registry.unregister("interop:inspect", func=inspect)


def test_map_switch_body_routes_and_joins_once_per_item(studio):
    calls = []

    async def record(label):
        calls.append(label)
        return label

    tool_registry.register("interop:body-record", record, "Synthetic collection branches")
    try:
        source = {
            "agent_name": "mapped-switch",
            "agent_type": "workflow",
            "tools": [{"tool_type": "builtin", "tool_name": "record"}],
            "nodes": [
                {
                    "id": "each",
                    "type": "map",
                    "node": "select",
                    "items": ["first", "second"],
                    "concurrency_limit": 2,
                },
                {
                    "id": "select",
                    "type": "switch",
                    "cases": [{"condition": "'{{_map_item}}' == 'first'", "node": "first"}],
                    "default": "second",
                },
                {
                    "id": "first",
                    "type": "tool",
                    "tool_name": "record",
                    "depends_on": ["select"],
                    "input": {"label": "first"},
                },
                {
                    "id": "second",
                    "type": "tool",
                    "tool_name": "record",
                    "depends_on": ["select"],
                    "input": {"label": "second"},
                },
                {
                    "id": "joined",
                    "type": "tool",
                    "tool_name": "record",
                    "depends_on": ["first", "second"],
                    "input": {
                        "label": {
                            "concat": [
                                "joined:",
                                {"coalesce": ["{{first.output}}", "{{second.output}}"]},
                            ]
                        }
                    },
                },
            ],
            "output_mapping": {"results": "{{each.output}}"},
        }
        native = apply(studio, source, {"record": "interop:body-record"})
        result = json.loads(run(studio, "mapped-switch", {})["output"])
        assert [item["selected_branch"] for item in result["results"]] == ["first", "second"]
        assert sorted(calls) == ["first", "joined:first", "joined:second", "second"]
        exported = studio.configuration.export_interop({"document": native}, None)
        assert exported["valid"]
        assert (
            studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)[
                "document"
            ]
            == native
        )
    finally:
        tool_registry.unregister("interop:body-record")


@pytest.mark.parametrize("target", [[], {}, 7])
def test_collection_switch_malformed_targets_report_preflight(studio, target):
    source = {
        "agent_name": "bad-body-switch",
        "agent_type": "workflow",
        "nodes": [
            {"id": "each", "type": "map", "node": "select", "items": [1]},
            {"id": "select", "type": "switch", "cases": [{"condition": "true", "node": target}]},
        ],
        "output_mapping": {"results": "{{each.output}}"},
    }
    report = studio.configuration.convert_interop({"documents": [source]}, None)
    assert not report["valid"] and report["document"] is None
    assert not studio.records("runs") and not studio.records("configuration-plans")
