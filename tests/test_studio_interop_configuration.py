"""External import/export contracts: no silent loss, execution or credential exposure."""

import json
from pathlib import Path

import pytest
import yaml

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_interop_mappings import LANGUAGE, export_value, import_value
from nexus.core.studio_workflow_v2 import resolve
from tests.test_studio_workflow_v2 import wait

SOURCE = Path(__file__).parent / "fixtures/studio/interop/review.yaml"
BINDINGS = {
    "models": {"subscription": {"provider": "codex"}},
    "tools": {"count_words": "text:wordcount"},
}


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def request():
    return {"text": SOURCE.read_text(), "bindings": BINDINGS}


def test_convert_plan_apply_execute_export_roundtrip(tmp_path):
    contexts = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            contexts.append(context)
            return AgentResult(success=True, output='{"review":"ok"}')

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        report = studio.configuration.convert_interop(request(), None)
        assert report["valid"], report
        assert not studio.definitions("agents") and not studio.records("configuration-plans")
        assert not contexts
        native = report["document"]
        plan = studio.configuration.plan(native, None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        result = wait(
            studio,
            studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": "count-review",
                    "task": "Review",
                    "workflow_input": {"text": "one two three"},
                }
            ),
            "completed",
        )
        assert json.loads(result["output"]) == {"result": {"review": "ok"}}
        assert "Word count: 3" in contexts[0].prompt
        exported = studio.configuration.export_interop({}, None)
        assert exported["valid"], exported
        imported = studio.configuration.convert_interop({"text": json.dumps(exported["bundle"])}, None)
        assert imported["valid"], imported
        assert imported["document"] == native
        assert len(contexts) == 1
    finally:
        studio.close()


def test_raw_agent_and_named_workflow_forward_reference(studio):
    docs = [
        {
            "agent_name": "raw-agent",
            "description": "Plain instructions",
            "instruction": "Reply simply",
            "agent_card": {"description": "Plain instructions", "skills": []},
        },
        {
            "agent_name": "parent",
            "agent_type": "workflow",
            "nodes": [{"id": "child", "type": "workflow", "workflow_name": "child"}],
            "output_mapping": {"answer": "{{child.output}}"},
        },
        {
            "agent_name": "child",
            "agent_type": "workflow",
            "nodes": [{"id": "answer", "type": "agent", "agent_name": "raw-agent"}],
            "output_mapping": {"answer": "{{answer.output}}"},
        },
    ]
    report = studio.configuration.convert_interop(
        {"documents": docs, "bindings": {"agents": {"raw-agent": {"provider": "claude-code"}}}},
        None,
    )
    assert report["valid"], report
    assert report["document"]["definitions"][0]["document"]["spec"]["provider"] == "claude-code"
    assert not studio.definitions("workflows")


def test_every_unsupported_field_is_reported_without_partial_document_or_secrets(studio):
    docs = list(yaml.safe_load_all(SOURCE.read_text()))
    docs[0]["spec"].update(deploy=True, api_key="PRIVATE_SECRET_VALUE", unknownFlag=True)
    docs[1]["spec"]["appConfig"].update(
        workflow_timeout="30m", broker={"password": "PRIVATE_BROKER_SECRET"}
    )
    docs[1]["spec"]["appConfig"]["nodes"][0].update(retry_strategy={"limit": 4}, when="x")
    report = studio.configuration.convert_interop({"documents": docs, "bindings": BINDINGS}, None)
    paths = {d["path"] for d in report["diagnostics"]}
    assert {
        "/documents/0/spec/deploy",
        "/documents/0/spec/api_key",
        "/documents/0/spec/unknownFlag",
        "/documents/1/spec/appConfig/workflow_timeout",
        "/documents/1/spec/appConfig/broker",
        "/documents/1/spec/appConfig/nodes/0/retry_strategy",
        "/documents/1/spec/appConfig/nodes/0/when",
    } <= paths
    assert not report["valid"] and report["document"] is None
    assert "PRIVATE_" not in json.dumps(report)
    assert not studio.records("configuration-plans") and not studio.definitions("agents")


@pytest.mark.parametrize(
    "text",
    [
        "a: 1\na: 2",
        "a: &x [1]\nb: *x",
        "!!python/object/apply:os.system ['echo bad']",
        "[",
        "[" * 60 + "0" + "]" * 60,
    ],
)
def test_yaml_parser_rejects_duplicate_alias_tag_and_deep_inputs(studio, text):
    report = studio.configuration.convert_interop({"text": text}, None)
    assert not report["valid"] and report["diagnostics"][0]["code"] == "invalid_yaml"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.pop("bindings"),
        lambda d: d["bindings"].update(agents=3),
        lambda d: d["bindings"]["models"].update(subscription=7),
        lambda d: d["bindings"]["models"]["subscription"].update(api_key="secret"),
        lambda d: d.update(documents=[]),
        lambda d: d.update(text="x" * (1024 * 1024 + 1)),
    ],
)
def test_invalid_or_ambiguous_binding_is_not_implicitly_replaced(studio, change):
    import copy

    data = copy.deepcopy(request())
    change(data)
    report = studio.configuration.convert_interop(data, None)
    assert not report["valid"] and report["document"] is None


@pytest.mark.parametrize(
    "value",
    [
        "{{workflow.input.text}}",
        "Read {{first.output.value}} for {{workflow.input.name}}",
        {"n": "{{count.output}}"},
        ["literal", "{{workflow.input}}"],
    ],
)
def test_mapping_templates_roundtrip_without_evaluation(value):
    mapped = import_value(value)
    assert export_value(mapped, LANGUAGE) == value


def test_template_runtime_preserves_types_and_legacy_literal_objects():
    value = import_value("Read {{workflow.input.text}}: {{count.output}}")
    context = {
        "input": {"text": "hello"},
        "nodes": {"count": [1, 2]},
        "_mapping_language": LANGUAGE,
    }
    assert resolve(value, context) == "Read hello: [1, 2]"
    literal = {"template": ["unchanged", {"ref": "input.text"}]}
    assert resolve(literal, {"input": {"text": "hello"}}) == {"template": ["unchanged", "hello"]}
    with pytest.raises(ValueError):
        import_value("{{workflow.input | dangerous}}")
    with pytest.raises(ValueError):
        resolve({"template": ["x" * 65537]}, context)


def test_unknown_references_and_bad_schemas_fail_before_saving(studio):
    data = request()
    data["text"] = data["text"].replace("{{count.output}}", "{{missing.output}}")
    report = studio.configuration.convert_interop(data, None)
    assert not report["valid"] and not studio.definitions("agents")


def test_export_refuses_unsupported_resources_and_node_semantics(studio):
    native = studio.configuration.convert_interop(request(), None)["document"]
    native["resources"] = [
        {"kind": "prompts", "id": "notes", "name": "Notes", "data": {"content": "Hello"}}
    ]
    report = studio.configuration.export_interop({"document": native}, None)
    assert not report["valid"] and report["bundle"] is None
    assert any(d["code"] == "unsupported_resource" for d in report["diagnostics"])


def test_owner_authorization_precedes_source_parsing(studio):
    class Team:
        pass

    studio.team = Team()
    for actor in [None, {"role": "operator"}]:
        with pytest.raises(PermissionError):
            studio.configuration.convert_interop({"text": "bad"}, actor)
        with pytest.raises(PermissionError):
            studio.configuration.export_interop({}, actor)
    studio.team = None


def test_authenticated_http_cli_conversion_review_apply_export(tmp_path):
    import threading
    from argparse import Namespace
    from http.server import ThreadingHTTPServer

    from nexus.core.studio_config_cli import configuration_command
    from nexus.studio import make_studio_handler

    studio = StudioService(tmp_path / "workspace")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "synthetic-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source, bindings, converted, planned, released, exported = [
        tmp_path / name
        for name in [
            "source.yaml",
            "bindings.json",
            "converted.json",
            "plan.json",
            "release.json",
            "export.json",
        ]
    ]
    source.write_text(SOURCE.read_text())
    bindings.write_text(json.dumps(BINDINGS))

    def run(operation, file, output, mapping=None):
        configuration_command(
            Namespace(
                operation=operation,
                file=str(file) if file else None,
                output=str(output),
                bindings=str(mapping) if mapping else None,
                url=f"http://127.0.0.1:{server.server_port}",
                session_env=None,
            )
        )

    try:
        run("convert-interop", source, converted, bindings)
        assert json.loads(converted.read_text())["valid"]
        assert converted.stat().st_mode & 0o777 == 0o600
        assert not studio.definitions("agents")
        run("plan", converted, planned)
        assert not studio.definitions("agents")
        run("apply", planned, released)
        assert json.loads(released.read_text())["status"] == "active"
        run("export-interop", None, exported)
        assert json.loads(exported.read_text())["format"] == "nexus-interop-bundle/v1"
        assert not studio.records("runs")
        count = len(studio.records("configuration-plans"))
        source.write_text("kind: agent\nname: bad-agent\nspec:\n  secret: PRIVATE_VALUE\n")
        with pytest.raises(SystemExit) as error:
            run("convert-interop", source, converted)
        assert error.value.code == 2
        assert json.loads(converted.read_text())["document"] is None
        assert "PRIVATE_VALUE" not in converted.read_text()
        assert len(studio.records("configuration-plans")) == count
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.close()


def test_new_template_operator_requires_explicit_language_and_upstream_dependency(studio):
    data = request()
    data["text"] = data["text"].replace("depends_on: [count]", "depends_on: []")
    report = studio.configuration.convert_interop(data, None)
    assert not report["valid"] and report["document"] is None
    graph = {
        "version": 2,
        "mapping_language": LANGUAGE,
        "nodes": [
            {
                "id": "bad",
                "kind": "switch",
                "cases": [
                    {
                        "condition": {"left": True, "op": "eq", "right": True},
                        "value": {"template": ["safe", 42]},
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="text and data references"):
        studio.graph.validate(graph)


@pytest.mark.parametrize(
    "value",
    [
        None,
        3,
        [],
        {"documents": None},
        {"documents": [{"kind": []}]},
        {
            "documents": [{"kind": "agent", "name": "valid", "spec": 7}],
            "bindings": {"models": {"general": {"provider": []}}},
        },
        {
            "documents": [
                {
                    "agent_name": "valid",
                    "agent_type": "workflow",
                    "nodes": [{"id": "x", "type": {}}],
                    "output_mapping": {},
                }
            ]
        },
    ],
)
def test_malformed_shapes_return_reports_without_server_errors(studio, value):
    report = studio.configuration.convert_interop(value, None)
    assert not report["valid"] and report["document"] is None


def test_environment_templates_and_name_collisions_need_explicit_changes(studio):
    docs = list(yaml.safe_load_all(SOURCE.read_text()))
    docs[1]["spec"]["appConfig"]["nodes"][0]["input"]["text"] = "${PRIVATE_ENV_REFERENCE}"
    report = studio.configuration.convert_interop({"documents": docs, "bindings": BINDINGS}, None)
    assert not report["valid"]
    assert "PRIVATE_ENV_REFERENCE" not in json.dumps(report)
    docs = [docs[0], {**docs[0], "name": "Reviewer!"}]
    report = studio.configuration.convert_interop({"documents": docs, "bindings": BINDINGS}, None)
    assert any(d["code"] == "duplicate_identity" for d in report["diagnostics"])


def test_complex_interop_nodes_and_deployment_resources_are_never_approximated(studio):
    docs = [
        {"kind": "connector", "name": "broker", "spec": {"password": "PRIVATE_BROKER_SECRET"}},
        {
            "agent_name": "repeat",
            "agent_type": "workflow",
            "nodes": [
                {"id": "loop", "type": "loop", "node": "body", "condition": "true"},
                {"id": "map", "type": "map", "node": "body", "items": "{{workflow.input.items}}"},
                {"id": "branch", "type": "switch", "cases": []},
            ],
            "output_mapping": {},
        },
    ]
    report = studio.configuration.convert_interop({"documents": docs}, None)
    assert not report["valid"] and report["document"] is None
    assert sum(d["code"] == "missing_body" for d in report["diagnostics"]) == 2
    assert any(d["code"] == "invalid_cases" for d in report["diagnostics"])
    assert any(d["code"] == "unsupported_kind" for d in report["diagnostics"])
    assert "PRIVATE_BROKER_SECRET" not in json.dumps(report)
    assert not studio.records("runs") and not studio.records("configuration-plans")


def test_export_preserves_original_interop_model_and_tool_references(studio):
    native = studio.configuration.convert_interop(request(), None)["document"]
    report = studio.configuration.export_interop({"document": native}, None)
    assert report["valid"], report
    agent, workflow = report["bundle"]["documents"]
    assert agent["spec"]["modelProvider"] == ["subscription"]
    assert workflow["spec"]["appConfig"]["nodes"][0]["tool_name"] == "count_words"
    assert report["bundle"]["bindings"] == BINDINGS
    imported = studio.configuration.convert_interop({"documents": [report["bundle"]]}, None)
    assert imported["valid"] and imported["document"] == native


def test_named_workflow_cycle_rejected_during_conversion_before_plan(studio):
    docs = [
        {
            "agent_name": name,
            "agent_type": "workflow",
            "nodes": [{"id": "child", "type": "workflow", "workflow_name": target}],
            "output_mapping": {"answer": "{{child.output}}"},
        }
        for name, target in [("first", "second"), ("second", "first")]
    ]
    report = studio.configuration.convert_interop({"documents": docs}, None)
    assert not report["valid"] and report["document"] is None
    assert len([d for d in report["diagnostics"] if d["path"].endswith("/graph")]) == 2
    assert not studio.definitions("workflows") and not studio.records("configuration-plans")


def test_agent_max_tool_calls_per_task_converts_and_roundtrips(studio):
    doc = {
        "kind": "agent",
        "name": "bounded-tools-agent",
        "description": "Agent with bounded tool call limit",
        "spec": {
            "systemPrompt": "Use tools carefully.",
            "modelProvider": ["subscription"],
            "additionalConfigurations": {
                "max_tool_calls_per_task": 12,
                "max_llm_calls_per_task": 20,
            },
        },
    }
    report = studio.configuration.convert_interop({"documents": [doc], "bindings": BINDINGS}, None)
    assert report["valid"], report
    spec = report["document"]["definitions"][0]["document"]["spec"]
    assert spec["max_tool_calls"] == 12
    assert spec["max_model_calls"] == 20
    exported = studio.configuration.export_interop({"document": report["document"]}, None)
    assert exported["valid"], exported
    assert (
        exported["bundle"]["documents"][0]["spec"]["additionalConfigurations"][
            "max_tool_calls_per_task"
        ]
        == 12
    )
