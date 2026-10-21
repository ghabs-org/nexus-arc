"""Typed Nuclia templates fail before network access and round-trip as configuration."""

from copy import deepcopy

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_enterprise import validate_enterprise
from nexus.core.studio_nuclia import request_body
from nexus.core.studio_nuclia_templates import prepare_arguments, validate_template


def template():
    return {
        "version": 1,
        "parameters": [
            {"name": "region", "type": "string", "required": True},
            {"name": "document", "type": "string", "default": "resource"},
            {"name": "count", "type": "integer", "default": 3},
            {"name": "enabled", "type": "boolean", "default": False},
            {"name": "optional", "type": "string", "nullable": True},
        ],
        "query_prompt": "Region: {{region}}; Question: {{query}}; count={{count}}; enabled={{enabled}}; optional={{optional}}",
        "filter_expression": {"field": {"prop": "resource", "id": "{{document}}"}},
    }


def test_runtime_defaults_types_and_json_values_stay_data():
    config = template()
    original = deepcopy(config)
    result = request_body("ask", {"query": "Policy?", "parameters": {"region": "emea"}}, config)
    assert (
        result["query"] == "Region: emea; Question: Policy?; count=3; enabled=false; optional=null"
    )
    assert result["filter_expression"]["field"]["id"] == "resource"
    assert result["citations"] == "default" and config == original
    config["filter_expression"]["field"]["typed_values"] = [
        "{{count}}",
        "{{enabled}}",
        "{{optional}}",
    ]
    result = prepare_arguments(config, {"query": "Policy?", "parameters": {"region": "emea"}})
    assert result["filter_expression"]["field"]["typed_values"] == [3, False, None]
    payload = '"},"operator":"or","field":{}'
    result = prepare_arguments(
        config, {"query": "Policy?", "parameters": {"region": "{{query}}", "document": payload}}
    )
    assert result["filter_expression"]["field"]["id"] == payload
    assert "Region: {{query}}" in result["query"]  # Substitution is deliberately non-recursive.


@pytest.mark.parametrize(
    "changes",
    [
        {"region": 1},
        {"region": None},
        {"region": "x" * 4001},
        {"count": True},
        {"count": 1.2},
        {"enabled": 1},
        {"optional": 1},
        {"unknown": "emea"},
    ],
)
def test_invalid_runtime_values_rejected(changes):
    with pytest.raises(ValueError):
        request_body(
            "ask", {"query": "Policy?", "parameters": {"region": "emea", **changes}}, template()
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "Policy?"},
        {"parameters": {"region": "emea"}},
        {"query": None, "parameters": {"region": "emea"}},
        {"query": "", "parameters": {"region": "emea"}},
        {"query": "Policy?", "parameters": []},
        {"query": "Policy?", "parameters": {"region": "emea"}, "filter_expression": {"field": {}}},
    ],
)
def test_missing_query_required_values_or_filter_override_rejected(arguments):
    with pytest.raises(ValueError):
        request_body("ask", arguments, template())


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"version": True},
        {"exec": "never"},
        {"parameters": [{"name": "query"}]},
        {"parameters": [{"name": "region", "type": "object"}]},
        {"parameters": [{"name": "region", "type": []}]},
        {"parameters": [{"name": "region", "nullable": 1}]},
        {"parameters": [{"name": "region", "type": "integer", "default": True}]},
        {"parameters": [{"name": "region", "default": None}]},
        {"query_prompt": "Missing required query token"},
        {"query_prompt": None},
        {"query_prompt": "{{query}} {{undeclared}}"},
        {"filter_expression": None},
        {"filter_expression": {"field": "{{document}}"}},
        {"filter_expression": {"field": {"{{document}}": "dynamic key"}}},
    ],
)
def test_invalid_configuration_rejected_before_saving(changes):
    with pytest.raises(ValueError):
        validate_template({**template(), **changes})


def test_empty_optional_and_nullable_defaults_are_explicit():
    config = {
        "version": 1,
        "parameters": [{"name": "optional", "default": ""}],
        "query_prompt": "{{query}} {{optional}}",
    }
    assert request_body("query", {"query": "Policy?"}, config)["query"] == "Policy? "
    config["parameters"] = [{"name": "optional"}]
    with pytest.raises(ValueError, match="referenced"):
        request_body("query", {"query": "Policy?"}, config)


def test_connection_validation_round_trip_and_failed_preflight_leave_no_records(tmp_path):
    studio = StudioService(tmp_path)
    config = {
        "type": "nuclia",
        "url": "https://example.org",
        "knowledge_box": "selected",
        "token_env": "NUCLIA_TOKEN",
        "template": template(),
        "actions": ["query", "ask"],
        "enabled": False,
    }
    try:
        normalized = validate_enterprise(config)
        assert validate_enterprise(normalized) == normalized
        saved = studio.catalog.save(
            "connectors",
            {"id": "templated-knowledge", "name": "Regional knowledge", "data": config},
        )
        assert saved["data"]["template"] == template()
        invalid = {**config, "template": {**template(), "query_prompt": "{{undefined}}"}}
        with pytest.raises(ValueError):
            studio.catalog.save(
                "connectors", {"id": "invalid-template", "name": "Invalid", "data": invalid}
            )
        assert [row["id"] for row in studio.catalog.list("connectors")] == [saved["id"]]
    finally:
        studio.close()


def test_configuration_plan_apply_export_preserves_templates_and_rejects_whole_invalid_import(
    tmp_path,
):
    source = StudioService(tmp_path / "source")
    target = StudioService(tmp_path / "target")
    rejected = StudioService(tmp_path / "rejected")
    try:
        source.catalog.save(
            "connectors",
            {
                "id": "regional",
                "name": "Regional knowledge",
                "data": {
                    "type": "nuclia",
                    "url": "https://example.org",
                    "knowledge_box": "selected",
                    "token_env": "NUCLIA_TOKEN",
                    "template": template(),
                    "actions": ["query", "ask"],
                    "enabled": False,
                },
            },
        )
        exported = source.configuration.export(None)
        assert target.configuration.validate(exported, None)["valid"]
        assert not target.catalog.list("connectors")
        plan = target.configuration.plan(exported, None)
        assert not target.catalog.list("connectors")
        target.configuration.apply(plan["id"], plan["digest"], None)
        assert target.configuration.export(None) == exported
        invalid = deepcopy(exported)
        invalid["resources"].append({**deepcopy(invalid["resources"][0]), "id": "invalid"})
        invalid["resources"][-1]["data"]["template"]["query_prompt"] = "{{undeclared}}"
        with pytest.raises(ValueError):
            rejected.configuration.plan(invalid, None)
        assert not rejected.catalog.list("connectors") and not rejected.records(
            "configuration-plans"
        )
    finally:
        source.close()
        target.close()
        rejected.close()


def test_agent_tool_description_exposes_parameter_contract_without_default_values(tmp_path):
    from nexus.tools import tool_registry

    studio = StudioService(tmp_path)
    config = template()
    config["parameters"][1]["default"] = "PRIVATE_SELECTED_RESOURCE"
    try:
        record = studio.catalog.save(
            "connectors",
            {
                "id": "regional",
                "name": "Regional knowledge",
                "data": {
                    "type": "nuclia",
                    "url": "https://example.org",
                    "knowledge_box": "selected",
                    "token_env": "NUCLIA_TOKEN",
                    "template": config,
                    "actions": ["query", "ask"],
                    "enabled": True,
                },
            },
        )
        name = next(name for name in studio.operations.tools if name.endswith(":" + record["id"]))
        description = tool_registry.get(name).description
        assert "arguments.parameters" in description and '"region"' in description
        assert '"required": true' in description
        assert "PRIVATE_SELECTED_RESOURCE" not in description and "NUCLIA_TOKEN" not in description
    finally:
        studio.close()
