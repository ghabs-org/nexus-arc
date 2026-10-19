"""Remote MCP transport using the installed SDK, with explicit credentials."""

import asyncio
import inspect
from urllib.parse import urlparse


async def remote_mcp(url, transport="streamable-http", headers=None, tool=None, arguments=None):
    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        from mcp.client import sse
        from mcp.client import streamable_http
    except ImportError as exc:
        raise ValueError("Install nexus-arc[mcp] for remote MCP") from exc

    async def invoke(streams):
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            if tool is None:
                result = await session.list_tools()
                return [{"name": t.name, "description": t.description or "",
                         "inputSchema": getattr(t, "inputSchema", getattr(t, "input_schema", {}))}
                        for t in result.tools or []]
            result = await session.call_tool(tool, arguments or {})
            if getattr(result, "isError", getattr(result, "is_error", False)):
                raise ValueError("Remote MCP tool reported an error")
            return "\n".join(block.text for block in result.content or [] if getattr(block, "type", "") == "text")

    async def connect():
        origin = urlparse(url)
        async def guard(request):
            target = urlparse(str(request.url))
            if (target.scheme, target.hostname, target.port) != (origin.scheme, origin.hostname, origin.port):
                raise ValueError("MCP server cannot redirect account credentials to another origin")
        def factory(headers=None, timeout=None, auth=None):
            http = getattr(sse, "httpx2", None) or getattr(sse, "httpx", None)
            return http.AsyncClient(headers=headers or {}, timeout=timeout or 55, auth=auth,
                                    trust_env=False, follow_redirects=False, event_hooks={"request": [guard]})
        if transport == "sse":
            async with sse_client(url, headers=headers or {}, timeout=10, sse_read_timeout=55, httpx_client_factory=factory) as streams:
                return await invoke(streams)
        client = getattr(streamable_http, "streamable_http_client", None) or streamable_http.streamablehttp_client
        if "http_client" in inspect.signature(client).parameters:
            # MCP SDK 2 uses httpx2; SDK 1 uses httpx. Match its own HTTP types.
            http = getattr(streamable_http, "httpx2", None) or getattr(streamable_http, "httpx", None)
            async with http.AsyncClient(headers=headers or {}, timeout=55, follow_redirects=False, trust_env=False, event_hooks={"request": [guard]}) as http_client:
                async with client(url, http_client=http_client) as streams:
                    return await invoke(streams)
        elif "httpx_client_factory" in inspect.signature(client).parameters:
            async with client(url, headers=headers or {}, timeout=55, httpx_client_factory=factory) as streams:
                return await invoke(streams)
        else:
            raise ValueError("Upgrade MCP SDK for guarded remote HTTP transport")

    try:
        return await asyncio.wait_for(connect(), 60)
    except Exception as exc:
        raise ValueError("Remote MCP request failed; verify transport, account access and server availability") from exc
