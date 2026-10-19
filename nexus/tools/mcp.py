"""Bridge MCP servers into the tool registry.

Each server tool becomes ``mcp:<server>:<tool>`` usable from workflows::

    await register_mcp_server(tool_registry, "files", "mcp-server-files")
    await tool_registry.call("mcp:files:list", path="/tmp")

Connections open per call (no lifecycle management) — fine for workflow
pace; add a persistent session pool if call volume ever matters.
Requires the ``mcp`` package: ``pip install nexus-arc[mcp]``.
"""

from __future__ import annotations

from typing import Any


def _require_mcp():
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise ImportError(
            "mcp package is required for MCP tools. Install it with: pip install nexus-arc[mcp]"
        ) from exc
    return ClientSession, StdioServerParameters, stdio_client


async def list_server_tools(
    command: str, args: list[str] | None = None, env: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Return [{name, description}] for tools served by an MCP stdio server."""
    session_type, parameters_type, stdio_client = _require_mcp()
    params = parameters_type(command=command, args=list(args or []), env=env)
    async with stdio_client(params) as (read, write):
        async with session_type(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "inputSchema": getattr(t, "inputSchema", getattr(t, "input_schema", {})),
        }
        for t in (listed.tools or [])
    ]


async def call_server_tool(
    command: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Call one MCP tool; returns concatenated text content."""
    session_type, parameters_type, stdio_client = _require_mcp()
    params = parameters_type(command=command, args=list(args or []), env=env)
    async with stdio_client(params) as (read, write):
        async with session_type(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, dict(arguments or {}))
    if getattr(result, "isError", getattr(result, "is_error", False)):
        raise RuntimeError("MCP server reported a tool error")
    texts = [
        getattr(block, "text", "")
        for block in (result.content or [])
        if getattr(block, "type", "") == "text"
    ]
    return "\n".join(texts)


async def register_mcp_server(
    registry: Any,
    server_name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """List an MCP server's tools and register each as ``mcp:<server>:<tool>``."""
    names: list[str] = []
    for info in await list_server_tools(command, args=args, env=env):
        qualified = f"mcp:{server_name}:{info['name']}"

        def bind(tool_name):
            async def invoke(**kwargs):
                return await call_server_tool(command, tool_name, kwargs, args=args, env=env)

            return invoke

        _invoke = bind(info["name"])
        _invoke.__name__ = qualified
        registry.register(
            qualified,
            _invoke,
            description=info["description"] or f"MCP tool {info['name']} on {server_name}",
            category="mcp",
        )
        names.append(qualified)
    return names
