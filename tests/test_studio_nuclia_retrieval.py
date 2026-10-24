"""Explicit bounded context strategies preserve old imports and reviewed fixed settings."""

import copy
import json

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_enterprise import validate_enterprise
from nexus.core.studio_nuclia import request_body
from nexus.core.studio_nuclia_descriptors import convert_tool_descriptor, validate_ask_settings
from nexus.core.studio_nuclia_interop import POLICY
from nexus.core.studio_nuclia_retrieval import EXPANDED_CONTEXT, validate_rag_strategies
from tests.test_studio_nuclia_descriptors import descriptor, import_request


def test_expanded_import_is_explicit_and_preserves_source_and_legacy_shapes():
    source = descriptor()
    before = copy.deepcopy(source)
    legacy = convert_tool_descriptor(source, policy=POLICY)
    expanded = convert_tool_descriptor(source, policy=POLICY, preserve_retrieval=True)
    assert "rag_strategies" not in legacy["connector"]["ask_settings"]
    assert "native" in legacy["adaptations"][-1].lower()
    assert expanded["connector"]["ask_settings"]["rag_strategies"] == list(EXPANDED_CONTEXT)
    assert "may increase usage" in expanded["adaptations"][-1]
    assert expanded["connector"]["enabled"] is False
    assert expanded["connector"]["actions"] == ["ask"]
    assert source == before
    expanded["connector"]["ask_settings"]["rag_strategies"][0]["before"] = 0
    assert EXPANDED_CONTEXT[0]["before"] == 2
    assert "rag_strategies" not in request_body("ask", {"query": "Policy?"})


def test_strategy_defaults_normalize_before_fixed_comparison_without_mutation():
    minimal = [{"name": "neighbouring_paragraphs"}, {"name": "hierarchy"}]
    before = copy.deepcopy(minimal)
    fixed = validate_ask_settings({"rag_strategies": minimal})
    assert fixed == {"rag_strategies": list(EXPANDED_CONTEXT)}
    for configured in (fixed, {"rag_strategies": minimal}):
        for supplied in ({}, {"rag_strategies": minimal}, fixed):
            result = request_body("ask", {"query": "Policy?", **supplied}, ask_settings=configured)
            assert result["rag_strategies"] == list(EXPANDED_CONTEXT)
    assert minimal == before
    assert validate_rag_strategies([]) == []
    assert request_body("ask", {"query": "Policy?", "rag_strategies": []})["rag_strategies"] == []
    assert (
        validate_rag_strategies(
            [
                {"name": "neighbouring_paragraphs", "before": 0, "after": 10},
                {"name": "hierarchy", "count": 1024},
            ]
        )[1]["count"]
        == 1024
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        "PRIVATE_VALUE",
        [None],
        [{}],
        [{"name": []}],
        [{"name": "full_resource"}],
        [{"name": "PRIVATE_VALUE"}],
        [{"name": "hierarchy"}, {"name": "hierarchy"}],
        [{"name": "hierarchy"}] * 3,
        [{"name": "hierarchy", "count": True}],
        [{"name": "hierarchy", "count": -1}],
        [{"name": "hierarchy", "count": 1025}],
        [{"name": "hierarchy", "count": 0.5}],
        [{"name": "hierarchy", "before": 1}],
        [{"name": "neighbouring_paragraphs", "before": 11}],
        [{"name": "neighbouring_paragraphs", "after": -1}],
        [{"name": "neighbouring_paragraphs", "after": False}],
        [{"name": "neighbouring_paragraphs", "after": float("inf")}],
        [{"name": "neighbouring_paragraphs", "url": "PRIVATE_VALUE"}],
    ],
)
def test_invalid_strategies_reject_without_echoing_values(value):
    for call in (
        lambda: validate_rag_strategies(value),
        lambda: validate_ask_settings({"rag_strategies": value}),
        lambda: request_body("ask", {"query": "Policy?", "rag_strategies": value}),
    ):
        with pytest.raises(ValueError) as error:
            call()
        assert "PRIVATE_VALUE" not in str(error.value)


def test_search_and_fixed_strategy_replacement_fail():
    with pytest.raises(ValueError, match="Unsupported Nuclia"):
        request_body("query", {"query": "Policy?", "rag_strategies": []})
    for replacement in (
        [],
        [{"name": "hierarchy"}],
        [{"name": "neighbouring_paragraphs", "before": 1}],
    ):
        with pytest.raises(ValueError, match="cannot be overridden"):
            request_body(
                "ask",
                {"query": "Policy?", "rag_strategies": replacement},
                ask_settings={"rag_strategies": list(EXPANDED_CONTEXT)},
            )


@pytest.mark.parametrize("value", [None, 1, "true", {}, []])
def test_invalid_import_flag_fails_without_creating_definitions(tmp_path, value):
    with pytest.raises(ValueError, match="boolean"):
        convert_tool_descriptor(descriptor(), policy=POLICY, preserve_retrieval=value)
    studio = StudioService(tmp_path)
    try:
        request = import_request()
        request["bindings"]["nuclia"]["CompanyKnowledge"]["preserve_retrieval"] = value
        result = studio.configuration.convert_interop(request, None)
        assert not result["valid"] and result["document"] is None
        assert not studio.definitions("agents")
        assert not studio.records("configuration-plans")
    finally:
        studio.close()


@pytest.mark.parametrize("sql", [False, True])
def test_expanded_binding_roundtrip_and_restart_require_matching_settings(tmp_path, sql):
    options = (
        {"url": f"sqlite:///{tmp_path / 'state.sqlite'}", "namespace": "retrieval"} if sql else None
    )
    studio = StudioService(tmp_path / "workspace", state_options=options)
    request = import_request()
    request["bindings"]["nuclia"]["CompanyKnowledge"]["preserve_retrieval"] = True
    source = descriptor()
    legacy = convert_tool_descriptor(source, policy=POLICY)["connector"]
    expanded = convert_tool_descriptor(source, policy=POLICY, preserve_retrieval=True)["connector"]
    try:
        studio.catalog.save("connectors", {"id": "knowledge", "name": "Knowledge", "data": legacy})
        mismatch = studio.configuration.convert_interop(request, None)
        assert not mismatch["valid"] and mismatch["document"] is None
        assert not studio.definitions("agents")
        studio.catalog.save(
            "connectors", {**studio.catalog.get("connectors", "knowledge"), "data": expanded}
        )
        report = studio.configuration.convert_interop(request, None)
        assert report["valid"], report
        assert "may increase usage" in json.dumps(report["adaptations"])
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        exported = studio.configuration.export_interop({"document": report["document"]}, None)
        assert exported["valid"]
        roundtrip = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
        assert roundtrip["valid"] and roundtrip["document"] == report["document"]
        native = validate_enterprise(studio.catalog.get("connectors", "knowledge")["data"])
        assert native["enabled"] is False
        assert native["ask_settings"]["rag_strategies"] == list(EXPANDED_CONTEXT)
    finally:
        studio.close()
    studio = StudioService(tmp_path / "workspace", state_options=options)
    try:
        saved = studio.catalog.get("connectors", "knowledge")
        assert saved["data"]["ask_settings"]["rag_strategies"] == list(EXPANDED_CONTEXT)
        assert studio.configuration.convert_interop(request, None)["valid"]
    finally:
        studio.close()
