"""Basic Graph notifications: durable hints, authoritative reads, mapped execution."""

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from html.parser import HTMLParser

from nexus.core.studio_teams import call_teams, validate_teams


def configuration(data):
    selected = validate_teams(
        {
            "type": "teams",
            "team_id": data.get("team_id"),
            "channel_id": data.get("channel_id"),
            "token_env": data.get("graph_token_env"),
            "actions": ["query"],
            "enabled": True,
        }
    )
    if data.get("auto_reply") or data.get("approval_notifications"):
        raise ValueError("Teams incoming tasks use Studio for results and approvals")
    if data.get("allowed_channels") != [selected["channel_id"]]:
        raise ValueError("Teams incoming events require exactly the selected channel")
    prefix = data.get("command_prefix", "/nexus ")
    if not isinstance(prefix, str) or not 2 <= len(prefix) <= 64 or not prefix.strip():
        raise ValueError("Choose a nonempty Teams command prefix of 2–64 characters")
    subscription = data.get("subscription_id", "")
    if subscription != "" or data.get("enabled"):
        subscription = str(uuid.UUID(str(subscription)))
    return {
        "team_id": selected["team_id"],
        "channel_id": selected["channel_id"],
        "graph_token_env": selected["token_env"],
        "command_prefix": prefix,
        "tenant_id": str(uuid.UUID(str(data.get("tenant_id", "")))),
        "subscription_id": subscription,
    }


def binding(record):
    return hashlib.sha256(
        json.dumps(
            [record["revision"], record.get("owner_id"), record["data"]], sort_keys=True
        ).encode()
    ).hexdigest()


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"br", "p", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class TeamsEvents:
    def __init__(self, studio):
        self.studio = studio

    def profile(self, identity, *, validation=False):
        record = self.studio.catalog.get("entrypoints", identity)
        if record["data"].get("type") != "teams" or (
            not validation and not record["data"]["enabled"]
        ):
            raise PermissionError("Teams incoming service is disabled")
        if not validation:
            self.studio.resource_lifecycle.assert_launch("entrypoints:" + identity)
        if self.studio.team:
            self.studio.team.assert_execution(record.get("owner_id"))
        return record

    def challenge(self, identity, token):
        self.profile(identity, validation=True)
        if (
            not isinstance(token, str)
            or not 0 < len(token.encode()) <= 4096
            or any(ord(c) < 32 for c in token)
        ):
            raise ValueError("Invalid Graph validation token")
        return token.encode()

    def receive(self, record, raw):
        data = record["data"]
        secret = os.getenv(data["token_env"], "")
        if not 24 <= len(secret) <= 128:
            raise PermissionError("Configure a Teams subscription verification secret")
        body = json.loads(raw)
        notifications = body.get("value") if isinstance(body, dict) else None
        if not isinstance(notifications, list) or not 1 <= len(notifications) <= 100:
            raise ValueError("Expected 1–100 Graph notifications")
        prepared = []
        for notice in notifications:
            if (
                not isinstance(notice, dict)
                or not isinstance(notice.get("clientState"), str)
                or not secrets.compare_digest(notice["clientState"].encode(), secret.encode())
            ):
                raise PermissionError("Invalid Teams subscription credential")
            if (
                notice.get("subscriptionId") != data["subscription_id"]
                or notice.get("tenantId") != data["tenant_id"]
            ):
                raise PermissionError("Teams subscription or tenant does not match")
            if notice.get("changeType") not in {"created", "updated", "deleted"} or notice.get(
                "encryptedContent"
            ):
                raise ValueError("Use basic channel notifications without encrypted resource data")
            # Never use an incoming URL as a credential destination.
            resource = notice.get("resource", "")
            prefix = (
                "teams('" + data["team_id"] + "')/channels('" + data["channel_id"] + "')/messages('"
            )
            if not isinstance(resource, str) or not resource.startswith(prefix):
                raise PermissionError("Notification is outside the selected Teams channel")
            match = re.fullmatch(
                r"([A-Za-z0-9_:@.~-]{1,200})'\)(?:/replies\('([A-Za-z0-9_:@.~-]{1,200})'\))?",
                resource[len(prefix) :],
            )
            if not match:
                raise ValueError("Invalid Teams message resource")
            if notice["changeType"] != "created":
                continue  # Edits/deletes cannot restart or repeat a task.
            parent, reply = match.groups()
            key = hashlib.sha256(
                json.dumps(
                    [
                        record["id"],
                        data["tenant_id"],
                        data["team_id"],
                        data["channel_id"],
                        parent,
                        reply,
                    ]
                ).encode()
            ).hexdigest()
            prepared.append(
                {
                    "id": key,
                    "entrypoint_id": record["id"],
                    "status": "teams_pending",
                    "requester_nexus_id": record.get("owner_id"),
                    "teams_binding": binding(record),
                    "teams_message_id": parent,
                    "teams_reply_id": reply,
                    "created": time.time(),
                }
            )
        with self.studio.lock:
            current = self.profile(record["id"])
            if binding(current) != binding(record):
                raise PermissionError("Teams entrypoint changed before receipt")
            for event in prepared:
                path = self.studio._path("channel-events", event["id"])
                if not path.exists():
                    self.studio._write(path, event)
        return {"ok": True}

    def resolve(self, event, record):
        if binding(record) != event["teams_binding"]:
            raise PermissionError("Teams incoming configuration changed")
        self.profile(record["id"])
        data = record["data"]
        reader = {
            "type": "teams",
            "team_id": data["team_id"],
            "channel_id": data["channel_id"],
            "token_env": data["graph_token_env"],
            "actions": ["query"],
            "enabled": True,
        }
        arguments = {"message_id": event["teams_message_id"]}
        if event.get("teams_reply_id"):
            arguments["reply_id"] = event["teams_reply_id"]
        message = call_teams(
            self.studio,
            reader,
            record.get("owner_id"),
            "query",
            arguments,
            self.studio.connections.enterprise.request_client,
        )
        if (
            message.get("id") != (event.get("teams_reply_id") or event["teams_message_id"])
            or message.get("deletedDateTime")
            or message.get("messageType") != "message"
        ):
            raise ValueError("Teams message is unavailable or not a user message")
        sender = message.get("from", {}).get("user", {}).get("id")
        if not sender or message.get("from", {}).get("application"):
            event["status"] = "ignored"
            return
        body = message.get("body", {})
        content = body.get("content")
        if (
            not isinstance(content, str)
            or len(content.encode()) > 64000
            or body.get("contentType") not in {"text", "html"}
        ):
            raise ValueError("Unsupported Teams message body")
        if body["contentType"] == "html":
            parser = PlainText()
            parser.feed(content)
            content = "".join(parser.parts)
        content = content.strip()
        if not content.startswith(data["command_prefix"]):
            event["status"] = "ignored"
            return
        owner = data["identity_map"].get(sender)
        if not owner or (not self.studio.team and owner != "desktop"):
            raise PermissionError("Teams sender is not mapped to this workspace")
        if self.studio.team:
            self.studio.team.assert_execution(owner)
        # A role/config change during Graph I/O revokes the fetched result.
        if binding(self.profile(record["id"])) != event["teams_binding"]:
            raise PermissionError("Teams incoming configuration changed during read")
        event.update(
            status="received",
            sender=sender,
            channel=data["channel_id"],
            task=content[len(data["command_prefix"]) :],
            callback="",
            requester_nexus_id=owner if self.studio.team else None,
        )
