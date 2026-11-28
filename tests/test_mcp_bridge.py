"""MCP bridge with a fake `mcp` SDK module."""

import asyncio
import sys
import types


def _install_fake_mcp(monkeypatch):
    calls = []

    class _Tool:
        def __init__(self, name, description=""):
            self.name = name
            self.description = description

    class _Session:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            pass

        async def list_tools(self):
            ns = types.SimpleNamespace(tools=[_Tool("list", "List things")])
            return ns

        async def call_tool(self, name, args):
            calls.append((name, args))
            block = types.SimpleNamespace(type="text", text=f"called:{name}")
            return types.SimpleNamespace(content=[block])

    class _stdio_client:
        def __init__(self, params):
            self.params = params

        async def __aenter__(self):
            return (None, None)

        async def __aexit__(self, *exc):
            return False

    fake_client_stdio = types.ModuleType("mcp.client.stdio")
    fake_client_stdio.stdio_client = _stdio_client
    fake_client = types.ModuleType("mcp.client")
    fake_client.stdio = fake_client_stdio
    fake_mcp = types.ModuleType("mcp")
    fake_mcp.ClientSession = _Session
    fake_mcp.StdioServerParameters = lambda command, args=None, env=None: (command, args, env)
    fake_mcp.client = fake_client
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", fake_client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", fake_client_stdio)
    return calls


def test_register_and_call_mcp_tool(monkeypatch):
    from nexus.tools import ToolRegistry
    from nexus.tools.mcp import register_mcp_server

    calls = _install_fake_mcp(monkeypatch)
    registry = ToolRegistry()
    names = asyncio.run(register_mcp_server(registry, "files", "mcp-server-files"))
    assert names == ["mcp:files:list"]
    result = asyncio.run(registry.call("mcp:files:list", path="/tmp"))
    assert result.ok is True
    assert result.output == "called:list"
    assert calls == [("list", {"path": "/tmp"})]


def test_missing_sdk_raises_helpfully(monkeypatch):
    import pytest

    monkeypatch.delitem(sys.modules, "mcp", raising=False)
    monkeypatch.delitem(sys.modules, "mcp.client", raising=False)
    monkeypatch.delitem(sys.modules, "mcp.client.stdio", raising=False)
    import builtins

    real_import = builtins.__import__

    def _guarded(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("No module named 'mcp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded)
    from nexus.tools.mcp import list_server_tools

    with pytest.raises(ImportError, match="nexus-arc\\[mcp\\]"):
        asyncio.run(list_server_tools("whatever"))
