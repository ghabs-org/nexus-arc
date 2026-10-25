"""Verified Slack thread bindings over existing private Nexus conversations."""

import hashlib
import json
import re

from nexus.adapters.notifications.slack import validate_timestamp

FORMAT = "nexus-slack-thread/v1"
FIELDS = {
    "format",
    "entrypoint_id",
    "entrypoint_revision",
    "workspace_id",
    "channel",
    "sender",
    "owner_id",
    "thread_ts",
    "target_kind",
    "target_id",
}


def capture(record, body, sender, channel, owner):
    """Capture only verified routing metadata, never arbitrary event state."""
    message = body["event"]
    validate_timestamp(message.get("ts"))
    root = validate_timestamp(message["thread_ts"] if "thread_ts" in message else message.get("ts"))
    workspace = body.get("team_id", "")
    if not isinstance(workspace, str) or not re.fullmatch(r"[A-Za-z0-9_-]{0,96}", workspace):
        raise ValueError("Slack workspace ID must be a bounded identifier")
    return {
        "format": FORMAT,
        "entrypoint_id": record["id"],
        "entrypoint_revision": record["revision"],
        "workspace_id": workspace,
        "channel": channel,
        "sender": sender,
        "owner_id": owner,
        "thread_ts": root,
        "target_kind": record["data"]["target_kind"],
        "target_id": record["data"]["target_id"],
    }


def assert_current(studio, event, record):
    binding = event.get("slack_thread")
    data = record["data"]
    if (
        not isinstance(binding, dict)
        or set(binding) != FIELDS
        or binding["format"] != FORMAT
        or data.get("type") != "slack"
        or not data.get("threaded_conversations")
        or not data["enabled"]
        or binding["entrypoint_id"] != record["id"]
        or type(binding["entrypoint_revision"]) is not int
        or binding["entrypoint_revision"] != record["revision"]
        or not isinstance(binding["workspace_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{0,96}", binding["workspace_id"])
        or binding["target_kind"] != "agent"
        or data["target_kind"] != "agent"
        or binding["target_id"] != data["target_id"]
        or any(binding[key] != event[key] for key in ("sender", "channel"))
        or binding["owner_id"] != event["requester_nexus_id"]
        or data["identity_map"].get(event["sender"]) != (event["requester_nexus_id"] or "desktop")
        or event["channel"] not in data["allowed_channels"]
    ):
        raise PermissionError("Slack thread configuration or identity changed")
    validate_timestamp(binding["thread_ts"])
    if studio.team:
        studio.team.assert_execution(event["requester_nexus_id"])
    return binding


def context(studio, event, record):
    """Return an owned session context, or None while earlier work is pending.

    Call under the existing workspace/SQL lock. Session metadata is additive;
    history storage, permissions, active-session guards and jobs stay native.
    """
    binding = assert_current(studio, event, record)
    for other in studio.records("channel-events", limit=None):
        if other["id"] == event["id"] or other.get("slack_thread") != binding:
            continue
        if other["status"] in {"slack_launching", "slack_files_launching", "uncertain"}:
            raise ValueError("Earlier Slack dispatch outcome is uncertain; review Activity")
        if (other.get("created", 0), other["id"]) < (event["created"], event["id"]) and other[
            "status"
        ] in {
            "slack_received",
            "slack_running",
            "slack_files_pending",
            "slack_files_fetching",
            "slack_files_ready",
            "slack_files_running",
        }:
            return None
    key = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    session_id = "slack-" + key
    runs = [
        run for run in studio.records("runs", limit=None) if run.get("session_id") == session_id
    ]
    if session_id in studio.active_sessions or any(
        run["status"] in {"queued", "running", "waiting"} for run in runs
    ):
        return None
    path = studio._path("sessions", session_id)
    if path.exists():
        session = studio.session(session_id)
        if (
            session.get("owner_id") != binding["owner_id"]
            or session.get("slack_thread_binding") != binding
        ):
            raise PermissionError("Slack thread conversation binding changed")
        if session.get("archived"):
            raise ValueError("Restore the archived Slack conversation before continuing")
    else:
        if runs:
            raise ValueError("Slack conversation is missing; inspect its history before continuing")
        session = studio._new_session(
            session_id, {"task": event["task"], "requester_nexus_id": binding["owner_id"]}
        )
        session["slack_thread_binding"] = binding
        studio._write(path, session)
    return {"session_id": session_id}
