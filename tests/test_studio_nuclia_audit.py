"""Reviewed audit templates send only explicitly selected, bounded scalar metadata."""

import asyncio
import copy
import json

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_nuclia import request_body
from nexus.core.studio_nuclia_descriptors import convert_tool_descriptor
from nexus.core.studio_nuclia_interop import POLICY, convert_template_settings
from nexus.core.studio_nuclia_templates import validate_template
from tests.test_studio_nuclia_descriptors import descriptor, import_request
from tests.test_studio_team import team as team


def template():
    return {
        "version": 1,
        "parameters": [
            {"name": "user", "required": True, "context_path": ["identity", "nexus_id"]},
            {"name": "count", "type": "integer", "default": 3},
            {"name": "approved", "type": "boolean", "default": False},
            {"name": "optional", "nullable": True},
        ],
        "query_prompt": "User {{user}}: {{query}}",
        "audit_metadata": {
            "enabled": True,
            "fields": {
                "user": "{{user}}",
                "question": "{{query}}",
                "environment": "personal",
                "count": "{{count}}",
                "approved": "{{approved}}",
                "optional": "{{optional}}",
                "empty": "",
            },
        },
    }


@pytest.mark.parametrize("action", ["ask", "query"])
def test_metadata_uses_original_query_and_typed_context_without_mutation(action):
    config = template()
    before = copy.deepcopy(config)
    arguments = {"query": "Policy?"}
    context = {"identity": {"nexus_id": "alice", "private": "NOT_SELECTED"}}
    body = request_body(action, arguments, config, context=context)
    assert body["query"] == "User alice: Policy?"
    assert body["audit_metadata"] == {
        "user": "alice",
        "question": "Policy?",
        "environment": "personal",
        "count": "3",
        "approved": "false",
        "optional": "null",
    }
    assert config == before and arguments == {"query": "Policy?"}
    assert "NOT_SELECTED" not in json.dumps(body)


def test_disabled_and_empty_metadata_send_nothing_and_replacement_is_not_recursive():
    config = template()
    config["audit_metadata"]["enabled"] = False
    assert "audit_metadata" not in request_body(
        "ask", {"query": "Policy?"}, config, context={"identity": {"nexus_id": "alice"}}
    )
    config = {"version": 1, "audit_metadata": {"enabled": True, "fields": {"blank": ""}}}
    assert "audit_metadata" not in request_body("ask", {"query": "Policy?"}, config)
    config["audit_metadata"]["fields"] = {"question": "{{query}}"}
    assert request_body("ask", {"query": "{{secret}}"}, config)["audit_metadata"] == {
        "question": "{{secret}}"
    }


@pytest.mark.parametrize("config", [None, {"version": 1}, template()])
def test_callers_cannot_supply_metadata_even_without_enabled_template(config):
    with pytest.raises(ValueError, match="override reviewed"):
        request_body("ask", {"query": "Policy?", "audit_metadata": {"user": "bob"}}, config)


@pytest.mark.parametrize(
    "audit",
    [
        None,
        {},
        {"fields": {}},
        {"enabled": True},
        {"enabled": 1, "fields": {}},
        {"enabled": True, "fields": [], "secret": "PRIVATE_VALUE"},
        {"enabled": True, "fields": {"field": {"PRIVATE_VALUE": 1}}},
        {"enabled": True, "fields": {"field": "{{unknown}}"}},
        {"enabled": True, "fields": {"field": "{{query.__class__}}"}},
        {"enabled": True, "fields": {"{{query}}": "dynamic key"}},
        {"enabled": True, "fields": {"": "PRIVATE_VALUE"}},
        {"enabled": True, "fields": {"line\nbreak": "PRIVATE_VALUE"}},
        {"enabled": True, "fields": {"x" * 129: "PRIVATE_VALUE"}},
        {"enabled": True, "fields": {str(i): "value" for i in range(31)}},
        {"enabled": False, "fields": {"field": "{{unknown}}"}},
    ],
)
def test_bad_audit_configuration_rejects_without_echoing_values(audit):
    with pytest.raises(ValueError) as error:
        validate_template({"version": 1, "audit_metadata": audit})
    assert "PRIVATE_VALUE" not in str(error.value)


def test_expanded_size_uses_provider_json_encoding_and_fails_before_partial_send():
    config = {
        "version": 1,
        "audit_metadata": {"enabled": True, "fields": {"first": "{{query}}", "last": "ok"}},
    }
    assert request_body("ask", {"query": "x" * 4000}, config)["audit_metadata"]["last"] == "ok"
    # Non-ASCII JSON escaping makes this larger than the 4,000-byte query limit.
    with pytest.raises(ValueError, match="10 KiB"):
        request_body("ask", {"query": "é" * 1900}, config)
    config["audit_metadata"]["fields"]["first"] = "x" * 10240
    with pytest.raises(ValueError, match="10 KiB"):
        validate_template(config)


def test_missing_or_spoofed_context_fails_without_dropping_audit_fields():
    for arguments, context in (
        ({"query": "Policy?"}, {}),
        ({"query": "Policy?", "parameters": {"user": "bob"}}, {"identity": {"nexus_id": "alice"}}),
        ({"query": "Policy?"}, {"identity": {"nexus_id": {"unexpected": True}}}),
    ):
        with pytest.raises(ValueError):
            request_body("ask", arguments, template(), context=context)


def test_shared_execution_selects_current_identity_and_hides_audit_values_from_tool_schema(team):
    from nexus.tools import tool_registry

    studio, _, manager, _, _ = team
    bodies = []
    studio.connections.enterprise.request_client = lambda method, url, headers, body: (
        bodies.append(body) or {"total": 0}
    )
    studio.catalog.save(
        "connectors",
        {
            "id": "audit-knowledge",
            "name": "Audited knowledge",
            "data": {
                "type": "nuclia",
                "url": "https://example.org",
                "knowledge_box": "selected",
                "token_env": "OPENAI_API_KEY",
                "actions": ["query"],
                "enabled": True,
                "template": template(),
            },
        },
    )
    tool = next(name for name in studio.operations.tools if name.endswith(":audit-knowledge"))
    assert "audit_metadata" not in tool_registry.get(tool).description
    assert "personal" not in tool_registry.get(tool).description
    assert "user" not in tool_registry.get(tool).description
    for identity in ("alice", "bob"):
        result = asyncio.run(
            studio.connections.execute(
                "audit-knowledge", {"action": "query", "arguments": {"query": "Policy?"}}, identity
            )
        )
        assert result == {"total": 0}
        assert bodies[-1]["audit_metadata"]["user"] == identity
    manager.revoked.add("alice")
    with pytest.raises(PermissionError):
        asyncio.run(
            studio.connections.execute(
                "audit-knowledge", {"action": "query", "arguments": {"query": "Policy?"}}, "alice"
            )
        )
    assert len(bodies) == 2


def test_descriptor_adapts_audit_and_preserves_disabled_empty_legacy_binding():
    source = descriptor()
    source["tool_config"]["audit_metadata"] = {"fields": {"region": "{region}", "query": "{query}"}}
    before = copy.deepcopy(source)
    converted = convert_tool_descriptor(source, policy=POLICY)
    assert converted["connector"]["template"]["audit_metadata"] == {
        "enabled": True,
        "fields": {"region": "{{region}}", "query": "{{query}}"},
    }
    assert any("Nuclia activity logs" in change for change in converted["adaptations"])
    assert source == before
    source["tool_config"]["audit_metadata"] = {"enabled": False}
    assert (
        "audit_metadata"
        not in convert_tool_descriptor(source, policy=POLICY)["connector"]["template"]
    )


@pytest.mark.parametrize("fields", [{"x": "{query!r}"}, {"x": "{query[0]}"}, {"x": "{query:1000}"}])
def test_reference_audit_rejects_executable_or_formatted_expressions(fields):
    with pytest.raises(ValueError, match="scalar placeholders"):
        convert_template_settings({"audit_metadata": {"fields": fields}}, policy=POLICY)


@pytest.mark.parametrize("sql", [False, True])
def test_reviewed_import_export_and_restart_keep_audit_fields(tmp_path, sql):
    options = (
        {"state_options": {"url": f"sqlite:///{tmp_path / 'state.sqlite'}", "namespace": "audit"}}
        if sql
        else {}
    )
    studio = StudioService(tmp_path / "workspace", **options)
    source = descriptor()
    source["tool_config"]["audit_metadata"] = {
        "enabled": False,
        "fields": {"region": "{region}", "query": "{query}"},
    }
    try:
        converted = convert_tool_descriptor(source, policy=POLICY)
        studio.catalog.save(
            "connectors", {"id": "knowledge", "data": converted["connector"], "name": "Knowledge"}
        )
        report = studio.configuration.convert_interop(import_request(source), None)
        assert report["valid"], report
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        export = studio.configuration.export_interop({"document": report["document"]}, None)
        assert export["valid"]
        roundtrip = studio.configuration.convert_interop({"documents": [export["bundle"]]}, None)
        assert roundtrip["valid"] and roundtrip["document"] == report["document"]
    finally:
        studio.close()
    studio = StudioService(tmp_path / "workspace", **options)
    try:
        native = studio.catalog.get("connectors", "knowledge")["data"]
        assert native["template"]["audit_metadata"]["fields"]["region"] == "{{region}}"
        assert native["template"]["audit_metadata"]["enabled"] is False
        assert native["enabled"] is False
    finally:
        studio.close()
