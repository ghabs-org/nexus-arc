"""Scoped OpenClaw operator access over existing Studio execution services.

The channel signature proves the gateway, while saved mappings resolve users.
Receipts claim mutations before dispatch so an ambiguous crash cannot replay work.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time

from nexus.core.studio_catalog import _text

OPERATIONS = {
    "discover",
    "runs",
    "schedules",
    "submit",
    "status",
    "artifacts",
    "artifact",
    "reply",
    "cancel",
    "receipt",
}
MUTATIONS = {"submit", "reply", "cancel"}
FIELDS = {
    "discover": set(),
    "runs": set(),
    "schedules": set(),
    "submit": {"target", "task"},
    "status": {"run_id"},
    "artifacts": {"run_id"},
    "artifact": {"run_id", "artifact_id", "revision"},
    "reply": {"token", "decision", "values"},
    "cancel": {"run_id"},
    "receipt": {"request_id"},
}


class OpenClawConflictError(ValueError):
    """A request was changed or a previous effect needs manual reconciliation."""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class StudioOpenClaw:
    def __init__(self, studio):
        self.studio = studio

    def receive(self, identity, headers, raw):
        if not 0 < len(raw) <= 1024 * 1024:
            raise ValueError("OpenClaw request exceeds one MiB")
        with self.studio.lock:
            data = self.studio.catalog.get("entrypoints", identity)["data"]
            if (
                data.get("type") != "openclaw"
                or not data.get("enabled")
                or not data.get("operator_targets")
                or len(os.getenv(data["token_env"], "")) < 32
            ):
                raise PermissionError("OpenClaw operator access is not configured")
            self.studio.resource_lifecycle.assert_launch("entrypoints:" + identity)
            self.studio.messaging.verify(data, headers, raw)
            body = json.loads(raw)
            if (
                not isinstance(body, dict)
                or set(body)
                != {
                    "version",
                    "entrypoint_id",
                    "id",
                    "sender_id",
                    "channel",
                    "session_id",
                    "operation",
                    "arguments",
                }
                or type(body.get("version")) is not int
                or body["version"] != 1
            ):
                raise ValueError("Expected a version 1 OpenClaw envelope")
            if body["entrypoint_id"] != identity:
                raise PermissionError("Signed entrypoint does not match the route")
            for field in ("id", "sender_id", "channel", "session_id"):
                _text(body[field], field, 200)
            owner = data["identity_map"].get(body["sender_id"])
            if not owner or body["channel"] not in data["allowed_channels"]:
                raise PermissionError("Channel or sender is not mapped to this workspace")
            actor = None
            if self.studio.team:
                self.studio.team.assert_execution(owner)
                actor = {"nexus_id": owner, "role": self.studio.team.bindings[owner]}
            elif owner != "desktop":
                raise PermissionError("Local channel users must map to desktop")
            scope = {
                "entrypoint_id": identity,
                "sender": body["sender_id"],
                "channel": body["channel"],
                "requester_nexus_id": owner if actor else None,
            }
            operation, args = body["operation"], body["arguments"]
            if not isinstance(operation, str) or operation not in OPERATIONS:
                raise ValueError("Unknown OpenClaw operation")
            if not isinstance(args, dict) or set(args) - FIELDS[operation]:
                raise ValueError("Unexpected OpenClaw arguments")
            if operation not in MUTATIONS:
                result = self.read(operation, args, scope, data, actor)
            else:
                result = self.mutate(body, scope, data, actor)
            return {"version": 1, **result}

    def bound_run(self, identity, scope, data):
        binding = self.studio._read(self.studio._path("openclaw-runs", identity))
        if binding["scope"] != scope or binding["target"] not in data["operator_targets"]:
            raise PermissionError("Run does not belong to this OpenClaw caller or target scope")
        run = self.studio.run(identity)
        if run.get("requester_nexus_id") != scope["requester_nexus_id"]:
            raise PermissionError("Run ownership changed")
        return run

    def view(self, run, scope):
        result = {
            key: run.get(key)
            for key in (
                "id",
                "kind",
                "status",
                "session_id",
                "created_at",
                "updated_at",
                "awaiting_approval",
                "awaiting_input",
                "cancel_requested",
            )
        }
        result["output"] = str(run.get("output") or "")[:16000]
        result["studio_path"] = "/#activity"
        result["run_path"] = "/api/runs/" + run["id"]
        gates = [run.get("awaiting_approval", []), run.get("awaiting_input", [])]
        if run["status"] == "waiting" and any(gates):
            key = digest([scope, run["id"], gates, run["updated_at"]])
            path = self.studio._path("openclaw-gates", key)
            gate = self.studio._read(path) if path.exists() else None
            if not gate or gate["expires"] <= time.time():
                gate = {
                    "id": key,
                    "scope": scope,
                    "run_id": run["id"],
                    "gates": gates,
                    "token": secrets.token_hex(24),
                    "expires": time.time() + 86400,
                    "used": False,
                    "run_updated_at": run["updated_at"],
                }
                self.studio._write(path, gate)
            if not gate["used"]:
                result["reply_token"] = key + "." + gate["token"]
        return result

    def read(self, operation, args, scope, data, actor):
        if operation == "receipt":
            identity = _text(args.get("request_id"), "Request ID", 200)
            receipt = self.studio._read(
                self.studio._path("openclaw-receipts", digest([scope, identity]))
            )
            if receipt["status"] != "accepted":
                raise OpenClawConflictError(
                    "Previous submission outcome is uncertain; inspect Studio activity before retrying"
                )
            run = self.bound_run(receipt["run_id"], scope, data)
            return {"receipt_id": identity, "run": self.view(run, scope)}
        if operation == "discover":
            items = []
            for kind in ("agents", "workflows"):
                for item in self.studio.definitions(kind):
                    target = kind + ":" + item["id"]
                    if target in data["operator_targets"]:
                        document = item.get("document", {})
                        items.append(
                            {
                                "target": target,
                                "name": document.get("name")
                                or document.get("metadata", {}).get("name")
                                or item["id"],
                            }
                        )
            return {"items": items, "operations": sorted(OPERATIONS)}
        if operation == "runs":
            runs = []
            for binding in self.studio.records("openclaw-runs", limit=None):
                if binding["scope"] == scope and binding["target"] in data["operator_targets"]:
                    runs.append(self.bound_run(binding["id"], scope, data))
            runs.sort(key=lambda run: run["created_at"], reverse=True)
            return {"items": [self.view(run, scope) for run in runs[:50]]}
        if operation == "schedules":
            items = []
            for record in self.studio.catalog.list("schedules"):
                settings = record["data"]
                target = (
                    "agents:" if settings["target_kind"] == "agent" else "workflows:"
                ) + settings["target_id"]
                if target not in data["operator_targets"]:
                    continue
                if actor and record.get("owner_id") != actor["nexus_id"]:
                    continue
                items.append(
                    {
                        "id": record["id"],
                        "name": record["name"],
                        "target": target,
                        "enabled": settings["enabled"],
                    }
                )
            return {"items": items}
        run = self.bound_run(args.get("run_id"), scope, data)
        if operation == "status":
            return {"run": self.view(run, scope)}
        artifacts = self.artifacts(run, actor)
        if operation == "artifacts":
            return {"items": artifacts}
        if type(args.get("revision")) is not int:
            raise ValueError("Choose an exact artifact revision")
        item = next(
            (
                item
                for item in artifacts
                if item["id"] == args.get("artifact_id") and item["revision"] == args["revision"]
            ),
            None,
        )
        if not item:
            raise PermissionError("Artifact revision is not an output of this run")
        if item["size"] > 1024 * 1024:
            raise ValueError("Use the authenticated Studio download for artifacts above one MiB")
        record = self.studio.artifacts.revision(item["id"], item["revision"])
        content = self.studio.artifacts.get(record)
        if len(content) > 1024 * 1024:
            raise ValueError("Artifact exceeds one MiB")
        return {"artifact": {**item, "content_base64": base64.b64encode(content).decode()}}

    def artifacts(self, run, actor):
        refs = {
            (part["artifact_id"], part["revision"])
            for part in run.get("structured_content", [])
            if part.get("type") == "artifact"
        }
        if run.get("artifact_id") and run.get("artifact_revision"):
            refs.add((run["artifact_id"], run["artifact_revision"]))
        result = []
        for identity, revision in sorted(refs):
            current = self.studio.artifacts.revision(identity)
            record = self.studio.artifacts.revision(identity, revision)
            self.studio.collaboration.owned(current, actor)
            self.studio.collaboration.owned(record, actor)
            data = record["data"]
            result.append(
                {
                    "id": identity,
                    "revision": revision,
                    "name": data["filename"],
                    "size": data.get("size", 0),
                    "sha256": data.get("sha256"),
                    "download_path": f"/api/catalog/artifacts/{identity}/versions/{revision}",
                }
            )
        return result

    def mutate(self, body, scope, data, actor):
        key = digest([scope, body["id"]])
        path = self.studio._path("openclaw-receipts", key)
        fingerprint = digest(body)
        if path.exists():
            receipt = self.studio._read(path)
            if receipt["fingerprint"] != fingerprint:
                raise OpenClawConflictError("Request ID was already used with different arguments")
            if receipt["status"] != "accepted":
                raise OpenClawConflictError(
                    "Previous submission outcome is uncertain; inspect Studio activity before retrying"
                )
            run = self.bound_run(receipt["run_id"], scope, data)
            return {"receipt_id": body["id"], "duplicate": True, "run": self.view(run, scope)}
        operation, args = body["operation"], body["arguments"]
        gate = None
        session_key = digest([scope, body["session_id"]])
        session_path = self.studio._path("openclaw-sessions", session_key)
        context = {}
        if operation == "submit":
            target = args.get("target")
            if not isinstance(target, str) or target not in data["operator_targets"]:
                raise PermissionError("Target is not enabled for this OpenClaw entrypoint")
            kind, identity = target.split(":", 1)
            self.studio.definition(kind, identity)
            _text(args.get("task"), "Task", 16000)
            if kind == "agents" and session_path.exists():
                context["session_id"] = self.studio._read(session_path)["session_id"]
        elif operation == "reply":
            token = _text(args.get("token"), "Reply token", 113)
            parts = token.split(".")
            if len(parts) != 2:
                raise PermissionError("Invalid reply token")
            gate_path = self.studio._path("openclaw-gates", parts[0])
            gate = self.studio._read(gate_path)
            if (
                gate["scope"] != scope
                or gate["used"]
                or gate["expires"] <= time.time()
                or not secrets.compare_digest(parts[1], gate["token"])
            ):
                raise PermissionError("Reply token expired, used or belongs to another caller")
            run = self.bound_run(gate["run_id"], scope, data)
            if (
                run["status"] != "waiting"
                or gate["run_updated_at"] != run["updated_at"]
                or gate["gates"]
                != [run.get("awaiting_approval", []), run.get("awaiting_input", [])]
            ):
                raise OpenClawConflictError("Pending gates changed; inspect the current run")
            if "values" in args:
                if (
                    "decision" in args
                    or not isinstance(args["values"], dict)
                    or not args["values"]
                    or not run.get("awaiting_input")
                ):
                    raise ValueError("Supply values for the pending input IDs")
                from nexus.core.studio_workflow_v2 import validate_schema

                inputs = {item["id"]: item for item in run["awaiting_input"]}
                if set(args["values"]) - set(inputs):
                    raise ValueError("Unknown workflow input ID")
                for identity, value in args["values"].items():
                    validate_schema(inputs[identity]["schema"], value)
            elif args.get("decision") not in {"approve", "reject"} or not run.get(
                "awaiting_approval"
            ):
                raise ValueError("Choose approve or reject for the pending gate")
        else:
            run = self.bound_run(args.get("run_id"), scope, data)
            if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                raise ValueError("Task is already terminal")
        self.studio._write(path, {"id": key, "fingerprint": fingerprint, "status": "dispatching"})
        if operation == "submit":
            run = self.studio.operations.launch_target(
                {"target_kind": "agent" if kind == "agents" else "workflow", "target_id": identity},
                args["task"],
                scope["requester_nexus_id"],
                context,
            )
            self.studio._write(
                self.studio._path("openclaw-runs", run["id"]),
                {"id": run["id"], "scope": scope, "target": target},
            )
            if run.get("session_id"):
                self.studio._write(
                    session_path, {"id": session_key, "session_id": run["session_id"]}
                )
            self.studio._write(
                self.studio._path("channel-events", key),
                {
                    "id": key,
                    **scope,
                    "run_id": run["id"],
                    "status": "running",
                    "task": args["task"],
                    "callback": "",
                    "created": time.time(),
                    "openclaw_operator": True,
                },
            )
        elif operation == "reply":
            gate["used"] = True
            self.studio._write(gate_path, gate)
            run = (
                self.studio.graph.structured.resume_input(run["id"], args["values"])
                if "values" in args
                else self.studio.graph.resume(run["id"], args["decision"])
            )
        else:
            run = self.studio.cancel_run(run["id"], actor)
        self.studio._write(
            path, {"id": key, "fingerprint": fingerprint, "status": "accepted", "run_id": run["id"]}
        )
        return {"receipt_id": body["id"], "duplicate": False, "run": self.view(run, scope)}
