"""Native geographic/ruleset equivalents: semantics, evidence, limits and ownership."""

import asyncio
import copy
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_connections import execution_identity
from nexus.core.studio_geography import zone_details
from tests.test_studio_dynamic_embeds import settled, upload
from tests.test_studio_research import public_dns as public_dns
from tests.test_studio_team import team as team

RULES = {
    "name": "hr_policies",
    "description": "Synthetic policy rules",
    "grouping_parameter": "country",
    "input_parameters": [
        {"name": "country", "type": "string", "required": True},
        {"name": "years", "type": "integer"},
    ],
    "decision_tree_groups": {
        "Netherlands": {
            "decision_trees": [
                {
                    "name": "Leave Policy",
                    "description": "Annual leave",
                    "decision_logic": "IF years >= 5: RETURN 25 ELSE RETURN 20",
                }
            ]
        },
        "_default": {
            "decision_trees": [
                {
                    "name": "General Policy",
                    "description": "Default leave",
                    "decision_logic": "RETURN 15",
                }
            ]
        },
    },
}


def location():
    return {
        "name": "Rome",
        "latitude": 41.9,
        "longitude": 12.5,
        "country_code": "IT",
        "country": "Italy",
        "timezone": "Europe/Rome",
    }


def weather(days=2):
    return {
        "current": {
            "time": "2026-10-21T12:00",
            "temperature_2m": 22,
            "relative_humidity_2m": 60,
            "weather_code": 1,
            "wind_speed_10m": 5,
        },
        "daily": {
            "time": ["2026-10-" + str(21 + i) for i in range(days)],
            "weather_code": [1] * days,
            "temperature_2m_max": [24] * days,
            "temperature_2m_min": [12] * days,
            "precipitation_sum": [0] * days,
        },
        "current_units": {
            "time": "iso8601",
            "temperature_2m": "°C",
            "relative_humidity_2m": "%",
            "weather_code": "wmo code",
            "wind_speed_10m": "km/h",
        },
        "daily_units": {
            "time": "iso8601",
            "weather_code": "wmo code",
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_sum": "mm",
        },
    }


def client(calls):
    def request(method, url, headers, body, text):
        calls.append((method, url, headers, body))
        assert method == "GET" and headers == {} and body is None
        parsed = urlparse(url)
        if parsed.hostname == "geocoding-api.open-meteo.com":
            return {"results": [location()]}
        assert parsed.hostname == "api.open-meteo.com"
        return weather(int(parse_qs(parsed.query)["forecast_days"][0]))

    return request


def test_geography_is_opt_in_and_catalog_browsing_has_no_requests(tmp_path):
    calls = []
    service = StudioService(tmp_path, research_options={"request_client": client(calls)})
    try:
        catalog = service.recipes.list()
        geo = next(r for r in catalog["official_items"] if r["id"] == "community-geo-information")
        rules = next(
            r for r in catalog["official_items"] if r["id"] == "community-ruleset-lookup-tool"
        )
        assert (
            geo["mode"] == "tool"
            and not geo["configured"]
            and geo["tool_id"].endswith(":city_to_coordinates")
        )
        assert rules["configured"] and rules["tool_id"].endswith(":ruleset_lookup")
        with pytest.raises(ValueError, match="enable"):
            asyncio.run(service.research.execute("get_weather", {"city": "Rome"}))
        assert not calls
    finally:
        service.close()


def test_geographic_current_forecast_sources_and_dst(tmp_path, public_dns):
    calls = []
    service = StudioService(
        tmp_path, research_options={"config": {"geographic": True}, "request_client": client(calls)}
    )
    try:
        for action in ("city_to_coordinates", "city_to_timezone", "get_weather"):
            arguments = {"city": "Rome", "country_code": "it"}
            if action == "get_weather":
                arguments["forecast_days"] = 2
            name = next(n for n in service.research.names if n.endswith(":" + action))
            run = settled(
                service,
                service.start_run(
                    {
                        "kind": "tool",
                        "task": "Geographic fixture",
                        "tool": name,
                        "kwargs": arguments,
                    }
                ),
            )
            assert run["status"] == "completed", run
            result = json.loads(run["output"])
            assert result["locations"][0]["timezone"] == "Europe/Rome"
            assert all(
                s["sha256"] and s["retrieved_at"] and s["citation_ref"] for s in result["sources"]
            )
            assert len(run["citations"]) == (2 if action == "get_weather" else 1)
            if action == "get_weather":
                assert (
                    len(result["forecast"]) == 2
                    and result["current_units"]["temperature_2m"] == "°C"
                )
        assert len(calls) == 4
        assert "countryCode=IT" in calls[0][1]
        assert (
            zone_details("Europe/Rome", datetime(2026, 1, 15, tzinfo=UTC))["utc_offset_seconds"]
            == 3600
        )
        assert zone_details("Europe/Rome", datetime(2026, 7, 15, tzinfo=UTC))["is_dst"]
    finally:
        service.close()


@pytest.mark.parametrize(
    "arguments",
    [
        {"city": "x"},
        {"city": "Rome", "limit": True},
        {"city": "Rome", "limit": 6},
        {"city": "Rome", "country_code": "Italy"},
        {"city": "Rome", "forecast_days": 8},
        {"city": "Rome", "url": "http://localhost"},
    ],
)
def test_invalid_geographic_inputs_fail_before_service_request(tmp_path, arguments):
    calls = []
    service = StudioService(
        tmp_path, research_options={"config": {"geographic": True}, "request_client": client(calls)}
    )
    try:
        with pytest.raises(ValueError):
            asyncio.run(service.research.execute("get_weather", arguments))
        assert not calls
    finally:
        service.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"latitude": 99},
        {"longitude": float("nan")},
        {"timezone": "../../etc/passwd"},
        {"timezone": "No/SuchZone"},
        {"name": "<invalid>\ntext"},
    ],
)
def test_invalid_geographic_provider_values_fail_without_fallback(tmp_path, public_dns, changes):
    calls = []

    def request(*args):
        calls.append(args)
        return {"results": [{**location(), **changes}]}

    service = StudioService(
        tmp_path, research_options={"config": {"geographic": True}, "request_client": request}
    )
    try:
        with pytest.raises(ValueError):
            asyncio.run(service.research.execute("city_to_coordinates", {"city": "Rome"}))
        assert len(calls) == 1
    finally:
        service.close()


def test_grouped_rules_preserve_text_types_default_and_old_revision(tmp_path):
    service = StudioService(tmp_path)
    try:
        record = upload(service, "policies.json", json.dumps({"decision_data": RULES}).encode())
        name = next(iter(service.rulesets.names))
        run = settled(
            service,
            service.start_run(
                {
                    "kind": "tool",
                    "task": "Lookup rules",
                    "tool": name,
                    "kwargs": {
                        "artifact_id": record["id"],
                        "revision": 1,
                        "parameters": {"country": "nEtHeRlAnDs", "years": 5},
                    },
                }
            ),
        )
        assert run["status"] == "completed", run
        result = json.loads(run["output"])
        assert result["group"] == "Netherlands" and not result["used_default"]
        assert "IF years >= 5" in result["decision_logic"]
        assert result["source"]["artifact_revision"] == 1 and result["citation_ref"] == "[source:1]"
        assert run["citations"][0]["sha256"] == record["data"]["sha256"]
        changed = copy.deepcopy(RULES)
        changed["decision_tree_groups"]["Netherlands"]["decision_trees"][0]["decision_logic"] = (
            "RETURN 99"
        )
        upload(service, "policies.json", json.dumps(changed).encode(), identity=record["id"])
        assert (
            "RETURN 25"
            in service.rulesets.lookup(record["id"], {"country": "Netherlands"}, 1)[
                "decision_logic"
            ]
        )
        assert (
            "RETURN 99"
            in service.rulesets.lookup(record["id"], {"country": "Netherlands"})["decision_logic"]
        )
        default = service.rulesets.lookup(record["id"], {"country": "Other"})
        assert default["used_default"] and default["group"] == "_default"
        for values in (
            {},
            {"country": 2},
            {"country": "NL", "years": True},
            {"country": "NL", "unknown": "x"},
        ):
            with pytest.raises(ValueError):
                service.rulesets.lookup(record["id"], values)
    finally:
        service.close()


@pytest.mark.parametrize("variant", ["duplicate", "no-default", "oversized", "executable-object"])
def test_ruleset_schema_limits_and_unmatched_group_errors(tmp_path, variant):
    service = StudioService(tmp_path)
    try:
        data = copy.deepcopy(RULES)
        if variant == "duplicate":
            data["decision_tree_groups"]["netherlands"] = data["decision_tree_groups"][
                "Netherlands"
            ]
        elif variant == "no-default":
            del data["decision_tree_groups"]["_default"]
        elif variant == "oversized":
            data["decision_tree_groups"]["Netherlands"]["decision_trees"][0]["decision_logic"] = (
                "x" * 16385
            )
        else:
            data["decision_tree_groups"]["Netherlands"]["decision_trees"][0]["decision_logic"] = {
                "code": "execute()"
            }
        artifact = upload(service, "bad.json", json.dumps(data).encode())
        with pytest.raises(ValueError):
            service.rulesets.lookup(
                artifact["id"],
                {"country": "Unmatched" if variant == "no-default" else "Netherlands"},
            )
    finally:
        service.close()


def test_ruleset_tool_rechecks_identity_and_private_sources(team):
    service, policy, _, request, _ = team
    artifact = upload(service, "private-rules.json", json.dumps(RULES).encode(), actor="alice")
    token = execution_identity.set("bob")
    try:
        from nexus.tools import tool_registry

        name = next(iter(service.rulesets.names))
        assert policy.tool_allowed(name, service)
        result = asyncio.run(
            tool_registry.call(
                name, artifact_id=artifact["id"], parameters={"country": "Netherlands"}
            )
        )
        assert not result.ok and "unavailable" in result.error
        policy.bindings["bob"] = "viewer"
        result = asyncio.run(
            tool_registry.call(
                name, artifact_id=artifact["id"], parameters={"country": "Netherlands"}
            )
        )
        assert not result.ok
    finally:
        execution_identity.reset(token)
    status, inventory = request("/api/studio/tools", user="alice")
    assert status == 200 and any(r["id"].endswith(":ruleset_lookup") for r in inventory["items"])


@pytest.mark.parametrize("variant", ["missing_units", "short_forecast", "nonfinite"])
def test_weather_response_validation(tmp_path, public_dns, variant):
    def request(method, url, *args):
        if "geocoding" in url:
            return {"results": [location()]}
        result = weather(1)
        if variant == "missing_units":
            del result["current_units"]
        elif variant == "short_forecast":
            result["daily"]["time"] = []
        else:
            result["current"]["temperature_2m"] = float("nan")
        return result

    service = StudioService(
        tmp_path, research_options={"config": {"geographic": True}, "request_client": request}
    )
    try:
        with pytest.raises(ValueError):
            asyncio.run(service.research.execute("get_weather", {"city": "Rome"}))
    finally:
        service.close()


def test_ruleset_explicit_default_and_catalog_configuration(tmp_path):
    service = StudioService(tmp_path)
    try:
        data = copy.deepcopy(RULES)
        data["decision_tree_groups"]["General"] = data["decision_tree_groups"].pop("_default")
        wrapped = {
            "decision_data": data,
            "default_group_key": "General",
            "include_topics_in_description": True,
            "include_groups_in_description": True,
        }
        artifact = upload(service, "configured.json", json.dumps(wrapped).encode())
        result = service.rulesets.lookup(artifact["id"], {"country": "Other"})
        assert result["used_default"] and result["group"] == "General"
        assert result["available_groups"] == ["Netherlands"]
        assert result["available_topics"] == ["General Policy", "Leave Policy"]
        setup = service.recipes.configure("community-ruleset-lookup-tool", {}, None)
        assert setup["tool_id"] in service.rulesets.names and setup["configured"]
        assert not service.recipes.configure("community-geo-information", {}, None)["configured"]
    finally:
        service.close()
