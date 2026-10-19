"""A2A 1.0 HTTP+JSON routing; task execution stays in StudioA2AServer."""

import re

from nexus.core.studio_a2a_server import A2AError

ERRORS = {
    -32001: (404, "NOT_FOUND", "TASK_NOT_FOUND"),
    -32002: (400, "FAILED_PRECONDITION", "TASK_NOT_CANCELABLE"),
    -32003: (400, "FAILED_PRECONDITION", "PUSH_NOTIFICATION_NOT_SUPPORTED"),
    -32004: (400, "FAILED_PRECONDITION", "UNSUPPORTED_OPERATION"),
    -32005: (400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED"),
    -32006: (500, "INTERNAL", "INVALID_AGENT_RESPONSE"),
    -32007: (400, "FAILED_PRECONDITION", "EXTENDED_AGENT_CARD_NOT_CONFIGURED"),
    -32008: (400, "FAILED_PRECONDITION", "EXTENSION_SUPPORT_REQUIRED"),
    -32009: (400, "FAILED_PRECONDITION", "VERSION_NOT_SUPPORTED"),
}


def error_response(error=None, *, status=None, message=None):
    code, label, reason = ERRORS.get(getattr(error, "code", None), (400, "INVALID_ARGUMENT", None))
    if status:
        code = status
        label = {
            403: "PERMISSION_DENIED",
            404: "NOT_FOUND",
            405: "UNIMPLEMENTED",
            413: "RESOURCE_EXHAUSTED",
            415: "INVALID_ARGUMENT",
            500: "INTERNAL",
        }.get(status, label)
    details = []
    if reason:
        details.append(
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": reason,
                "domain": "a2a-protocol.org",
            }
        )
    # Only deliberate protocol errors carry their message across the boundary.
    return code, {
        "error": {
            "code": code,
            "status": label,
            "message": message
            or (str(error) if isinstance(error, A2AError) else "Invalid request parameters"),
            "details": details,
        }
    }


def request_operation(method, path, query, body):
    if not isinstance(body, dict):
        raise A2AError(-32602, "Request must be an object")
    if method == "GET" and body:
        raise A2AError(-32602, "GET request bodies are not supported")
    if path == "extendedAgentCard" and method == "GET":
        operation, allowed = "card", set()
    elif path in {"message:send", "message:stream"} and method == "POST":
        operation = "send" if path.endswith(":send") else "stream"
        allowed = set()
    elif path == "tasks" and method == "GET":
        operation, allowed = "list", {"contextId", "status", "pageSize", "pageToken"}
    elif path.startswith("tasks/"):
        identity = path[6:]
        if "/pushNotificationConfigs" in identity:
            raise A2AError(-32003, "Push notifications are not offered; use task subscriptions")
        suffix = ""
        if ":" in identity:
            identity, suffix = identity.rsplit(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identity):
            raise A2AError(-32602, "Invalid task ID")
        operations = {
            ("GET", ""): "get",
            ("POST", "cancel"): "cancel",
            ("POST", "subscribe"): "subscribe",
        }
        operation = operations.get((method, suffix))
        if operation is None:
            raise A2AError(-32601, "Operation not found")
        allowed = {"historyLength"} if operation == "get" else set()
        if "id" in body and body["id"] != identity:
            raise A2AError(-32602, "Body task ID must match the URL")
        body = {**body, "id": identity}
    else:
        raise A2AError(-32601, "Operation not found")
    if set(query) - allowed or any(len(values) != 1 for values in query.values()):
        raise A2AError(-32602, "Unsupported or repeated query parameters")
    params = dict(body)
    for key, values in query.items():
        value = values[0]
        if key in {"pageSize", "historyLength"}:
            if not re.fullmatch(r"[0-9]{1,6}", value):
                raise A2AError(-32602, "Numeric query parameters must be decimal integers")
            value = int(value)
        params[key] = value
    if params.get("tenant"):
        raise A2AError(-32004, "Use the entrypoint URL to select a Nexus agent")
    return operation, params
