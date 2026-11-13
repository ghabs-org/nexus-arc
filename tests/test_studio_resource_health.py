"""Real local probes, immutable revision attribution and passive inspection."""

import json
import sqlite3
import sys

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_features import StudioFeatures
from nexus.core.studio_resource_health import StudioResourceHealth
from tests.test_studio_build_lifecycle import apply, plan, proposal, seed


@pytest.fixture
def fixture(tmp_path):
    with sqlite3.connect(tmp_path / "health.db") as connection:
        connection.execute("CREATE TABLE private_data (secret TEXT)")
        connection.execute("INSERT INTO private_data VALUES ('must-never-leak')")
    studio = StudioService(tmp_path)
    build = seed(
        studio,
        proposal(
            resources=[
                {
                    "kind": "connectors",
                    "id": "database",
                    "name": "Local database",
                    "data": {"type": "sqlite", "path": "health.db"},
                }
            ]
        ),
    )
    key = next(
        c["key"]
        for c in studio.resource_lifecycle.inspect_build(build["id"])["components"]
        if c["key"].startswith("connectors:")
    )
    yield studio, build, key, StudioResourceHealth(studio.resource_lifecycle)
    studio.close()


def request(build, key):
    return {"revision": build["revision"], "release_id": build["release_id"], "component": key}


def test_real_sqlite_probe_persists_without_reading_data_or_activating(fixture):
    studio, build, key, health = fixture
    before = studio.resource_lifecycle.current(key)
    for _ in range(2):
        view = studio.resource_lifecycle.inspect_build(build["id"])
        assert view["live_health"] == "not_checked"
        assert studio.records("resource-health") == []
    code, result = StudioFeatures(studio).post(
        ["builds", build["id"], "health"], request(build, key), None
    )
    assert code == 200 and result["status"] == "passed"
    assert result["release_id"] == build["release_id"]
    assert result["action"] == "select_one"
    assert "must-never-leak" not in json.dumps(result)
    assert studio.resource_lifecycle.current(key) == before
    assert studio.resource_lifecycle.marker(key)["active"] is False
    studio.close()
    reopened = StudioService(studio.root)
    try:
        view = reopened.resource_lifecycle.inspect_build(build["id"])
        assert view["live_health"] == "partial"
        assert (
            next(c for c in view["components"] if c["key"] == key)["health"]["status"] == "passed"
        )
    finally:
        reopened.close()


def test_stale_release_and_unmanaged_components_rejected_before_probe(fixture):
    studio, build, key, health = fixture
    for change in ({"release_id": "old"}, {"revision": 0}, {"component": "connectors:other"}):
        with pytest.raises(ValueError):
            health.check(build["id"], request(build, key) | change)
    prompt = next(
        c["key"]
        for c in studio.resource_lifecycle.inspect_build(build["id"])["components"]
        if c["key"].startswith("prompts:")
    )
    with pytest.raises(ValueError, match="no supported"):
        health.check(build["id"], request(build, prompt))
    assert studio.records("resource-health") == []


def test_activation_and_manual_change_mark_result_stale(fixture):
    studio, build, key, health = fixture
    health.check(build["id"], request(build, key))
    reviewed = plan(studio, build, "activate", enable=[key])
    assert not reviewed["errors"]
    apply(studio, build, reviewed)
    view = studio.resource_lifecycle.inspect_build(build["id"])
    assert next(c for c in view["components"] if c["key"] == key)["health"]["status"] == "stale"
    latest = studio.builder.get(build["id"])
    health.check(build["id"], request(latest, key))
    record = studio.catalog.get("connectors", key.split(":")[1])
    record["description"] = "Changed configuration"
    studio._write(studio.catalog._path("connectors", record["id"]), record)
    # Any component revision change invalidates the previous health evidence.
    assert health.inspect(latest, {"key": key, "managed": True})["status"] == "stale"


def test_configuration_race_is_not_reported_healthy(fixture, monkeypatch):
    studio, build, key, health = fixture
    original = studio.operations.call_connector

    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        changed = studio.builder.get(build["id"])
        changed["revision"] += 1
        studio._write(studio._path("builds", build["id"]), changed)
        return result

    monkeypatch.setattr(studio.operations, "call_connector", racing)
    assert health.check(build["id"], request(build, key))["status"] == "stale"


def test_probe_error_messages_never_persist(fixture):
    studio, build, key, health = fixture
    (studio.root / "health.db").write_bytes(b"secret credential bearing invalid database")
    result = health.check(build["id"], request(build, key))
    assert result["status"] == "failed"
    assert result["reason"] == "connectivity_check_failed"
    assert "secret credential" not in json.dumps(studio.records("resource-health"))


def test_mcp_discovery_does_not_call_advertised_tools(tmp_path):
    server = tmp_path / "health_mcp.py"
    effect = tmp_path / "effect"
    server.write_text(
        """try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer
from pathlib import Path
server = MCPServer("Read-only health fixture")
@server.tool()
def dangerous() -> str:
    Path("""
        + repr(str(effect))
        + """).write_text("called")
    return "private-response"
server.run(transport="stdio")
"""
    )
    studio = StudioService(tmp_path)
    try:
        build = seed(
            studio,
            proposal(
                resources=[
                    {
                        "kind": "connectors",
                        "id": "mcp",
                        "name": "MCP discovery",
                        "data": {
                            "type": "mcp",
                            "command": sys.executable,
                            "args": [str(server)],
                            "actions": ["discover", "call"],
                            "tools": ["dangerous"],
                        },
                    }
                ]
            ),
        )
        key = next(
            c["key"]
            for c in studio.resource_lifecycle.inspect_build(build["id"])["components"]
            if c["key"].startswith("connectors:")
        )
        health = StudioResourceHealth(studio.resource_lifecycle)
        result = health.check(build["id"], request(build, key))
        assert result["status"] == "passed"
        assert not effect.exists()
        assert "private-response" not in json.dumps(result)
    finally:
        studio.close()
