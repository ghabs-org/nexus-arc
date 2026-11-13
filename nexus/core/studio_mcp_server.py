"""Stateless MCP Streamable HTTP gateway over Studio's shared A2A task service.

Only the selected bearer entrypoint is exposed. Polling, cancellation and human
input use the same durable tasks as A2A. Approval gates remain in Nexus Studio.
"""

import json

from nexus.core.studio_a2a_server import A2AError
from nexus.core.studio_catalog import _text


class StudioMCPServer:
    def __init__(self, studio):
        self.studio = studio

    def authorize(self, identity, bearer):
        record = self.studio.catalog.get("entrypoints", identity)
        if not record["data"].get("mcp_enabled") or not self.studio.operations.authorize_entrypoint(
            identity, bearer
        ):
            raise PermissionError("MCP entrypoint authorization required")
        if self.studio.team:
            self.studio.team.assert_execution(record.get("owner_id"))
        return record

    @staticmethod
    def tools(record):
        return [
            {
                "name": "submit_task",
                "description": "Start the configured Nexus agent/workflow task. Reuse request_id only to recover the same submission; poll using get_task. "
                + record["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "maxLength": 16000},
                        "request_id": {"type": "string", "maxLength": 150},
                        "input": {"description": "Optional structured workflow input"},
                        "files": {
                            "type": "array",
                            "maxItems": 5,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "maxLength": 200},
                                    "bytes": {"type": "string"},
                                },
                                "required": ["name", "bytes"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["task", "request_id"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": False, "idempotentHint": True},
            },
            {
                "name": "get_task",
                "description": "Inspect an existing task without submitting or repeating work",
                "inputSchema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
            },
            {
                "name": "cancel_task",
                "description": "Request task cancellation. Already-started external effects can still complete; cancellation does not undo them.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": False, "idempotentHint": False},
            },
            {
                "name": "provide_input",
                "description": "Continue a task awaiting typed user input. Approval gates must be reviewed in Nexus Studio.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "request_id": {"type": "string", "maxLength": 150},
                        "input": {},
                    },
                    "required": ["task_id", "request_id", "input"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": False, "idempotentHint": True},
            },
        ]

    def dispatch(self, record, request):
        if (
            not isinstance(request, dict)
            or request.get("jsonrpc") != "2.0"
            or not isinstance(request.get("method"), str)
        ):
            raise A2AError(-32600, "Expected a JSON-RPC object")
        method, params = request["method"], request.get("params", {})
        if not isinstance(params, dict):
            raise A2AError(-32602, "Parameters must be an object")
        if method.startswith("notifications/") and "id" not in request:
            return None
        if type(request.get("id")) not in {str, int}:
            raise A2AError(-32600, "Request ID must be a string or integer")
        if method == "initialize":
            return {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "nexus-studio", "version": "1.0.0"},
                "instructions": "Only this explicitly configured target is exposed. Submit once with a stable request_id and poll its task. Human approval remains in Nexus Studio.",
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools(record)}
        if method != "tools/call":
            raise A2AError(-32601, "Method not supported")
        name, arguments = params.get("name"), params.get("arguments", {})
        spec = next((tool for tool in self.tools(record) if tool["name"] == name), None)
        if spec is None:
            raise A2AError(-32602, "Tool not found")
        from nexus.core.studio_openapi import _validate

        try:
            _validate({}, spec["inputSchema"], arguments)
            if name in {"submit_task", "provide_input"}:
                request_id = _text(arguments["request_id"], "Request ID", 150)
                message = {"role": "user", "messageId": "mcp-" + request_id, "parts": []}
                if "task" in arguments:
                    message["parts"].append({"kind": "text", "text": arguments["task"]})
                if "input" in arguments:
                    # A2A's structured part supports object/list. Wrap scalars
                    # for initial workflow input; typed continuations unwrap below.
                    value = arguments["input"]
                    if not isinstance(value, (dict, list)):
                        if name == "provide_input" and isinstance(value, str):
                            message["parts"].append({"kind": "text", "text": value})
                        elif name == "provide_input" and type(value) in {int, float, bool}:
                            message["parts"].append(
                                {"kind": "data", "data": {"_mcp_scalar": value}}
                            )
                        else:
                            raise ValueError("Structured task input must be an object or array")
                    else:
                        message["parts"].append({"kind": "data", "data": value})
                for file in arguments.get("files", []):
                    message["parts"].append(
                        {"kind": "file", "file": {"name": file["name"], "bytes": file["bytes"]}}
                    )
                if name == "provide_input":
                    message["taskId"] = arguments["task_id"]
                identity = self.studio.a2a.send(record, {"message": message}, "0.3")
                result = self.studio.a2a.task(record, identity, "0.3")
            else:
                identity = arguments["task_id"]
                result = self.studio.a2a.task(record, identity, "0.3")
                if name == "cancel_task":
                    actor = (
                        {
                            "nexus_id": record["owner_id"],
                            "role": self.studio.team.bindings[record["owner_id"]],
                        }
                        if self.studio.team
                        else None
                    )
                    self.studio.cancel_run(identity, actor)
                    result = self.studio.a2a.task(record, identity, "0.3")
            return {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "structuredContent": result,
                "isError": False,
            }
        except (ValueError, PermissionError, FileNotFoundError):
            # Do not leak SDK credentials, other task IDs or raw account errors.
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Task action failed. Review its ID, required input, permissions and Activity before resubmitting.",
                    }
                ],
                "isError": True,
            }
