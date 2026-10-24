"""Nuclia descriptors bind without importing code, widening actions or losing settings."""

import copy
import json

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_enterprise import validate_enterprise
from nexus.core.studio_nuclia import request_body
from nexus.core.studio_nuclia_descriptors import convert_tool_descriptor
from nexus.core.studio_nuclia_interop import POLICY


def descriptor():
    return {
        "tool_type": "python",
        "component_module": "uninstalled_nuclia_tool.nuclia_rag_tool",
        "class_name": "NucliaRagTool",
        "tool_config": {
            "tool_name": "CompanyKnowledge",
            "base_url": "https://knowledge.example/api/v1/",
            "kb_id": "company",
            "token": "${NUCLIA_TOKEN}",
            "account_id": "${NUCLIA_ACCOUNT}",
            "top_k": 7,
            "user_prompt": "Use {context} to answer {question}",
            "template_parameters": [{"name": "region", "required": True}],
            "prompt_rephrasing": {"template": "Region: {region}; {query}"},
            "filter_expression_template": {
                "field": {"prop": "label", "labelset": "region", "label": "{region}"}
            },
        },
    }


def import_request(source=None):
    return {
        "documents": [
            {
                "agent_name": "knowledge-helper",
                "instruction": "Use company evidence.",
                "tools": [source or descriptor()],
            }
        ],
        "bindings": {
            "agents": {"knowledge-helper": {"provider": "codex"}},
            "nuclia": {"CompanyKnowledge": {"connector_id": "knowledge", "policy": POLICY}},
        },
    }


def configure(studio, source=None):
    converted = convert_tool_descriptor(source or descriptor(), policy=POLICY)
    return studio.catalog.save(
        "connectors", {"id": "knowledge", "name": "Knowledge", "data": converted["connector"]}
    )


def test_descriptor_preserves_request_settings_and_only_credential_references():
    source = descriptor()
    before = copy.deepcopy(source)
    result = convert_tool_descriptor(source, policy=POLICY)
    native = validate_enterprise(result["connector"])
    assert source == before
    assert native["url"] == "https://knowledge.example"
    assert native["token_env"] == "NUCLIA_TOKEN"
    assert native["actions"] == ["ask"] and native["enabled"] is False
    body = request_body(
        "ask",
        {"query": "Policy?", "parameters": {"region": "emea"}},
        native["template"],
        ask_settings=native["ask_settings"],
    )
    assert body == {
        "query": "Region: emea; Policy?",
        "top_k": 7,
        "citations": "default",
        "rephrase": False,
        "prefer_markdown": True,
        "prompt": {"user": "Use {context} to answer {question}"},
        "filter_expression": {"field": {"prop": "label", "labelset": "region", "label": "emea"}},
    }
    assert len(result["adaptations"]) == 12
    result["connector"]["template"]["parameters"].clear()
    assert source == before


def test_source_defaults_legacy_parameters_and_query_compatibility():
    source = descriptor()
    config = source["tool_config"]
    config.pop("top_k")
    config.pop("user_prompt")
    config["context_parameters"] = config.pop("template_parameters")
    native = convert_tool_descriptor(source, policy=POLICY)["connector"]
    assert native["ask_settings"] == {"top_k": 5, "rephrase": False, "prefer_markdown": True}
    # Old native connections retain their original 10-result default.
    assert request_body("ask", {"query": "Policy?"})["top_k"] == 10
    assert request_body("query", {"query": "Policy?"}, ask_settings=native["ask_settings"]) == {
        "query": "Policy?",
        "top_k": 10,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.update(component_module="arbitrary.execute"),
        lambda s: s.update(extra="PRIVATE_SECRET"),
        lambda s: s["tool_config"].update(token="PRIVATE_SECRET"),
        lambda s: s["tool_config"].update(api_key="PRIVATE_SECRET"),
        lambda s: s["tool_config"].update(
            base_url="https://user:PRIVATE_SECRET@knowledge.example/api/v1"
        ),
        lambda s: s["tool_config"].update(base_url="${PRIVATE_SECRET}/api/v1"),
        lambda s: s["tool_config"].update(kb_id="${PRIVATE_SECRET}"),
        lambda s: s["tool_config"].update(top_k=51),
        lambda s: s["tool_config"].update(top_k=True),
        lambda s: s["tool_config"].update(rephrase="yes"),
        lambda s: s["tool_config"].update(prefer_markdown=1),
        lambda s: s["tool_config"].update(user_prompt={"secret": "PRIVATE_SECRET"}),
        lambda s: s["tool_config"].update(citations="none"),
        lambda s: s["tool_config"].update(output_response_as_artifact=False),
        lambda s: s["tool_config"].update(inline_citation_links=False),
        lambda s: s["tool_config"].update(include_citations_in_tool_response=True),
        lambda s: s["tool_config"].update(output_filename_base="PRIVATE_SECRET"),
        lambda s: s["tool_config"].update(artifact_description_query_max_length=True),
        lambda s: s["tool_config"].update(remi_publish_topic="PRIVATE_SECRET"),
        lambda s: s["tool_config"].update(audit_metadata={"enabled": True}),
        lambda s: s["tool_config"].update(context_parameters=[]),
        lambda s: s["tool_config"].update(top_k=float("nan")),
    ],
)
def test_unsupported_settings_fail_without_values_or_writes(mutation, tmp_path):
    source = descriptor()
    mutation(source)
    with pytest.raises((TypeError, ValueError)) as error:
        convert_tool_descriptor(source, policy=POLICY)
    assert "PRIVATE_SECRET" not in str(error.value)
    studio = StudioService(tmp_path)
    try:
        report = studio.configuration.convert_interop(import_request(source), None)
        assert not report["valid"] and report["document"] is None
        assert "PRIVATE_SECRET" not in json.dumps(report)
        assert not studio.definitions("agents")
        assert not studio.catalog.list("connectors")
    finally:
        studio.close()


@pytest.mark.parametrize(
    "change",
    [
        {"top_k": 1},
        {"rephrase": True},
        {"prompt": {"user": "Override"}},
        {"prefer_markdown": False},
    ],
)
def test_fixed_settings_cannot_be_overridden(change):
    native = convert_tool_descriptor(descriptor(), policy=POLICY)["connector"]
    with pytest.raises(ValueError, match="overridden"):
        request_body("ask", {"query": "Policy?", **change}, ask_settings=native["ask_settings"])


@pytest.mark.parametrize("backend", ["files", "sqlite"])
def test_import_plan_apply_export_roundtrip_keeps_connection_disabled(tmp_path, backend):
    studio = StudioService(
        tmp_path / "workspace",
        state_options={"url": f"sqlite:///{tmp_path}/state.sqlite", "namespace": "descriptor"}
        if backend == "sqlite"
        else None,
    )
    try:
        configure(studio)
        request = import_request()
        report = studio.configuration.convert_interop(request, None)
        assert report["valid"], report
        assert report["adaptations"][0]["policy"] == POLICY
        assert report["document"]["definitions"][0]["document"]["spec"]["connector_ids"] == [
            "knowledge"
        ]
        assert not studio.definitions("agents")
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        assert not studio.catalog.get("connectors", "knowledge")["data"]["enabled"]
        exported = studio.configuration.export_interop({"document": report["document"]}, None)
        assert exported["valid"], exported
        roundtrip = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
        assert roundtrip["valid"], roundtrip
        assert roundtrip["document"] == report["document"]
    finally:
        studio.close()


def test_existing_source_metadata_does_not_gain_empty_nuclia_bindings(tmp_path):
    studio = StudioService(tmp_path)
    try:
        report = studio.configuration.convert_interop(
            {
                "documents": [
                    {
                        "agent_name": "ordinary",
                        "instruction": "Use provided text.",
                        "request_timeout_seconds": 20,
                    }
                ],
                "bindings": {"agents": {"ordinary": {"provider": "codex"}}},
            },
            None,
        )
        assert report["valid"], report
        original = report["document"]["definitions"][0]["document"]["metadata"][
            "interop_agent_source"
        ]
        assert "nuclia" not in original["bindings"]
        assert "adaptations" not in report
    finally:
        studio.close()


@pytest.mark.parametrize(
    "mismatch", ["endpoint", "box", "token", "template", "settings", "actions", "policy", "unknown"]
)
def test_binding_mismatches_reject_whole_import(tmp_path, mismatch):
    studio = StudioService(tmp_path)
    try:
        configure(studio)
        request = import_request()
        config = request["documents"][0]["tools"][0]["tool_config"]
        if mismatch == "endpoint":
            config["base_url"] = "https://other.example/api/v1"
        elif mismatch == "box":
            config["kb_id"] = "another"
        elif mismatch == "token":
            config["token"] = "${ANOTHER_TOKEN}"
        elif mismatch == "template":
            config["template_parameters"][0]["required"] = False
        elif mismatch == "settings":
            config["top_k"] = 3
        elif mismatch == "actions":
            native = studio.catalog.get("connectors", "knowledge")
            native["data"]["actions"].append("query")
            studio.catalog.save("connectors", native)
        else:
            request["bindings"]["nuclia"]["CompanyKnowledge"].update(
                {"policy": "implicit"} if mismatch == "policy" else {"unexpected": True}
            )
        report = studio.configuration.convert_interop(request, None)
        assert not report["valid"] and report["document"] is None, report
        assert not studio.definitions("agents") and not studio.records("configuration-plans")
    finally:
        studio.close()
