import asyncio
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler
from nexus.tools.mcp_remote import remote_mcp


def test_gateway_real_mcp_sdk_uses_shared_task_service_and_deduplicates_submission(
    tmp_path, monkeypatch
):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(success=True, output="Gateway answer")

    monkeypatch.setenv("MCP_GATEWAY_TEST", "test-gateway-token-with-32-characters")
    studio = StudioService(tmp_path, provider_factory=lambda name: Provider())
    agent = studio.save_definition(
        "agents",
        {"name": "Gateway agent", "instructions": "Help with the task", "provider": "codex"},
    )
    record = studio.catalog.save(
        "entrypoints",
        {
            "name": "MCP test",
            "data": {
                "target_kind": "agent",
                "target_id": agent["id"],
                "token_env": "MCP_GATEWAY_TEST",
                "enabled": True,
                "mcp_enabled": True,
            },
        },
    )
    other = studio.catalog.save(
        "entrypoints", {"name": "Other MCP", "data": {**record["data"], "mcp_enabled": True}}
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "test-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/mcp/{record['id']}"
    headers = {"Authorization": "Bearer test-gateway-token-with-32-characters"}

    async def invoke(name, args):
        return json.loads(await remote_mcp(url, headers=headers, tool=name, arguments=args))

    try:
        tools = asyncio.run(remote_mcp(url, headers=headers))
        assert {t["name"] for t in tools} == {
            "submit_task",
            "get_task",
            "cancel_task",
            "provide_input",
        }
        args = {"task": "Answer once", "request_id": "stable-message-id"}
        task = asyncio.run(invoke("submit_task", args))
        again = asyncio.run(invoke("submit_task", args))
        assert task["id"] == again["id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = asyncio.run(invoke("get_task", {"task_id": task["id"]}))
            if result["status"]["state"] == "completed":
                break
        assert result["artifacts"][0]["parts"][0]["text"] == "Gateway answer"
        assert len(calls) == 1 and len(studio.records("runs")) == 1
        assert studio._read(studio._path("a2a-tasks", task["id"]))["entrypoint_id"] == record["id"]
        reply = studio.mcp_server.dispatch(
            other,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_task", "arguments": {"task_id": task["id"]}},
            },
        )
        assert reply["isError"]
        with pytest.raises(PermissionError):
            studio.mcp_server.authorize(record["id"], "wrong-token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.close()
