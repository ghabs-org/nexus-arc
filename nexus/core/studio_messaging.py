"""Verified channel events, mapped user execution and single-use approval buttons.

Incoming webhooks acknowledge after durable receipt. The scheduler drains events;
it never accepts a Nexus identity or a notification destination from an event.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import parse_qs

CHANNELS = {"telegram", "slack", "discord", "whatsapp", "openclaw", "teams"}


def validate_channel(data):
    kind = data.get("type", "webhook")
    if kind not in CHANNELS:
        raise ValueError("Choose a supported incoming channel")
    mapping = data.get("identity_map", {})
    channels = data.get("allowed_channels", [])
    if (
        not isinstance(mapping, dict)
        or not mapping
        or len(mapping) > 1000
        or any(
            not isinstance(k, str)
            or not k
            or len(k) > 200
            or not isinstance(v, str)
            or not v
            or len(v) > 96
            for k, v in mapping.items()
        )
    ):
        raise ValueError("Map platform user IDs to Nexus identities (desktop in local mode)")
    if (
        not isinstance(channels, list)
        or not channels
        or any(not isinstance(c, str) or not c or len(c) > 200 for c in channels)
    ):
        raise ValueError("Choose the allowed incoming channel IDs")
    result = {"type": kind, "identity_map": mapping, "allowed_channels": channels}
    if "operator_targets" in data:
        targets = data["operator_targets"]
        if (
            kind != "openclaw" or not isinstance(targets, list) or len(targets) > 100
            or any(not isinstance(target, str) or not re.fullmatch(
                r"(?:agents|workflows):[A-Za-z0-9][A-Za-z0-9_-]{0,95}", target
            ) for target in targets)
        ):
            raise ValueError("OpenClaw operator_targets must list up to 100 agent/workflow references")
        result["operator_targets"] = list(dict.fromkeys(targets))
    for name in ("auto_reply", "approval_notifications"):
        result[name] = data.get(name, False)
        if type(result[name]) is not bool:
            raise ValueError(f"{name} must be true or false")
    connector = data.get("notification_connector_id", "")
    if not isinstance(connector, str) or len(connector) > 96:
        raise ValueError("Choose a saved messaging connection")
    if (result["auto_reply"] or result["approval_notifications"]) and not connector:
        raise ValueError("Notifications require a saved, fixed-destination connection")
    result["notification_connector_id"] = connector
    if kind == "teams":
        from nexus.core.studio_teams_events import configuration

        result.update(configuration(data))
    return result


class StudioMessaging:
    def __init__(self, studio):
        self.studio = studio
        from nexus.core.studio_teams_events import TeamsEvents

        self.teams = TeamsEvents(studio)

    def verify(self, data, headers, raw):
        headers = {key.lower(): value for key, value in headers.items()}
        secret = os.getenv(data["token_env"], "")
        kind = data["type"]
        if not secret:
            raise PermissionError("Incoming channel verification is not configured")
        if kind == "telegram":
            valid = len(secret) >= 24 and secrets.compare_digest(
                headers.get("x-telegram-bot-api-secret-token", ""), secret
            )
        elif kind == "whatsapp":
            digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            valid = secrets.compare_digest(
                headers.get("x-hub-signature-256", ""), "sha256=" + digest
            )
        else:
            timestamp = headers.get(
                "x-slack-request-timestamp"
                if kind == "slack"
                else "x-signature-timestamp"
                if kind == "discord"
                else "x-nexus-timestamp",
                "",
            )
            try:
                if abs(time.time() - int(timestamp)) > 300:
                    raise ValueError
            except ValueError:
                raise PermissionError("Channel event timestamp is stale or invalid") from None
            if kind == "discord":
                try:
                    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

                    Ed25519PublicKey.from_public_bytes(bytes.fromhex(secret)).verify(
                        bytes.fromhex(headers.get("x-signature-ed25519", "")),
                        timestamp.encode() + raw,
                    )
                    valid = True
                except ImportError:
                    raise ValueError(
                        "Install cryptography for Discord interaction verification"
                    ) from None
                except Exception:
                    valid = False
            else:
                prefix = (
                    b"v0:" + timestamp.encode() + b":"
                    if kind == "slack"
                    else timestamp.encode() + b"."
                )
                digest = hmac.new(secret.encode(), prefix + raw, hashlib.sha256).hexdigest()
                valid = secrets.compare_digest(
                    headers.get(
                        "x-slack-signature" if kind == "slack" else "x-nexus-signature", ""
                    ),
                    ("v0=" if kind == "slack" else "sha256=") + digest,
                )
        if not valid:
            raise PermissionError("Channel signature verification failed")

    def receive(self, identity, headers, raw):
        if not 0 < len(raw) <= 1024 * 1024:
            raise ValueError("Incoming event exceeds one MiB")
        record = self.studio.catalog.get("entrypoints", identity)
        data = record["data"]
        if not data["enabled"] or data.get("type") not in CHANNELS:
            raise PermissionError("Incoming channel is disabled")
        if data["type"] == "teams":
            return self.teams.receive(self.teams.profile(identity), raw)
        self.verify(data, headers, raw)
        content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        try:
            body = (
                json.loads(parse_qs(raw.decode())["payload"][0])
                if content_type.startswith("application/x-www-form-urlencoded")
                else json.loads(raw)
            )
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, KeyError, UnicodeError):
            raise ValueError("Malformed channel event") from None
        kind = data["type"]
        if kind == "slack" and body.get("type") == "url_verification":
            return {"challenge": str(body.get("challenge", ""))[:500]}
        if kind == "discord" and body.get("type") == 1:
            return {"type": 1}
        event = self.normalize(kind, body)
        if event is None:
            return {"ok": True}
        sender, channel, task, callback, event_id = event
        if not event_id or len(event_id) > 300:
            raise ValueError("Channel event needs a stable platform event ID")
        owner = data["identity_map"].get(sender)
        if not owner or channel not in data["allowed_channels"]:
            raise PermissionError("Channel or sender is not mapped to this workspace")
        actor = owner if self.studio.team else None
        if self.studio.team:
            self.studio.team.assert_execution(actor)
        elif owner != "desktop":
            raise PermissionError("Local channel users must map to desktop")
        digest = hashlib.sha256(f"{identity}:{event_id}".encode()).hexdigest()
        with self.studio.lock:
            path = self.studio._path("channel-events", digest)
            if not path.exists():
                self.studio._write(
                    path,
                    {
                        "id": digest,
                        "entrypoint_id": identity,
                        "sender": sender,
                        "channel": channel,
                        "task": task,
                        "callback": callback,
                        "requester_nexus_id": actor,
                        "status": "received",
                        "created": time.time(),
                    },
                )
        return {"type": 6 if callback else 5} if kind == "discord" else {"ok": True}

    @staticmethod
    def normalize(kind, body):
        if kind == "telegram":
            callback = body.get("callback_query")
            msg = callback.get("message", {}) if callback else body.get("message", {})
            sender = (callback or msg).get("from", {})
            if sender.get("is_bot") or not msg:
                return None
            return (
                str(sender.get("id", "")),
                str(msg.get("chat", {}).get("id", "")),
                str(msg.get("text", "")),
                str(callback.get("data", "")) if callback else "",
                str(body.get("update_id", "")),
            )
        if kind == "slack":
            if body.get("type") == "block_actions":
                actions = body.get("actions", [])
                return (
                    str(body.get("user", {}).get("id", "")),
                    str(body.get("channel", {}).get("id", "")),
                    "",
                    str(actions[0].get("value", "")) if actions else "",
                    str(body.get("trigger_id", "")),
                )
            event = body.get("event", {})
            if (
                event.get("type") not in {"message", "app_mention"}
                or event.get("bot_id")
                or event.get("subtype")
            ):
                return None
            return (
                str(event.get("user", "")),
                str(event.get("channel", "")),
                str(event.get("text", "")),
                "",
                str(body.get("event_id", "")),
            )
        if kind == "discord":
            if body.get("type") not in {2, 3}:
                return None
            sender = body.get("member", {}).get("user") or body.get("user", {})
            data = body.get("data", {})
            text = " ".join(str(option.get("value", "")) for option in data.get("options", []))
            return (
                str(sender.get("id", "")),
                str(body.get("channel_id", "")),
                text,
                str(data.get("custom_id", "")),
                str(body.get("id", "")),
            )
        if kind == "whatsapp":
            entries = body.get("entry", [])
            changes = (
                entries[0].get("changes", [])
                if entries and isinstance(entries[0], dict)
                else []
            )
            value = (
                changes[0].get("value", {})
                if changes and isinstance(changes[0], dict)
                else {}
            )
            messages = value.get("messages", [])
            if not messages or not isinstance(messages[0], dict):
                return None
            msg = messages[0]
            interactive = msg.get("interactive", {})
            reply = (
                interactive.get("button_reply")
                or interactive.get("list_reply")
                or {}
            )
            return (
                str(msg.get("from", "")),
                str(value.get("metadata", {}).get("phone_number_id", "")),
                str(msg.get("text", {}).get("body", "")),
                str(reply.get("id", "")),
                str(msg.get("id", "")),
            )
        return (
            str(body.get("sender_id", "")),
            str(body.get("channel", "")),
            str(body.get("text", "")),
            str(body.get("callback_data", "")),
            str(body.get("id", "")),
        )

    def approval(self, token, event):
        if not token.startswith("nexus:") or len(token) != 38:
            raise PermissionError("Unknown approval button")
        with self.studio.lock:
            path = self.studio._path("channel-actions", token[6:])
            action = self.studio._read(path)
            if (
                action["used"]
                or action["expires"] < time.time()
                or any(
                    action[key] != event[key]
                    for key in ("entrypoint_id", "requester_nexus_id", "sender", "channel")
                )
            ):
                raise PermissionError("Approval button expired, used or belongs to another user")
            run = self.studio.run(action["run_id"])
            if run["status"] != "waiting" or action["gates"] != run.get("awaiting_approval"):
                raise ValueError("Approval step has changed; use its latest buttons")
            action["used"] = True
            self.studio._write(path, action)
            self.studio.graph.resume(action["run_id"], action["decision"])

    def tick(self):
        for event in self.studio.records("channel-events", limit=None):
            notification = None
            with self.studio.lock:
                event = self.studio._read(self.studio._path("channel-events", event["id"]))
                record = self.studio.catalog.get("entrypoints", event["entrypoint_id"])
                data = record["data"]
                if not data["enabled"]:
                    continue
                try:
                    if event["status"] == "teams_pending":
                        self.teams.resolve(event, record)
                    if event["status"] == "received":
                        if self.studio.team:
                            self.studio.team.assert_execution(event["requester_nexus_id"])
                        # Recheck configuration after receipt; a mapping change revokes pending work.
                        if (
                            data["identity_map"].get(event["sender"])
                            != (event["requester_nexus_id"] or "desktop")
                            or event["channel"] not in data["allowed_channels"]
                        ):
                            raise PermissionError("Channel mapping changed")
                        if event["callback"]:
                            self.approval(event["callback"], event)
                            event["status"] = "completed"
                        else:
                            if not event["task"].strip() or len(event["task"]) > 16000:
                                raise ValueError(
                                    "Channel text must be nonempty, up to 16000 characters"
                                )
                            # Claim before enqueue: an ambiguous crash is reviewed, never replayed.
                            event["status"] = "launching"
                            self.studio._write(
                                self.studio._path("channel-events", event["id"]), event
                            )
                            run = self.studio.operations.launch_target(
                                data, event["task"], event["requester_nexus_id"]
                            )
                            event.update(status="running", run_id=run["id"])
                    if event["status"] == "running":
                        run = self.studio.run(event["run_id"])
                        if run["status"] in {
                            "waiting",
                            "completed",
                            "failed",
                            "cancelled",
                            "interrupted",
                        }:
                            marker = hashlib.sha256(
                                json.dumps(
                                    [run["status"], run.get("awaiting_approval")], sort_keys=True
                                ).encode()
                            ).hexdigest()
                            if marker != event.get("notified"):
                                event["notified"] = marker
                                # Persist the send claim before network I/O. Failed or ambiguous sends
                                # are never posted again by an automatic tick.
                                self.studio._write(
                                    self.studio._path("channel-events", event["id"]), event
                                )
                                notification = (dict(event), data, run)
                            if run["status"] != "waiting":
                                event["status"] = "completed"
                except Exception:
                    event.update(
                        status="failed",
                        error="Channel processing failed; inspect the run and account setup before retrying",
                    )
                self.studio._write(self.studio._path("channel-events", event["id"]), event)
            if notification:
                try:
                    self.notify(*notification)
                except Exception:
                    with self.studio.lock:
                        latest = self.studio._read(self.studio._path("channel-events", event["id"]))
                        latest["notification_error"] = (
                            "Notification outcome uncertain; inspect the remote channel before sending again"
                        )
                        self.studio._write(self.studio._path("channel-events", event["id"]), latest)

    def notify(self, event, data, run):
        operator = event.get("openclaw_operator", False)
        waiting = run["status"] == "waiting" and (
            run.get("awaiting_approval") or operator and run.get("awaiting_input")
        )
        if not data["approval_notifications" if waiting else "auto_reply"]:
            return
        if operator:
            current = self.studio.catalog.get("entrypoints", event["entrypoint_id"])["data"]
            if (current != data or not current["enabled"]
                    or current["identity_map"].get(event["sender"]) != (event["requester_nexus_id"] or "desktop")
                    or event["channel"] not in current["allowed_channels"]):
                raise PermissionError("OpenClaw notification mapping changed")
            self.studio.openclaw.bound_run(run["id"], {
                key: event[key] for key in ("entrypoint_id", "sender", "channel", "requester_nexus_id")
            }, current)
        connection = self.studio.catalog.get("connectors", data["notification_connector_id"])[
            "data"
        ]
        if connection["type"] != data["type"] or connection.get("channel") != event["channel"]:
            raise PermissionError(
                "Notification connection must match the verified incoming channel"
            )
        buttons = []
        if waiting and not operator:
            for decision in ("approve", "reject"):
                token = secrets.token_hex(16)
                self.studio._write(
                    self.studio._path("channel-actions", token),
                    {
                        **{
                            key: event[key]
                            for key in ("entrypoint_id", "requester_nexus_id", "sender", "channel")
                        },
                        "run_id": run["id"],
                        "gates": run["awaiting_approval"],
                        "decision": decision,
                        "used": False,
                        "expires": time.time() + 86400,
                    },
                )
                buttons.append({"label": decision.title(), "callback_data": "nexus:" + token})
        text = (
            ("Approval requested: " + ", ".join(gate["name"] for gate in run["awaiting_approval"]))
            if waiting and not operator
            else (run.get("output") or f"Run {run['status']}")
        )
        if operator:
            text = f"Studio run {run['id']}: {run['status']}\n" + str(run.get("output") or "")[:12000]
            text += f"\nInspect results or pending gates: /nexus studio status {run['id']}"
        asyncio.run(
            self.studio.connections.execute(
                data["notification_connector_id"],
                {
                    "action": "send",
                    "arguments": {"text": text[:16000], "buttons": buttons},
                },
                event["requester_nexus_id"],
                approved=True,
            )
        )
