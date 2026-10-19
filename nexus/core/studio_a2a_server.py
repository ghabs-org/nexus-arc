"""Opt-in A2A JSON-RPC text gateway for explicitly exposed bearer entrypoints.

Bearer callers run as the entrypoint creator. They cannot approve paused gates.
Task mappings and message deduplication share Studio's transactional state.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time

from nexus.core.studio_catalog import _text


class A2AError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class StudioA2AServer:
    def __init__(self, studio):
        self.studio = studio
        self.grpc_servers = {}

    def authorize(self, identity, bearer):
        record = self.studio.catalog.get("entrypoints", identity)
        if not record["data"].get("a2a_enabled") or not self.studio.operations.authorize_entrypoint(
            identity, bearer
        ):
            raise PermissionError("A2A entrypoint authorization required")
        if self.studio.team:
            self.studio.team.assert_execution(record.get("owner_id"))
        return record

    def card(self, record, origin, version):
        endpoint = origin + "/api/a2a/" + record["id"]
        common = {
            "name": record["name"],
            "description": record["description"] or "Nexus Studio text agent",
            "version": "1.0.0",
            "defaultInputModes": [
                "text/plain",
                "application/json",
                "application/pdf",
            ],
            "defaultOutputModes": ["text/plain"],
            "capabilities": {"streaming": True, "pushNotifications": False},
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
            "security": [{"bearer": []}],
            "skills": [
                {
                    "id": record["data"]["target_id"],
                    "name": record["name"],
                    "description": "Runs the configured target. Workflow approvals require the Nexus interface.",
                    "tags": ["text"],
                }
            ],
        }
        if record["data"]["target_kind"] == "agent":
            from nexus.core.studio_agent_settings import validate_settings

            target = self.studio.definition("agents", record["data"]["target_id"])
            metadata = target.get("spec", {}).get("agent_card")
            if metadata:
                metadata = validate_settings(self.studio, {"agent_card": metadata})["agent_card"]
                # Routing descriptions cannot add protocol, auth or execution capabilities.
                for field in ("description", "version", "documentationUrl", "provider"):
                    if field in metadata:
                        common[field] = metadata[field]
                if metadata.get("skills"):
                    common["skills"] = metadata["skills"]
        if version == "0.3":
            return {
                **common,
                "protocolVersion": "0.3.0",
                "url": endpoint,
                "preferredTransport": "JSONRPC",
            }
        common.pop("security")
        common["securitySchemes"] = {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}}
        common["securityRequirements"] = [{"schemes": {"bearer": {"list": []}}}]
        common["capabilities"]["extendedAgentCard"] = True
        interfaces = [
            {"url": endpoint, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
            {"url": endpoint + "/rest", "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"},
        ]
        grpc_server = self.grpc_servers.get(record["id"])
        if grpc_server:
            interfaces.append(grpc_server.interface)
        return {**common, "supportedInterfaces": interfaces}

    @staticmethod
    def version(value):
        version = value or "0.3"
        if version not in {"0.3", "1.0"}:
            raise A2AError(-32009, "Supported A2A versions: 1.0 and 0.3")
        return version

    def task(self, entrypoint, identity, version, history_length=0):
        try:
            mapping = self.studio._read(self.studio._path("a2a-tasks", identity))
            if mapping["entrypoint_id"] != entrypoint["id"] or mapping.get(
                "owner_id"
            ) != entrypoint.get("owner_id"):
                raise FileNotFoundError
            run = self.studio.run(identity)
        except FileNotFoundError:
            raise A2AError(-32001, "Task not found") from None
        state = {
            "queued": "submitted",
            "running": "working",
            "waiting": "input-required",
            "completed": "completed",
            "cancelled": "canceled",
        }.get(run["status"], "failed")
        result = {
            "id": identity,
            "contextId": mapping["context_id"],
            "status": {
                "state": "TASK_STATE_" + state.upper().replace("-", "_")
                if version == "1.0"
                else state,
                "timestamp": run.get("updated_at", run["created_at"]),
            },
        }
        if version == "0.3":
            result["kind"] = "task"
        if run.get("output"):
            result["artifacts"] = [
                {
                    "artifactId": identity + "-output",
                    "parts": [
                        {"text": run["output"], **({"kind": "text"} if version == "0.3" else {})}
                    ],
                }
            ]
        if run.get("error") or run["status"] == "waiting":
            result["status"]["message"] = {
                "messageId": identity + "-status",
                "role": "ROLE_AGENT" if version == "1.0" else "agent",
                "parts": [
                    {
                        "text": run.get("error") or "Approval required in the Nexus interface",
                        **({"kind": "text"} if version == "0.3" else {}),
                    }
                ],
                **({"kind": "message"} if version == "0.3" else {}),
            }
        if history_length:
            result["history"] = [
                {
                    "messageId": mapping["message_id"],
                    "role": "ROLE_USER" if version == "1.0" else "user",
                    "parts": [
                        {"text": run["task"], **({"kind": "text"} if version == "0.3" else {})}
                    ],
                    **({"kind": "message"} if version == "0.3" else {}),
                }
            ]
        return result

    @staticmethod
    def stopped(task):
        return str(task["status"]["state"]).lower().removeprefix("task_state_") not in {
            "working",
            "submitted",
        }

    def send(self, record, params, version):
        message = params.get("message")
        if not isinstance(message, dict) or message.get("role") != (
            "ROLE_USER" if version == "1.0" else "user"
        ):
            raise A2AError(-32602, "A user message is required")
        parts = message.get("parts")
        if (
            not isinstance(parts, list)
            or not 1 <= len(parts) <= 10
            or any(not isinstance(p, dict) for p in parts)
        ):
            raise A2AError(-32602, "Supply between one and ten message parts")
        texts, structured, files = [], [], []
        for part in parts:
            if sum(key in part for key in ("text", "data", "file", "raw", "url")) != 1:
                raise A2AError(-32602, "Each message part must have one content type")
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif isinstance(part.get("data"), (dict, list)):
                structured.append(part["data"])
            elif isinstance(part.get("file"), dict) and version == "0.3":
                file = part["file"]
                if not isinstance(file.get("bytes"), str) or file.get("uri"):
                    raise A2AError(
                        -32005, "Use inline file bytes; remote file URLs are not fetched"
                    )
                files.append((file.get("name", "attachment.bin"), file["bytes"]))
            elif isinstance(part.get("raw"), str) and version == "1.0":
                files.append((part.get("filename", "attachment.bin"), part["raw"]))
            else:
                raise A2AError(-32005, "Unsupported message part")
        if len(files) > 5 or len(json.dumps(structured)) > 64000:
            raise A2AError(-32602, "Use up to five files and 64 KiB of structured data")
        text = _text(
            "\n".join(texts)
            or (json.dumps(structured[0]) if structured else "Analyze the supplied files"),
            "Message text",
            16000,
        )
        message_id = _text(message.get("messageId"), "Message ID", 200)
        context_id = _text(message.get("contextId") or message_id, "Context ID", 200)
        digest = hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()
        key = hashlib.sha256((record["id"] + "\0" + message_id).encode()).hexdigest()
        with self.studio.lock:
            path = self.studio._path("a2a-messages", key)
            if path.exists():
                saved = self.studio._read(path)
                if saved["digest"] != digest:
                    raise A2AError(-32602, "Message ID already used with different content")
                return saved["run_id"]
            continuation_id = message.get("taskId")
            if continuation_id:
                self.task(record, continuation_id, version)
                run = self.studio.run(continuation_id)
                pending = run.get("awaiting_input", [])
                if run["status"] != "waiting" or not pending or files:
                    raise A2AError(
                        -32004,
                        "Task does not accept this continuation; approval gates require Nexus UI",
                    )
                value = structured[0] if structured else text
                values = {pending[0]["id"]: value} if len(pending) == 1 else value
                run = self.studio.graph.structured.resume_input(continuation_id, values)
                self.studio._write(path, {"id": key, "run_id": run["id"], "digest": digest})
                return run["id"]
            artifact_ids = []
            for index, (filename, encoded) in enumerate(files):
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise A2AError(-32602, "Invalid inline file encoding") from exc
                if len(content) > 16 * 1024 * 1024:
                    raise A2AError(-32602, "Inline file exceeds 16 MiB")
                artifact_id = "a2a-" + key[:48] + "-" + str(index)
                artifact_path = self.studio.catalog._path("artifacts", artifact_id)
                if not artifact_path.exists():
                    self.studio.catalog.save(
                        "artifacts",
                        {
                            "id": artifact_id,
                            "name": str(filename)[:200],
                            "requester_nexus_id": record.get("owner_id"),
                            "data": {"filename": filename, "content_base64": encoded},
                        },
                    )
                artifact_ids.append(artifact_id)
            request = {"task": text, "artifact_ids": artifact_ids}
            if structured:
                request["workflow_input"] = structured[0] if len(structured) == 1 else structured
            run = self.studio.operations.trigger_entrypoint(record["id"], request)
            self.studio._write(
                self.studio._path("a2a-tasks", run["id"]),
                {
                    "id": run["id"],
                    "entrypoint_id": record["id"],
                    "owner_id": record.get("owner_id"),
                    "context_id": context_id,
                    "message_id": message_id,
                },
            )
            self.studio._write(path, {"id": key, "run_id": run["id"], "digest": digest})
            return run["id"]

    def dispatch(self, record, request, version, origin):
        if (
            not isinstance(request, dict)
            or request.get("jsonrpc") != "2.0"
            or type(request.get("id")) not in {str, int}
        ):
            raise A2AError(-32600, "Expected a JSON-RPC request with an ID")
        method, params = request.get("method"), request.get("params", {})
        if not isinstance(params, dict):
            raise A2AError(-32602, "Parameters must be an object")
        methods = (
            {
                "SendMessage": "send",
                "SendStreamingMessage": "stream",
                "GetTask": "get",
                "SubscribeToTask": "subscribe",
                "CancelTask": "cancel",
                "ListTasks": "list",
                "GetExtendedAgentCard": "card",
            }
            if version == "1.0"
            else {
                "message/send": "send",
                "message/stream": "stream",
                "tasks/get": "get",
                "tasks/resubscribe": "subscribe",
                "tasks/cancel": "cancel",
                "agent/getAuthenticatedExtendedCard": "card",
            }
        )
        operation = methods.get(method)
        if operation is None:
            if "PushNotification" in str(method) or "pushNotification" in str(method):
                raise A2AError(-32003, "Push notifications are not offered; use task subscriptions")
            raise A2AError(-32601, "Method not found")
        return self.invoke(record, operation, params, version, origin)

    def invoke(self, record, operation, params, version, origin, *, wait_active=None):
        """One task service for JSON-RPC, REST and the MCP gateway."""
        if not isinstance(params, dict):
            raise A2AError(-32602, "Parameters must be an object")
        history = params.get("historyLength", 0)
        if type(history) is not int or not 0 <= history <= 100:
            raise A2AError(-32602, "History length must be between 0 and 100")
        if operation == "card":
            return self.card(record, origin, version), False
        if operation == "list":
            size = params.get("pageSize", 50)
            if type(size) is not int or not 1 <= size <= 100:
                raise A2AError(-32602, "Page size must be between 1 and 100")
            try:
                offset = int(params.get("pageToken") or "0")
                if not 0 <= offset <= 1000000:
                    raise ValueError
            except (ValueError, TypeError):
                raise A2AError(-32602, "Invalid page token") from None
            tasks = [
                self.task(record, row["id"], version)
                for row in self.studio.records("a2a-tasks", limit=None)
                if row["entrypoint_id"] == record["id"]
                and row.get("owner_id") == record.get("owner_id")
            ]
            tasks = [
                task
                for task in tasks
                if (not params.get("contextId") or task["contextId"] == params["contextId"])
                and (not params.get("status") or task["status"]["state"] == params["status"])
            ]
            return {
                "tasks": tasks[offset : offset + size],
                "nextPageToken": str(offset + size) if offset + size < len(tasks) else "",
                "totalSize": len(tasks),
            }, False
        if operation in {"send", "stream"}:
            config = params.get("configuration") or {}
            if not isinstance(config, dict):
                raise A2AError(-32602, "Configuration must be an object")
            flag = "returnImmediately" if version == "1.0" else "blocking"
            if flag in config and type(config[flag]) is not bool:
                raise A2AError(-32602, "Execution mode must be a boolean")
            if any(mode != "text/plain" for mode in config.get("acceptedOutputModes", [])):
                raise A2AError(-32005, "Only text/plain output is supported")
            if "pushNotificationConfig" in config or "taskPushNotificationConfig" in config:
                raise A2AError(-32003, "Push notifications are not offered")
            identity = self.send(record, params, version)
        else:
            identity = _text(params.get("id"), "Task ID", 96)
        task = self.task(record, identity, version, history)
        if operation == "cancel":
            actor = (
                {
                    "nexus_id": record["owner_id"],
                    "role": self.studio.team.bindings[record["owner_id"]],
                }
                if self.studio.team
                else None
            )
            try:
                self.studio.cancel_run(identity, actor)
            except ValueError as exc:
                raise A2AError(-32002, str(exc)) from exc
            task = self.task(record, identity, version)
        if operation == "subscribe" and self.stopped(task):
            raise A2AError(-32004, "Task is already terminal or paused; use GetTask")
        if operation == "send" and (
            config.get("returnImmediately") is not True
            if version == "1.0"
            else config.get("blocking") is True
        ):
            deadline = time.monotonic() + 300
            while not self.stopped(task) and not self.studio.closing:
                if wait_active is not None and not wait_active():
                    raise A2AError(-32004, "Request closed; retrieve the existing task by ID")
                if time.monotonic() > deadline:
                    raise A2AError(
                        -32000, "Wait timed out. Repeat the same message ID to retrieve its task"
                    )
                time.sleep(0.25)
                task = self.task(record, identity, version, history)
        return (
            {"task": task} if version == "1.0" and operation == "send" else task
        ), operation in {"stream", "subscribe"}

    def updates(self, record, task, version, bearer, *, active=None):
        identity, previous = task["id"], None
        deadline = time.monotonic() + 30
        while (
            not self.studio.closing and time.monotonic() < deadline and (active is None or active())
        ):
            self.authorize(record["id"], bearer)
            current = self.task(record, identity, version)
            if previous is None:
                yield {"task": current} if version == "1.0" else current
            elif current != previous:
                if current.get("artifacts") != previous.get("artifacts"):
                    for artifact in current.get("artifacts", []):
                        update = {
                            "taskId": identity,
                            "contextId": current["contextId"],
                            "artifact": artifact,
                            "append": False,
                            "lastChunk": self.stopped(current),
                        }
                        yield (
                            {"artifactUpdate": update}
                            if version == "1.0"
                            else {"kind": "artifact-update", **update}
                        )
                update = {
                    "taskId": identity,
                    "contextId": current["contextId"],
                    "status": current["status"],
                }
                yield (
                    {"statusUpdate": update}
                    if version == "1.0"
                    else {"kind": "status-update", "final": self.stopped(current), **update}
                )
            if self.stopped(current):
                return
            previous = current
            time.sleep(0.25)
