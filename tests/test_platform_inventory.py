"""Platform inventory lists providers and registered tools."""


def test_inventory_structure():
    import asyncio

    import nexus.tools  # noqa: F401  (registers builtins)
    from nexus.core.command_bridge.platform_handler import handle_platform_inventory
    from nexus.init_project import PROVIDERS

    result = asyncio.run(handle_platform_inventory({}, None))
    assert result["ok"] is True
    assert [p["name"] for p in result["providers"]] == list(PROVIDERS)
    assert all(isinstance(p["available"], bool) for p in result["providers"])
    names = [t["name"] for t in result["tools"]]
    assert "text:wordcount" in names
    assert all({"name", "description", "category"} <= set(t) for t in result["tools"])
