"""Native adapter and MCP behavior without changing live accounts."""

import json
import time
from unittest.mock import AsyncMock

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_connections import validate_connection
from nexus.tools.registry import ToolRegistry


def done(service, run):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        current = service.run(run["id"])
        if current["status"] not in {"running", "queued"}:
            return current
        time.sleep(0.01)
    pytest.fail("Run did not finish")


@pytest.mark.asyncio
async def test_native_git_action_uses_configured_adapter_and_explicit_mutation_confirmation(
    tmp_path,
):
    adapter = AsyncMock()
    adapter.list_open_issues.return_value = [{"number": 3, "title": "Evidence"}]
    adapter.add_comment.return_value = {"id": "comment-1"}
    identities = []

    def factory(data, identity):
        identities.append(identity)
        return adapter

    app = StudioService(tmp_path, connection_options={"factories": {"github": factory}})
    try:
        app.catalog.save(
            "connectors",
            {
                "name": "Git work",
                "data": {
                    "type": "github",
                    "repo": "owner/project",
                    "enabled": True,
                    "actions": ["list_issues", "comment"],
                },
            },
        )
        assert await app.connections.execute("git-work", {}) == [{"number": 3, "title": "Evidence"}]
        with pytest.raises(ValueError, match="confirm"):
            await app.connections.execute(
                "git-work",
                {"action": "comment", "arguments": {"issue_id": "3", "body": "Reviewed"}},
            )
        await app.connections.execute(
            "git-work",
            {
                "action": "comment",
                "confirm": True,
                "arguments": {"issue_id": "3", "body": "Reviewed"},
            },
        )
        adapter.add_comment.assert_awaited_once_with(issue_id="3", body="Reviewed")
        tool = app.connections.tool("git-work")
        with pytest.raises(PermissionError, match="approved"):
            await tool(action="comment", confirm=True)
        with pytest.raises(PermissionError, match="Enable"):
            await app.connections.execute("git-work", {"action": "merge", "confirm": True})
        assert identities == [None, None]
    finally:
        app.close()


@pytest.mark.asyncio
async def test_mcp_discovery_named_calls_allowlists_and_minimal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_PERSONAL", "personal-key")
    monkeypatch.setenv("UNRELATED_SECRET", "never-forward")
    app = StudioService(tmp_path)
    listing = AsyncMock(return_value=[{"name": "lookup", "inputSchema": {"type": "object"}}])
    calling = AsyncMock(return_value="Found evidence")
    monkeypatch.setattr("nexus.tools.mcp.list_server_tools", listing)
    monkeypatch.setattr("nexus.tools.mcp.call_server_tool", calling)
    monkeypatch.setattr("shutil.which", lambda command: "/fixture/trusted-server")
    try:
        app.catalog.save(
            "connectors",
            {
                "name": "Research MCP",
                "data": {
                    "type": "mcp",
                    "command": "trusted-server",
                    "enabled": True,
                    "env": {"API_KEY": "MCP_PERSONAL"},
                    "tools": ["lookup", "write"],
                    "read_only_tools": ["lookup"],
                    "actions": ["discover", "call"],
                },
            },
        )
        assert (await app.connections.execute("research-mcp", {}))[0]["name"] == "lookup"
        result = await app.connections.tool("research-mcp")(
            action="call", tool="lookup", arguments={"q": "evidence"}
        )
        assert result == "Found evidence"
        env = calling.await_args.args[-1]
        assert env["API_KEY"] == "personal-key" and "UNRELATED_SECRET" not in env
        with pytest.raises(PermissionError):
            await app.connections.tool("research-mcp")(action="call", tool="write", confirm=True)
        with pytest.raises(PermissionError):
            await app.connections.execute(
                "research-mcp", {"action": "call", "tool": "unlisted", "confirm": True}
            )
    finally:
        app.close()


@pytest.mark.asyncio
async def test_mcp_framework_registration_binds_each_tool_and_preserves_arguments(monkeypatch):
    from nexus.tools import mcp

    monkeypatch.setattr(
        mcp,
        "list_server_tools",
        AsyncMock(
            return_value=[
                {"name": "first", "description": ""},
                {"name": "second", "description": ""},
            ]
        ),
    )
    caller = AsyncMock(return_value="ok")
    monkeypatch.setattr(mcp, "call_server_tool", caller)
    registry = ToolRegistry()
    names = await mcp.register_mcp_server(registry, "sample", "trusted")
    await registry.call(names[0], tool_name="argument-value")
    assert caller.await_args.args[1:3] == ("first", {"tool_name": "argument-value"})
    await registry.call(names[1])
    assert caller.await_args.args[1] == "second"


def test_agent_executes_opted_in_tool_then_returns_final_answer(tmp_path):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(
                success=True,
                output=json.dumps(
                    {
                        "tool_call": {
                            "name": "text:wordcount",
                            "arguments": {"text": "one two three"},
                        }
                    }
                )
                if len(calls) == 1
                else json.dumps({"final": "Three words"}),
            )

    app = StudioService(tmp_path, provider_factory=lambda name: Provider())
    try:
        app.save_definition(
            "agents", {"name": "Counter", "instructions": "Count", "tool_ids": ["text:wordcount"]}
        )
        run = done(app, app.start_run({"task": "Count supplied text", "agent_id": "counter"}))
        assert run["status"] == "completed" and run["output"] == "Three words"
        assert len(calls) == 2 and "Untrusted tool result:\n3" in calls[1]
        assert app.records("tool-traces")[0]["ok"] is True
    finally:
        app.close()


@pytest.mark.parametrize(
    "kind", ["github", "gitlab", "telegram", "discord", "slack", "openclaw", "mcp"]
)
def test_native_connections_are_disabled_by_default_and_validate_fields(kind):
    data = {
        "type": kind,
        "repo": "owner/project",
        "base_url": "https://example.test",
        "command": "trusted",
    }
    if kind == "github":
        data.pop("base_url")
    assert validate_connection(data)["enabled"] is False
    with pytest.raises(ValueError, match="environment"):
        validate_connection({**data, "token_env": "not a secret field!"})


@pytest.mark.asyncio
async def test_real_stdio_mcp_protocol_discovery_and_execution(tmp_path):
    import sys

    server = tmp_path / "mcp_server.py"
    server.write_text("""try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer
server = MCPServer("Studio protocol test")
@server.tool()
def word_count(text: str) -> str:
    return str(len(text.split()))
server.run(transport="stdio")
""")
    app = StudioService(tmp_path / "workspace")
    try:
        app.catalog.save(
            "connectors",
            {
                "name": "Local MCP",
                "data": {
                    "type": "mcp",
                    "command": sys.executable,
                    "args": [str(server)],
                    "enabled": True,
                    "actions": ["discover", "call"],
                    "tools": ["word_count"],
                    "read_only_tools": ["word_count"],
                },
            },
        )
        tools = await app.connections.execute("local-mcp", {"action": "discover"})
        assert tools[0]["name"] == "word_count" and "text" in tools[0]["inputSchema"]["properties"]
        result = await app.connections.tool("local-mcp")(
            action="call", tool="word_count", arguments={"text": "one two three"}
        )
        assert "3" in result
    finally:
        app.close()


@pytest.mark.asyncio
async def test_native_pull_request_actions_use_real_api_payload_shapes(monkeypatch):
    from nexus.adapters.git.github import GitHubPlatform
    from nexus.adapters.git.gitlab import GitLabPlatform

    for adapter, expected_path, result in [
        (GitHubPlatform(repo="o/r", token="test"), "repos/o/r/pulls", {"id": 1, "number": 3}),
        (
            GitLabPlatform(repo="o/r", token="test"),
            "projects/o%2Fr/merge_requests",
            {"id": 1, "iid": 3},
        ),
    ]:
        post = AsyncMock(return_value=result)
        monkeypatch.setattr(adapter, "_post", post)
        pr = await adapter.create_pull_request("Review", "Evidence", "feature", "main", True)
        assert pr.number == 3 and post.await_args.args[0] == expected_path
        payload = post.await_args.args[1]
        assert payload.get("head", payload.get("source_branch")) == "feature"
        if isinstance(adapter, GitHubPlatform):
            assert payload["draft"] is True
        else:
            assert payload["title"] == "Draft: Review"
