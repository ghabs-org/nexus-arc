"""Version-negotiated A2A JSON-RPC discovery and durable, owner-scoped remote tasks.

Supports A2A 1.0 and 0.3 JSON-RPC. Sends are never retried automatically. Cards
cannot redirect credentials to another origin or silently switch transports.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import time
import urllib.request
import uuid
from urllib.parse import urlparse

from nexus.core.studio_catalog import _text
from nexus.core.studio_connections import env_name
from nexus.core.studio_external_content import RESPONSE_LIMIT, StudioExternalContent, binding
from nexus.core.studio_graph import StudioPausedError


def validate_external(data):
    url = _text(data.get("url"), "Agent service URL", 2000).rstrip("/")
    parsed = urlparse(url)
    local = data.get("allow_private", False)
    enabled = data.get("enabled", False)
    if type(local) is not bool or type(enabled) is not bool:
        raise ValueError("Enabled and private-network options must be booleans")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in ({"https", "http"} if local else {"https"})
    ):
        raise ValueError(
            "Use HTTPS without credentials/query; private HTTP must be explicitly allowed"
        )
    version = data.get("protocol_version", "1.0")
    if version not in {"1.0", "0.3"}:
        raise ValueError("Supported A2A protocol versions are 1.0 and 0.3 JSON-RPC")
    token = data.get("token_env", "")
    card_path = data.get("card_path", "/.well-known/agent-card.json")
    if (
        not isinstance(card_path, str)
        or not card_path.startswith("/")
        or card_path.startswith("//")
        or urlparse(card_path).query
        or urlparse(card_path).fragment
        or ".." in card_path.split("/")
    ):
        raise ValueError("Agent card path must be an absolute path on the configured origin")
    if type(data.get("streaming", False)) is not bool:
        raise ValueError("Streaming must be true or false")
    return {
        "url": url,
        "token_env": env_name(token) if token else "",
        "protocol_version": version,
        "enabled": enabled,
        "allow_private": local,
        "streaming": data.get("streaming", False),
        "card_path": card_path,
    }


class StudioExternalAgents:
    def __init__(self, studio, request_client=None, stream_client=None, callback_origin=None):
        self.studio = studio
        self.request_client = request_client
        self.stream_client = stream_client
        self.content = StudioExternalContent(self)
        from nexus.core.studio_external_callbacks import StudioExternalCallbacks

        self.callbacks = StudioExternalCallbacks(self)
        self.callback_origin = callback_origin

    @staticmethod
    def assert_binding(profile, record):
        if record.get("profile_binding") and record["profile_binding"] != binding(profile):
            raise ValueError(
                "External connection changed; review the saved remote task before continuing"
            )

    def prepare_payload(self, payload):
        return self.content.prepare(payload)

    def publish_result(self, record, objects, profile, actor):
        output, planned = self.content.plan(objects, profile, actor)
        with self.studio.lock:
            self.assert_binding(self.profile(profile["id"]), record)
            run = self.studio.run(record["id"])
            if run.get("cancel_requested"):
                raise ValueError("Local remote-task monitoring was cancelled")
            parts = self.content.publish(record["id"], planned, actor)
            record.update(output=output, content_version=1, structured_content=parts)
            self.studio._update_run(
                record["id"], output=output, content_version=1, structured_content=parts
            )
        return output

    def download(self, url, profile, actor, limit):
        self.assert_binding(self.profile(profile["id"]), {"profile_binding": binding(profile)})
        source, target = (
            urlparse(profile["data"]["url"]),
            urlparse(url if isinstance(url, str) else ""),
        )
        if (
            not isinstance(url, str)
            or len(url) > 2000
            or (source.scheme, source.netloc) != (target.scheme, target.netloc)
            or target.username
            or target.password
            or target.fragment
        ):
            raise ValueError("Remote artifact URLs must use the configured agent origin")
        data = self.request(url, profile["data"], actor, binary_limit=min(limit, 16 * 1024 * 1024))
        if not isinstance(data, bytes) or len(data) > min(limit, 16 * 1024 * 1024):
            raise ValueError("Remote artifact download exceeds limits or returned invalid bytes")
        return data

    def headers(self, data, actor):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "A2A-Version": data["protocol_version"],
        }
        if self.studio.team:
            self.studio.team.assert_execution(actor)
            values, error = self.studio.team.environment_resolver(actor)
            if error:
                raise PermissionError("Your external-agent account setup is not ready")
        else:
            values = os.environ
        if data["token_env"]:
            token = values.get(data["token_env"])
            if not token or "\r" in token or "\n" in token:
                raise ValueError("External-agent credential is not configured for your account")
            headers["Authorization"] = "Bearer " + token
        return headers

    def request(self, url, data, actor, body=None, *, binary_limit=None):
        # Validate every request, including endpoints obtained from remote cards.
        parsed = urlparse(url)
        if not data["allow_private"]:
            addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
            if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ValueError(
                    "External agent resolves to a private network; configure that explicitly"
                )
        headers = self.headers(data, actor)
        if binary_limit is not None:
            headers["Accept"] = "application/octet-stream"
        if self.request_client:
            return self.request_client(url, body, headers)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("External-agent redirects are disabled")

        request = urllib.request.Request(
            url, data=json.dumps(body).encode() if body is not None else None, headers=headers
        )
        try:
            limit = (
                binary_limit
                if binary_limit is not None
                else RESPONSE_LIMIT
                if body is not None
                else 1024 * 1024
            )
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(
                request, timeout=30
            ) as response:
                content = response.read(limit + 1)
                if len(content) > limit:
                    raise ValueError("External-agent response exceeds configured limits")
                if binary_limit is not None:
                    return content
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError("Expected an external-agent object")
                return result
        except Exception:
            raise ValueError(
                "External-agent request failed. Review account configuration and remote task state before retrying"
            ) from None

    def profile(self, identity):
        record = self.studio.catalog.get("externalagents", identity)
        if not record["data"]["enabled"]:
            raise ValueError("External agent is disabled")
        return record

    def discover(self, identity, actor, *, probe=False, expected_data=None):
        record = (
            self.studio.catalog.get("externalagents", identity) if probe else self.profile(identity)
        )
        if expected_data is not None and record["data"] != expected_data:
            raise ValueError("External-agent connection changed before its check")
        data = record["data"]
        parsed = urlparse(data["url"])
        origin = f"{parsed.scheme}://{parsed.netloc}"
        card = self.request(
            origin + data.get("card_path", "/.well-known/agent-card.json"), data, actor
        )
        if data["protocol_version"] == "1.0":
            interfaces = card.get("supportedInterfaces", [])
            interface = next(
                (
                    item
                    for item in interfaces
                    if isinstance(item, dict)
                    and item.get("protocolBinding") == "JSONRPC"
                    and item.get("protocolVersion") in {"1.0", "1.0.0"}
                ),
                None,
            )
            if interface is None:
                raise ValueError("Agent card does not offer supported A2A 1.0 JSON-RPC")
            endpoint = interface.get("url")
        else:
            if (
                card.get("protocolVersion") not in {"0.3", "0.3.0"}
                or card.get("preferredTransport", "JSONRPC") != "JSONRPC"
            ):
                raise ValueError("Agent card does not offer supported A2A 0.3 JSON-RPC")
            endpoint = card.get("url")
        target = urlparse(str(endpoint))
        if (
            (target.scheme, target.netloc) != (parsed.scheme, parsed.netloc)
            or target.username
            or target.password
            or target.query
            or target.fragment
        ):
            raise ValueError(
                "Agent card endpoint must use its configured origin without credentials"
            )
        for field in ("defaultInputModes", "defaultOutputModes"):
            modes = card.get(field)
            if modes is not None and (
                not isinstance(modes, list)
                or len(modes) > 50
                or any(not isinstance(mode, str) or len(mode) > 150 for mode in modes)
            ):
                raise ValueError("Remote agent card has invalid media modes")
        return {
            "id": identity,
            "name": str(card.get("name") or record["name"])[:200],
            "description": str(card.get("description") or "")[:4000],
            "skills": card.get("skills", [])[:100],
            "endpoint": endpoint,
            "protocol_version": data["protocol_version"],
            "transport": "JSONRPC",
            "streaming": card.get("capabilities", {}).get("streaming") is True,
            "push_notifications": card.get("capabilities", {}).get("pushNotifications") is True,
            "input_modes": card.get("defaultInputModes"),
            "output_modes": card.get("defaultOutputModes"),
        }

    def stream_rpc(self, profile, card, actor, method, params):
        """Read bounded JSON-RPC SSE events; never repeat a SendMessage."""
        request_id = uuid.uuid4().hex
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        data = profile["data"]
        if (
            urlparse(card["endpoint"]).netloc != urlparse(data["url"]).netloc
            or urlparse(card["endpoint"]).scheme != urlparse(data["url"]).scheme
        ):
            raise ValueError("Remote stream must use its configured origin")
        headers = {**self.headers(data, actor), "Accept": "text/event-stream"}
        if self.stream_client:
            yield from self.stream_client(card["endpoint"], body, headers)
            return
        if not data["allow_private"]:
            parsed = urlparse(card["endpoint"])
            if any(
                not ipaddress.ip_address(item[4][0]).is_global
                for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
            ):
                raise ValueError("Remote stream resolves to a private network")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("Remote streaming redirects are disabled")

        request = urllib.request.Request(
            card["endpoint"], data=json.dumps(body).encode(), headers=headers
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(
            request, timeout=30
        ) as response:
            if response.headers.get_content_type() != "text/event-stream":
                raise ValueError("Remote agent did not return an event stream")
            size, event = 0, []
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                raw = response.readline(RESPONSE_LIMIT + 1)
                if not raw:
                    break
                size += len(raw)
                if len(raw) > RESPONSE_LIMIT or size > 64 * 1024 * 1024:
                    raise ValueError("Remote stream exceeds response limits")
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    event.append(line[5:].lstrip(" "))
                elif not line and event:
                    envelope = json.loads("\n".join(event))
                    event = []
                    if (
                        envelope.get("jsonrpc") != "2.0"
                        or envelope.get("id") != request_id
                        or "error" in envelope
                        or not isinstance(envelope.get("result"), dict)
                    ):
                        raise ValueError("Invalid remote stream response")
                    yield envelope["result"]

    def collect_stream(self, profile, card, actor, method, params, record):
        version = profile["data"]["protocol_version"]
        task = None
        try:
            for event in self.stream_rpc(profile, card, actor, method, params):
                if self.studio.team:
                    self.studio.team.assert_execution(actor)
                if self.studio.closing:
                    raise RuntimeError("Studio is stopping")
                if version == "1.0" and event.get("message"):
                    return event
                if version == "0.3" and event.get("kind") == "message":
                    return event
                incoming = (
                    event.get("task")
                    if version == "1.0"
                    else event
                    if event.get("kind") == "task"
                    else None
                )
                if incoming:
                    task = incoming
                elif task:
                    update = (
                        event.get("statusUpdate")
                        if version == "1.0"
                        else event
                        if event.get("kind") == "status-update"
                        else None
                    )
                    artifact = (
                        event.get("artifactUpdate")
                        if version == "1.0"
                        else event
                        if event.get("kind") == "artifact-update"
                        else None
                    )
                    for item in (update, artifact):
                        if item and item.get("taskId") != task["id"]:
                            raise ValueError("Stream update belongs to another task")
                    if update:
                        task["status"] = update["status"]
                    if artifact:
                        value = artifact["artifact"]
                        artifacts = task.setdefault("artifacts", [])
                        old = next(
                            (
                                item
                                for item in artifacts
                                if item.get("artifactId") == value.get("artifactId")
                            ),
                            None,
                        )
                        if old and artifact.get("append"):
                            old.setdefault("parts", []).extend(value.get("parts", []))
                        elif old:
                            old.update(value)
                        else:
                            artifacts.append(value)
                if not task:
                    raise ValueError("Stream must begin with a task or message")
                if len(json.dumps(task).encode()) > RESPONSE_LIMIT:
                    raise ValueError("Accumulated remote task exceeds response limits")
                task_id = _text(task.get("id"), "Remote task ID", 200)
                if record.get("task_id") and record["task_id"] != task_id:
                    raise ValueError("Remote stream changed task identity")
                if (
                    record.get("context_id")
                    and "contextId" in task
                    and task["contextId"] != record["context_id"]
                ):
                    raise ValueError("Remote stream changed task context")
                record.update(
                    task_id=task_id,
                    context_id=task.get("contextId", record.get("context_id")),
                    status="streaming",
                )
                self.studio._write(self.studio._path("external-runs", record["id"]), record)
                output = "\n\n".join(self.text(item) for item in task.get("artifacts", []))
                if len(output.encode()) > 262144:
                    raise ValueError("Remote stream output exceeds 256 KiB")
                self.studio._update_run(record["id"], remote_task_id=task_id, output=output)
                state = (
                    str(task.get("status", {}).get("state", ""))
                    .lower()
                    .removeprefix("task_state_")
                    .replace("_", "-")
                )
                if state in {
                    "completed",
                    "failed",
                    "canceled",
                    "rejected",
                    "input-required",
                    "auth-required",
                }:
                    return {"task": task} if version == "1.0" else task
        except Exception:
            if not record.get("task_id"):
                raise ValueError(
                    "Remote send was not confirmed. Review its remote state before retrying"
                ) from None
        # Once a task ID exists, recover with a read; never resend the original message.
        if not record.get("task_id"):
            raise ValueError("Remote stream ended without a task ID or response")
        result = self.rpc(
            profile,
            card,
            actor,
            "GetTask" if version == "1.0" else "tasks/get",
            {"id": record["task_id"], "historyLength": 10},
        )
        return {"task": result} if version == "1.0" else result

    def rpc(self, profile, card, actor, method, params):
        origin = urlparse(profile["data"]["url"])
        target = urlparse(card["endpoint"])
        if (origin.scheme, origin.netloc) != (target.scheme, target.netloc):
            raise ValueError("Configured remote-agent origin changed; inspect it before continuing")
        request_id = uuid.uuid4().hex
        response = self.request(
            card["endpoint"],
            profile["data"],
            actor,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )
        if (
            response.get("id") != request_id
            or response.get("jsonrpc") != "2.0"
            or "error" in response
            or not isinstance(response.get("result"), dict)
        ):
            raise ValueError("Remote agent returned an invalid or unsuccessful JSON-RPC response")
        return response["result"]

    @staticmethod
    def text(message):
        return "\n".join(
            str(part["text"])
            for part in (message or {}).get("parts", [])
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )

    async def execute(self, run_id, payload):
        actor = payload.get("requester_nexus_id")
        profile = self.profile(payload["external_agent_id"])
        if payload.get("external_binding") and payload["external_binding"] != binding(profile):
            raise ValueError("External connection changed while queued; review before sending")
        card = await asyncio.to_thread(self.discover, profile["id"], actor)
        version = profile["data"]["protocol_version"]
        record = {
            "id": run_id,
            "owner_id": actor,
            "agent_id": profile["id"],
            "card": card,
            "status": "preparing",
            "message_id": uuid.uuid4().hex,
            "profile_binding": binding(profile),
        }
        source = None
        if payload.get("external_resume_id"):
            source = self.studio.workbench.visible(
                self.studio._read(
                    self.studio._path("external-runs", payload["external_resume_id"])
                ),
                self.studio.workbench.actor(payload),
                own=True,
            )
            if (
                source.get("agent_id") != profile["id"]
                or source.get("status") != "continuation-queued"
            ):
                raise ValueError("Remote continuation changed or belongs to another agent")
            self.assert_binding(profile, source)
            record.update(
                task_id=source["task_id"],
                context_id=source.get("context_id"),
                source_id=source["id"],
            )
        self.studio._write(self.studio._path("external-runs", run_id), record)
        try:
            message = {
                "role": "ROLE_USER" if version == "1.0" else "user",
                "messageId": record["message_id"],
                "parts": self.content.parts(payload, profile, card, actor),
            }
            if source:
                message["taskId"] = source["task_id"]
                if source.get("context_id"):
                    message["contextId"] = source["context_id"]
            if version == "0.3":
                message["kind"] = "message"
            streaming = profile["data"].get("streaming", False)
            if streaming and not card["streaming"]:
                raise ValueError(
                    "This agent does not advertise streaming; disable it in connection settings"
                )
            configuration = {"returnImmediately": True} if version == "1.0" else {"blocking": False}
            if payload.get("external_callbacks"):
                if not card["push_notifications"]:
                    raise ValueError("This agent does not advertise callback notifications")
                config = self.callbacks.create(record, payload["_callback_origin"])
                configuration[
                    "taskPushNotificationConfig" if version == "1.0" else "pushNotificationConfig"
                ] = config
            record["status"] = "sending"
            self.studio._write(self.studio._path("external-runs", run_id), record)
            result = await asyncio.to_thread(
                self.collect_stream if streaming else self.rpc,
                profile,
                card,
                actor,
                ("SendStreamingMessage" if version == "1.0" else "message/stream")
                if streaming
                else ("SendMessage" if version == "1.0" else "message/send"),
                {
                    "message": message,
                    "configuration": configuration,
                },
                *([record] if streaming else []),
            )
            deadline = time.monotonic() + 300
            while True:
                if self.studio.team:
                    self.studio.team.assert_execution(actor)
                task = (
                    result.get("task")
                    if version == "1.0"
                    else result
                    if result.get("kind") == "task"
                    else None
                )
                if task is None:
                    message = result.get("message") if version == "1.0" else result
                    output = self.publish_result(record, [message], profile, actor)
                    record.update(status="completed", output=output)
                    return output
                state = (
                    str(task.get("status", {}).get("state") or "")
                    .lower()
                    .removeprefix("task_state_")
                    .replace("_", "-")
                )
                task_id = _text(task.get("id"), "Remote task ID", 200)
                if record.get("task_id") and record["task_id"] != task_id:
                    raise ValueError("Remote response changed task identity")
                if (
                    record.get("context_id")
                    and "contextId" in task
                    and task["contextId"] != record["context_id"]
                ):
                    raise ValueError("Remote response changed task context")
                if state not in {
                    "submitted",
                    "working",
                    "completed",
                    "input-required",
                    "auth-required",
                    "failed",
                    "canceled",
                    "cancelled",
                    "rejected",
                }:
                    raise ValueError("Remote service returned an unknown task state")
                record.update(
                    task_id=task_id,
                    status=state,
                    context_id=task.get("contextId", record.get("context_id")),
                )
                self.studio._write(self.studio._path("external-runs", run_id), record)
                self.studio._update_run(run_id, remote_task_id=task_id, remote_status=state)
                if state == "completed":
                    objects = task.get("artifacts", [])
                    if not objects:
                        objects = [
                            item
                            for item in [
                                task.get("status", {}).get("message"),
                                *task.get("history", []),
                            ]
                            if item and item.get("role") in {"agent", "ROLE_AGENT"}
                        ]
                    return self.publish_result(record, objects, profile, actor)
                if state in {"input-required", "auth-required"}:
                    raise StudioPausedError("Remote agent requires input or account authorization")
                if state in {"failed", "canceled", "cancelled", "rejected"}:
                    raise ValueError("Remote agent stopped without completing the task")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Remote task polling timed out; inspect its saved task ID")
                await asyncio.sleep(1)
                result = await asyncio.to_thread(
                    self.rpc,
                    profile,
                    card,
                    actor,
                    "GetTask" if version == "1.0" else "tasks/get",
                    {"id": task_id, "historyLength": 10},
                )
                if version == "1.0":
                    result = {"task": result}
        except StudioPausedError:
            raise
        except BaseException:
            if record["status"] == "preparing":
                record["status"] = "rejected"
            elif record["status"] not in {"failed", "canceled", "cancelled", "rejected"}:
                record["status"] = "uncertain"
            raise
        finally:
            self.studio._write(self.studio._path("external-runs", run_id), record)
            if source:
                original = {**record, "id": source["id"], "last_run_id": run_id}
                self.studio._write(self.studio._path("external-runs", source["id"]), original)
                if (
                    record["status"] == "completed"
                    and self.studio.run(source["id"])["kind"] == "external"
                ):
                    self.studio._update_run(
                        source["id"],
                        status="completed",
                        output=record.get("output", "Remote continuation completed"),
                        content_version=record.get("content_version", 1),
                        structured_content=record.get("structured_content", []),
                    )

    def continue_task(self, identity, payload, actor):
        text = _text(payload.get("task"), "Additional task input", 16000)
        with self.studio.lock:
            path = self.studio._path("external-runs", identity)
            record = self.studio.workbench.visible(self.studio._read(path), actor, own=True)
            self.assert_binding(self.profile(record["agent_id"]), record)
            if record["status"] not in {"input-required", "auth-required"} or not record.get(
                "task_id"
            ):
                raise ValueError("This remote task is not waiting for additional input")
            previous = record["status"]
            record["status"] = "continuation-queued"
            self.studio._write(path, record)
            try:
                return self.studio.start_run(
                    {
                        "kind": "external",
                        "external_agent_id": record["agent_id"],
                        "external_resume_id": identity,
                        "requester_nexus_id": self.studio.workbench.owner(actor),
                        "task": text,
                        **{
                            key: payload[key]
                            for key in ("external_input", "artifact_ids", "artifact_refs")
                            if key in payload
                        },
                    }
                )
            except BaseException:
                record["status"] = previous
                self.studio._write(path, record)
                raise

    def refresh_task(self, identity, actor, *, callback=None):
        """Recover a known task after interruption without sending another message."""
        with self.studio.lock:
            path = self.studio._path("external-runs", identity)
            record = self.studio.workbench.visible(self.studio._read(path), actor, own=True)
            before = dict(record)
            if self.studio.run(identity)["status"] in {"running", "queued"}:
                raise ValueError("This task is already being monitored")
            if not record.get("task_id"):
                raise ValueError(
                    "No acknowledged remote task ID. Inspect the remote service before retrying"
                )
        profile = self.profile(record["agent_id"])
        self.assert_binding(profile, record)
        version = profile["data"]["protocol_version"]
        task = self.rpc(
            profile,
            record["card"],
            self.studio.workbench.owner(actor),
            "GetTask" if version == "1.0" else "tasks/get",
            {"id": record["task_id"], "historyLength": 10},
        )
        if task.get("id") != record["task_id"]:
            raise ValueError("Remote task identity changed")
        if (
            record.get("context_id")
            and "contextId" in task
            and task["contextId"] != record["context_id"]
        ):
            raise ValueError("Remote task context changed")
        status = (
            str(task.get("status", {}).get("state", ""))
            .lower()
            .removeprefix("task_state_")
            .replace("_", "-")
        )
        if status not in {
            "submitted",
            "working",
            "completed",
            "input-required",
            "auth-required",
            "failed",
            "canceled",
            "cancelled",
            "rejected",
        }:
            raise ValueError("Remote service returned an unknown task state")
        output, planned = self.content.plan(
            task.get("artifacts", []), profile, self.studio.workbench.owner(actor)
        )
        with self.studio.lock:
            if callback:
                grant = self.studio._read(self.callbacks.path(callback["id"]))
                self.callbacks.check(grant, time.time())
                run = self.studio.run(identity)
                if (
                    grant.get("flight") != callback.get("flight")
                    or run.get("cancel_requested")
                    or run["status"] in {"running", "queued", "cancelled"}
                ):
                    raise ValueError("Callback recovery no longer owns this task read")
            if self.studio._read(path) != before:
                raise ValueError("Remote review state changed. Refresh it again")
            self.assert_binding(self.profile(profile["id"]), record)
            parts = self.content.publish(identity, planned, self.studio.workbench.owner(actor))
            record.update(content_version=1, structured_content=parts)
            record.update(status=status, output=output)
            self.studio._write(path, record)
            if status == "completed":
                self.studio._update_run(
                    identity,
                    status="completed",
                    output=output or "Remote task completed without text artifacts",
                    error=None,
                    content_version=1,
                    structured_content=parts,
                )
            elif status in {"input-required", "auth-required"}:
                self.studio._update_run(
                    identity, status="waiting", remote_status=status, error=None
                )
            return record

    def cancel(self, run_id, actor):
        record = self.studio.workbench.visible(
            self.studio._read(self.studio._path("external-runs", run_id)), actor, own=True
        )
        if not record.get("task_id") or record["status"] in {
            "completed",
            "failed",
            "canceled",
            "cancelled",
            "rejected",
        }:
            raise ValueError("This remote task cannot be canceled")
        profile = self.profile(record["agent_id"])
        self.assert_binding(profile, record)
        method = "CancelTask" if profile["data"]["protocol_version"] == "1.0" else "tasks/cancel"
        outcome = self.rpc(
            profile,
            record["card"],
            self.studio.workbench.owner(actor),
            method,
            {"id": record["task_id"]},
        )
        record["status"] = (
            str(outcome.get("status", {}).get("state", "cancel-requested"))
            .lower()
            .removeprefix("task_state_")
            .replace("_", "-")
        )
        self.studio._write(self.studio._path("external-runs", run_id), record)
        if record["status"] in {"canceled", "cancelled"}:
            self.studio._update_run(
                run_id, status="cancelled", error="Remote task canceled by its owner"
            )
        return record
