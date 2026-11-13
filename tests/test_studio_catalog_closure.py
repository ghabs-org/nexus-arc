"""Catalog identity/configuration checks; invocation acceptance is separate.

Verifies:
1. All 4 external platform exclusions are retained with explicit reason.
2. All 27 supported entries provide usable configuration templates without leaking credentials.
3. Community recipe configuration and mocked discovery retain their contracts.
4. Direct configuration readiness is exposed and evaluated per entry.
5. Distinct alias identities are preserved.
"""

import asyncio
import json
from pathlib import Path

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_catalog_setup import describe_setup, setup_metadata
from tests.test_studio_research import public_dns as public_dns


def test_catalog_inventory_closure_and_exclusions(tmp_path):
    studio = StudioService(tmp_path)
    try:
        catalog = studio.recipes.list()
        items = catalog["items"]
        official = catalog["official_items"]
        excluded = catalog["excluded"]

        assert len(items) == 12, "Community items count must be exactly 12"
        assert len(official) == 15, "Official supported items count must be exactly 15"
        assert len(excluded) == 4, "Excluded external items count must be exactly 4"
        assert len(items) + len(official) + len(excluded) == 31, "Total pinned catalog count must be 31"

        # Check explicit exclusions
        excluded_ids = {e["id"] for e in excluded}
        assert excluded_ids == {
            "community-event-mesh-agent",
            "community-event-mesh-gateway",
            "community-event-mesh-identity-provider",
            "community-event-mesh-tool",
        }
        for e in excluded:
            assert "Vendor product excluded" in e["reason"]
    finally:
        studio.close()


def test_all_27_supported_entries_have_usable_configuration_and_no_secrets(tmp_path):
    studio = StudioService(tmp_path)
    try:
        catalog = studio.recipes.list()
        all_supported = catalog["items"] + catalog["official_items"]
        assert len(all_supported) == 27

        for entry in all_supported:
            setup = entry.get("setup_metadata")
            assert setup is not None, f"Entry '{entry['id']}' missing setup_metadata"
            assert setup.get("billing"), f"Entry '{entry['id']}' missing billing disclosure"
            assert len(setup.get("requirements", [])) > 0, f"Entry '{entry['id']}' missing requirements"
            assert len(setup.get("installation", [])) > 0, f"Entry '{entry['id']}' missing installation guide"

            # Check configuration template
            cfg_template = setup.get("configuration_template")
            assert cfg_template is not None, f"Entry '{entry['id']}' missing configuration_template"
            template_str = json.dumps(cfg_template)
            assert "YOUR_API_KEY" not in template_str
            assert "SECRET" not in template_str or "YOUR_REGION" in template_str or "AWS_SECRET_ACCESS_KEY" in template_str

            # Check readiness
            readiness = entry.get("readiness")
            assert readiness is not None, f"Entry '{entry['id']}' missing readiness"
            assert readiness["state"] in ("setup-required", "dependencies-missing", "configuration-present")
            assert readiness["live_verified"] is False
    finally:
        studio.close()


def test_alias_identities_are_preserved(tmp_path):
    studio = StudioService(tmp_path)
    try:
        catalog = studio.recipes.list()
        ids = {r["id"] for r in catalog["official_items"] + catalog["items"]}

        # sql-database and sql-database-tool must both exist as separate IDs
        assert "community-sql-database" in ids
        assert "community-sql-database-tool" in ids

        sql_db = next(r for r in catalog["official_items"] if r["id"] == "community-sql-database")
        sql_tool = next(r for r in catalog["official_items"] if r["id"] == "community-sql-database-tool")

        assert sql_db["setup_metadata"]["alias_of"] == "community-sql-database-tool"
        assert sql_tool["setup_metadata"]["alias_of"] is None
    finally:
        studio.close()


def test_community_recipe_configuration_and_mocked_discovery(tmp_path, monkeypatch, public_dns):
    studio = StudioService(tmp_path)
    try:
        # Mock remote mcp
        calls = []

        async def mock_remote(url, *args, **kwargs):
            calls.append((url, kwargs))
            return [{"name": "search", "description": "Search web", "inputSchema": {"type": "object"}}]

        monkeypatch.setattr("nexus.tools.mcp_remote.remote_mcp", mock_remote)
        monkeypatch.setenv("TAVILY_KEY", "mock-tavily-key")
        monkeypatch.setenv("BRIGHTDATA_KEY", "mock-brightdata-key")
        monkeypatch.setenv("ZAPIER_KEY", "mock-zapier-key")

        # 1. Tavily
        tav = studio.recipes.configure("tavily", {"token_env": "TAVILY_KEY"}, None)
        assert tav["data"]["type"] == "mcp"
        assert not tav["data"]["enabled"]
        res = asyncio.run(studio.connections.execute(tav["id"], {"action": "discover"}, probe=True))
        assert res[0]["name"] == "search"

        # 2. Bright Data
        bd = studio.recipes.configure("bright-data", {"token_env": "BRIGHTDATA_KEY"}, None)
        assert bd["data"]["type"] == "mcp"
        res_bd = asyncio.run(studio.connections.execute(bd["id"], {"action": "discover"}, probe=True))
        assert res_bd[0]["name"] == "search"

        # 3. Zapier
        zap = studio.recipes.configure("zapier", {"token_env": "ZAPIER_KEY"}, None)
        assert zap["data"]["type"] == "mcp"
        res_zap = asyncio.run(studio.connections.execute(zap["id"], {"action": "discover"}, probe=True))
        assert res_zap[0]["name"] == "search"

        # 4. Filesystem
        fs = studio.recipes.configure("filesystem", {"command": "/bin/mcp-fs"}, None)
        assert fs["data"]["command"] == "/bin/mcp-fs"
        assert fs["data"]["args"] == [str(tmp_path)]

        # 5. SendGrid
        sg = studio.recipes.configure("send-grid", {}, None)
        assert sg["data"]["type"] == "openapi"
        assert sg["data"]["operations"] == ["sendMail"]

        # 6. Finance
        fin = studio.recipes.configure("finance", {}, None)
        assert fin["tool_id"] and "setup" in fin

        # 7. Find My IP
        ip_tool = studio.recipes.configure("find-my-ip", {}, None)
        assert ip_tool["tool_id"] and "setup" in ip_tool

        # 8. Web Agent
        web_tool = studio.recipes.configure("web-agent", {}, None)
        assert web_tool["tool_id"] and "setup" in web_tool

        # 9. ImageMagick
        im_tool = studio.recipes.configure("imagemagick", {}, None)
        assert im_tool["tool_id"] and "setup" in im_tool

        # 10. Object Detection
        od_tool = studio.recipes.configure("object-detection", {}, None)
        assert od_tool["tool_id"] and "setup" in od_tool

        # 11. Artifact Host Agent
        studio._write(studio._path("sessions", "test-session"), {"id": "test-session", "title": "Test", "messages": []})
        sharing_cfg = studio.recipes.configure("artifact-host-agent", {"session_id": "test-session"}, None)
        assert sharing_cfg["id"] and sharing_cfg["session_id"] == "test-session"

        # 12. CLI Entrypoint
        cli_cfg = studio.recipes.configure("cli-entrypoint", {}, None)
        assert "nexus chat" in cli_cfg["command"]
    finally:
        studio.close()


def test_official_connectors_configuration_and_validation(tmp_path):
    studio = StudioService(tmp_path)
    try:
        from nexus.core.studio_connections import validate_connection

        # MongoDB
        mongo_cfg = {
            "type": "mongodb",
            "dsn_env": "MONGO_URI",
            "database": "analytics",
            "collections": ["events"],
            "allow_writes": False,
            "actions": ["query"],
            "enabled": False,
        }
        val_mongo = validate_connection(mongo_cfg)
        assert val_mongo["type"] == "mongodb"
        assert val_mongo["enabled"] is False

        # SQL
        sql_cfg = {
            "type": "sql",
            "dsn_env": "DATABASE_URL",
            "dialect": "postgresql",
            "actions": ["query"],
            "allow_writes": False,
            "enabled": False,
        }
        val_sql = validate_connection(sql_cfg)
        assert val_sql["type"] == "sql"
        assert val_sql["dialect"] == "postgresql"

        # Slack
        slack_cfg = {
            "type": "slack",
            "token_env": "SLACK_BOT_TOKEN",
            "channel": "C12345",
            "actions": ["send"],
            "enabled": False,
        }
        val_slack = validate_connection(slack_cfg)
        assert val_slack["type"] == "slack"
        assert val_slack["channel"] == "C12345"

        # Bedrock
        bedrock_cfg = {
            "type": "bedrock",
            "region": "us-east-1",
            "resource_id": "agent-123",
            "alias_id": "alias-456",
            "access_key_env": "AWS_ACCESS_KEY_ID",
            "secret_key_env": "AWS_SECRET_ACCESS_KEY",
            "actions": ["invoke"],
            "enabled": False,
        }
        val_bedrock = validate_connection(bedrock_cfg)
        assert val_bedrock["type"] == "bedrock"
        assert val_bedrock["region"] == "us-east-1"
    finally:
        studio.close()
