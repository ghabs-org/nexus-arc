"""Frozen reference Nuclia templates adapt explicitly before native preflight."""

from copy import deepcopy

import pytest

from nexus.core.studio_nuclia import request_body
from nexus.core.studio_nuclia_interop import POLICY, convert_template_settings


def source():
    return {
        "template_parameters": [
            {"name": "region", "type": "string", "required": True},
            {"name": "count", "type": "integer", "default": 3},
        ],
        "prompt_rephrasing": {"template": "Region: {region}; Question: {query}"},
        "filter_expression_template": {
            "field": {
                "and": [
                    {"prop": "label", "labelset": "Region", "label": "{region}"},
                    {"not": {"prop": "resource", "id": "literal"}},
                ]
            }
        },
    }


def test_explicit_adaptation_preserves_data_without_executing_source():
    config = source()
    before = deepcopy(config)
    report = convert_template_settings(config, policy=POLICY)
    assert report["policy"] == POLICY and len(report["adaptations"]) == 5
    assert config == before
    assert report["template"]["parameters"][0]["nullable"] is True  # Frozen reference default.
    result = request_body(
        "ask", {"query": "Policy?", "parameters": {"region": "emea"}}, report["template"]
    )
    assert result["query"] == "Region: emea; Question: Policy?"
    assert result["filter_expression"]["field"]["and"][0]["label"] == "emea"
    assert result["filter_expression"]["field"]["and"][1] == {
        "not": {"prop": "resource", "id": "literal"}
    }
    report["template"]["parameters"][1]["default"] = 9
    assert config == before


@pytest.mark.parametrize("policy", [None, "reference", "", True])
def test_implicit_or_unknown_policy_rejected(policy):
    with pytest.raises(ValueError, match="explicit"):
        convert_template_settings(source(), policy=policy)


@pytest.mark.parametrize(
    "template",
    [
        "{query} {region.__class__}",
        "{query} {region[0]}",
        "{query} {region!r}",
        "{query} {count:04d}",
        "{query} {}",
        "{query} {",
        "{query} {undefined}",
        "{query} {{{{region}}}}",
    ],
)
def test_unsupported_formatting_and_undeclared_names_fail(template):
    config = source()
    config["prompt_rephrasing"]["template"] = template
    with pytest.raises(ValueError):
        convert_template_settings(config, policy=POLICY)


def test_escaped_braces_and_runtime_values_are_not_reinterpreted():
    config = source()
    config["prompt_rephrasing"]["template"] = '{{"question": "{query}"}}; {region}'
    native = convert_template_settings(config, policy=POLICY)["template"]
    result = request_body(
        "ask", {"query": "Policy?", "parameters": {"region": "{query} {{query}}"}}, native
    )
    assert result["query"] == '{"question": "Policy?"}; {query} {{query}}'


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(token="PRIVATE_SECRET"),
        lambda c: c.update(audit_metadata={"enabled": False}),
        lambda c: c["template_parameters"][0].update(context_expression="private.user()"),
        lambda c: c["template_parameters"][0].update(nullable=1),
        lambda c: c["prompt_rephrasing"].update(unknown="PRIVATE_SECRET"),
        lambda c: c.update(prompt_rephrasing={"template": None}),
        lambda c: c.update(template_parameters={}),
        lambda c: c.update(filter_expression_template={"field": "{region}"}),
        lambda c: c.update(
            filter_expression_template={"field": {"prop": "x", "value": float("nan")}}
        ),
    ],
)
def test_unknown_or_invalid_source_settings_fail_without_echoing_values(mutation):
    config = source()
    mutation(config)
    with pytest.raises(ValueError) as error:
        convert_template_settings(config, policy=POLICY)
    assert "PRIVATE_SECRET" not in str(error.value) and "private.user" not in str(error.value)


def test_filters_are_never_silently_removed_and_scalars_follow_declared_policy():
    native = convert_template_settings(source(), policy=POLICY)["template"]
    with pytest.raises(ValueError, match="required"):
        request_body("ask", {"query": "Policy?"}, native)
    for value in (None, ""):
        result = request_body("ask", {"query": "Policy?", "parameters": {"region": value}}, native)
        assert result["filter_expression"]["field"]["and"][0]["label"] == value
    with pytest.raises(ValueError, match="override"):
        request_body(
            "ask",
            {
                "query": "Policy?",
                "parameters": {"region": "emea"},
                "filter_expression": {"field": {}},
            },
            native,
        )


def test_optional_defaults_and_disabled_templates_follow_native_rules():
    native = convert_template_settings(
        {
            "template_parameters": [{"name": "optional"}],
            "prompt_rephrasing": None,
            "filter_expression_template": None,
        },
        policy=POLICY,
    )["template"]
    assert native == {"version": 1, "parameters": [{"name": "optional", "nullable": True}]}
    assert request_body("ask", {"query": "Policy?"}, native)["query"] == "Policy?"
