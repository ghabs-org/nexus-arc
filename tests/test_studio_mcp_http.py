"""Independent JSON-RPC HTTP fixture exercises the real MCP client SDK."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nexus.tools.mcp_remote import remote_mcp


def test_real_remote_mcp_sdk_discovers_schema_and_invokes_tool_without_replaying():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = message["method"]
            seen.append((method, self.headers.get("Authorization")))
            if method.startswith("notifications/"):
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {
                    "protocolVersion": message["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "independent-fixture", "version": "1.0"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo supplied text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        }
                    ]
                }
            elif method == "tools/call":
                result = {
                    "content": [{"type": "text", "text": message["params"]["arguments"]["text"]}],
                    "isError": False,
                }
            else:
                raise AssertionError(method)
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Mcp-Session-Id", "fixture-session")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_response(405)
            self.end_headers()

        def do_DELETE(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/mcp"
    try:
        tools = asyncio.run(remote_mcp(url, headers={"Authorization": "Bearer fixture-token"}))
        assert tools[0]["name"] == "echo" and tools[0]["inputSchema"]["required"] == ["text"]
        result = asyncio.run(
            remote_mcp(
                url,
                headers={"Authorization": "Bearer fixture-token"},
                tool="echo",
                arguments={"text": "HTTP transport checked"},
            )
        )
        assert result == "HTTP transport checked"
        assert [method for method, _ in seen].count("tools/call") == 1
        assert all(value == "Bearer fixture-token" for _, value in seen)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
