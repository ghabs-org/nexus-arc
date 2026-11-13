"""Catalog recipes through real SDK transports; start the local Postman simulation first."""

import asyncio
import json
import os
from urllib.request import urlopen

import pytest

from nexus.core.studio import StudioService


@pytest.mark.parametrize(
    "identity,path,tool",
    [
        ("tavily", "/tavily/mcp", "tavily_search"),
        ("bright-data", "/bright/sse", "scrape_as_markdown"),
        ("zapier", "/zapier/{token}/sse", "add_row"),
    ],
)
def test_recipe_real_transport_selected_invocation_and_no_replay(
    tmp_path, monkeypatch, identity, path, tool
):
    base = os.environ.get("NEXUS_ACCEPTANCE_CATALOG_MCP_URL", "http://127.0.0.1:49911/mcp")
    monkeypatch.setenv("CATALOG_FIXTURE_TOKEN", "synthetic-catalog-token")
    with urlopen(base + "/fixture/calls", timeout=3) as response:
        previous_calls = len(json.load(response))
    studio = StudioService(tmp_path)
    try:
        record = studio.recipes.configure(identity, {"token_env": "CATALOG_FIXTURE_TOKEN"}, None)
        data = {**record["data"], "url": base + path, "allow_private": True}
        record = studio.catalog.save("connectors", {**record, "data": data})
        with pytest.raises(ValueError, match="disabled"):
            asyncio.run(studio.connections.execute(record["id"], {"action": "discover"}))
        discovered = asyncio.run(
            studio.connections.execute(record["id"], {"action": "discover"}, probe=True)
        )
        assert discovered[0]["name"] == tool
        assert discovered[0]["inputSchema"]["required"] == ["text"]

        data.update(enabled=True, actions=["discover", "call"], tools=[tool])
        studio.catalog.save("connectors", {**record, "data": data})
        payload = {"action": "call", "tool": tool, "arguments": {"text": identity}}
        with pytest.raises(ValueError, match="confirm"):
            asyncio.run(studio.connections.execute(record["id"], payload))
        with pytest.raises(PermissionError, match="not enabled"):
            asyncio.run(
                studio.connections.execute(
                    record["id"], {**payload, "tool": "unselected", "confirm": True}
                )
            )
        result = asyncio.run(studio.connections.execute(record["id"], {**payload, "confirm": True}))
        assert result == ("bright" if identity == "bright-data" else identity) + ":" + identity
        with pytest.raises(ValueError, match="Integration action failed"):
            asyncio.run(
                studio.connections.execute(
                    record["id"],
                    {
                        **payload,
                        "confirm": True,
                        "arguments": {"text": "fixture:reject"},
                    },
                )
            )
        with urlopen(base + "/fixture/calls", timeout=3) as response:
            calls = json.load(response)
        provider = "bright" if identity == "bright-data" else identity
        own = [row for row in calls[previous_calls:] if row["provider"] == provider]
        assert own == [
            {"provider": provider, "tool": tool, "text": identity},
            {"provider": provider, "tool": tool, "text": "fixture:reject"},
        ]
    finally:
        studio.close()
