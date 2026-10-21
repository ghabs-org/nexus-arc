"""Dedicated real HTTP acceptance against the local Postman geography simulation.

Run a simulation at NEXUS_GEOGRAPHY_SIM_URL first; no live provider is called.
"""

import asyncio
import os
import socket
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_research import StudioResearch


@pytest.fixture
def service(tmp_path, monkeypatch):
    base = os.environ["NEXUS_GEOGRAPHY_SIM_URL"].rstrip("/")
    assert base.startswith("http://127.0.0.1:")
    original = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            if host in {"geocoding-api.open-meteo.com", "api.open-meteo.com"}
            else original(host, *args, **kwargs)
        ),
    )

    def request(method, url, headers, body, text):
        parsed = urlparse(url)
        assert parsed.hostname in {"geocoding-api.open-meteo.com", "api.open-meteo.com"}
        assert not headers
        # Use the production bounded/no-redirect HTTP implementation on loopback.
        return StudioResearch.request(
            SimpleNamespace(request_client=None),
            method,
            base + parsed.path + "?" + parsed.query,
            headers,
            body,
            private=True,
            text=text,
        )

    app = StudioService(
        tmp_path, research_options={"config": {"geographic": True}, "request_client": request}
    )
    try:
        yield app
    finally:
        app.close()


@pytest.mark.parametrize("action", ["city_to_coordinates", "city_to_timezone", "get_weather"])
def test_geographic_wire_contract(service, action):
    args = {"city": "Rome", "country_code": "IT"}
    if action == "get_weather":
        args["forecast_days"] = 3
    result = asyncio.run(service.research.execute(action, args))
    assert result["locations"][0]["latitude"] == 41.9
    assert result["sources"][0]["url"].startswith("https://geocoding-api.open-meteo.com/")
    if action == "get_weather":
        assert len(result["forecast"]) == 3
        assert result["daily_units"]["precipitation_sum"] == "mm"


@pytest.mark.parametrize("city", ["Failure", "Redirect", "BadSchema", "Unknown", "WeatherFailure"])
def test_service_failures_do_not_fall_back_or_poison_recovery(service, city):
    with pytest.raises(ValueError):
        asyncio.run(service.research.execute("get_weather", {"city": city}))
    recovered = asyncio.run(service.research.execute("city_to_coordinates", {"city": "Rome"}))
    assert recovered["locations"][0]["name"] == "Rome"


def test_example_workflow_uses_http_source_and_pinned_ruleset(service):
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_geography_rulesets.py"
    )
    spec = importlib.util.spec_from_file_location("geography_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.exercise(service)
    assert result["policy"]["source"]["artifact_revision"] == 1
    assert result["city"]["sources"][0]["url"].startswith("https://geocoding-api.open-meteo.com/")
