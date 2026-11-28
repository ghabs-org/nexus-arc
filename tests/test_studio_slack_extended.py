"""Tests for Slack outbound artifacts, rich replies, feedback, cancellation, workflow semantics, and Socket Mode."""

import asyncio
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.adapters.notifications.base import Message
from nexus.adapters.notifications.slack import SlackNotificationChannel
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_slack_artifacts import (
    MAX_ARTIFACT_SIZE,
    SlackArtifacts,
    sanitize_filename,
)
from nexus.core.studio_slack_rich import (
    build_artifact_blocks,
    build_cancellation_action_block,
    build_feedback_action_block,
    build_rich_reply_blocks,
    build_status_context_block,
    split_text_sections,
)
from nexus.core.studio_slack_socket_mode import StudioSlackSocketMode


# ---------------------------------------------------------------------------
# 1. Rich Replies & Block Kit Formatting Tests
# ---------------------------------------------------------------------------


def test_split_text_sections_bounds():
    assert split_text_sections("") == []
    short_text = "Short update"
    blocks = split_text_sections(short_text)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == short_text

    long_text = "A" * 7500
    long_blocks = split_text_sections(long_text, limit=3000)
    assert len(long_blocks) == 3
    assert len(long_blocks[0]["text"]["text"]) == 3000
    assert len(long_blocks[1]["text"]["text"]) == 3000
    assert len(long_blocks[2]["text"]["text"]) == 1500


def test_status_context_and_action_blocks():
    ctx = build_status_context_block("completed", run_id="run-1234567890abcdef", elapsed_seconds=3.14)
    assert ctx["type"] == "context"
    assert any("Completed" in el["text"] for el in ctx["elements"])
    assert any("run-12345678" in el["text"] for el in ctx["elements"])

    fb = build_feedback_action_block("pos_token", "neg_token", run_id="run-1")
    assert fb["type"] == "actions"
    assert len(fb["elements"]) == 2
    assert fb["elements"][0]["action_id"] == "slack_feedback_positive"
    assert fb["elements"][0]["value"] == "pos_token"

    cancel = build_cancellation_action_block("cancel_token", run_id="run-1")
    assert cancel["type"] == "actions"
    assert cancel["elements"][0]["action_id"] == "slack_cancel_task"


def test_build_rich_reply_blocks_composition():
    blocks = build_rich_reply_blocks(
        text="Task complete",
        status="completed",
        run_id="run-99",
        elapsed_seconds=1.2,
        artifacts=[{"name": "output.json", "size": 128, "type": "application/json"}],
        feedback_tokens=("nexus_feedback:pos", "nexus_feedback:neg"),
    )
    assert any(b.get("type") == "section" for b in blocks)
    assert any(b.get("type") == "context" for b in blocks)
    assert any(b.get("type") == "actions" for b in blocks)
    assert any("Generated Artifacts" in str(b) for b in blocks)


# ---------------------------------------------------------------------------
# 2. Outbound Artifact Delivery Tests
# ---------------------------------------------------------------------------


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("valid_report-v1.json") == "valid_report-v1.json"
    assert sanitize_filename("") == "artifact.bin"
    assert sanitize_filename(".hidden") == "artifact.bin"


def test_outbound_artifact_validation_and_upload(tmp_path):
    studio = StudioService(tmp_path)
    studio.catalog.save(
        "connectors",
        {
            "name": "SlackConn",
            "data": {
                "type": "slack",
                "channel": "CSELECTED",
                "token_env": "SLACK_ART_TOKEN",
                "enabled": True,
                "actions": ["send", "upload_artifact"],
            },
        },
    )

    art_path = studio._path("artifacts", "art-1")
    art_path.parent.mkdir(parents=True, exist_ok=True)
    art_path.write_bytes(b"Artifact content binary")

    uploader = SlackArtifacts(studio)

    # Missing token fails cleanly
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="token is not configured"):
            uploader.upload_artifact("slackconn", "CSELECTED", "art-1")

    # Mismatched channel fails permission
    with pytest.raises(PermissionError, match="destination channel must match"):
        uploader.upload_artifact("slackconn", "CWRONG", "art-1")

    # Oversized artifact rejected
    big_art = studio._path("artifacts", "art-big")
    with patch.object(os.path, "getsize", return_value=MAX_ARTIFACT_SIZE + 1):
        with patch.dict(os.environ, {"SLACK_ART_TOKEN": "xoxb-mock"}):
            with pytest.raises(ValueError, match="exceeds maximum allowed size"):
                with patch("pathlib.Path.stat") as mock_stat:
                    mock_stat.return_value.st_size = MAX_ARTIFACT_SIZE + 100
                    uploader.upload_artifact("slackconn", "CSELECTED", "art-1")

    studio.close()


# ---------------------------------------------------------------------------
# 3. Workflow Semantics in Slack Threads
# ---------------------------------------------------------------------------


def test_workflow_threads_configuration_and_validation(tmp_path):
    studio = StudioService(tmp_path)
    studio.save_definition(
        "workflows",
        {
            "name": "Audit Workflow",
            "graph": {
                "version": 2,
                "nodes": [{"id": "step1", "kind": "input", "output_schema": {"type": "string"}}],
            },
        },
    )
    studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review code."})

    # Valid workflow target with workflow_threads: True
    ep = studio.catalog.save(
        "entrypoints",
        {
            "name": "WorkflowSlack",
            "data": {
                "type": "slack",
                "target_kind": "workflow",
                "target_id": "audit-workflow",
                "workflow_threads": True,
                "token_env": "SLACK_SECRET",
                "identity_map": {"user1": "desktop"},
                "allowed_channels": ["C123"],
                "enabled": True,
            },
        },
    )
    assert ep["data"]["workflow_threads"] is True

    # workflow_threads with agent target rejected
    with pytest.raises(ValueError, match="workflow target"):
        studio.catalog.save(
            "entrypoints",
            {
                "name": "BadWorkflowSlack",
                "data": {
                    "type": "slack",
                    "target_kind": "agent",
                    "target_id": "reviewer",
                    "workflow_threads": True,
                    "token_env": "SLACK_SECRET",
                    "identity_map": {"user1": "desktop"},
                    "allowed_channels": ["C123"],
                    "enabled": True,
                },
            },
        )
    studio.close()


def test_workflow_thread_input_resumption(tmp_path):
    studio = StudioService(tmp_path)
    studio.save_definition(
        "workflows",
        {
            "name": "Input Workflow",
            "graph": {
                "version": 2,
                "nodes": [{"id": "ask", "kind": "input", "output_schema": {"type": "string"}}],
            },
        },
    )
    studio.catalog.save(
        "entrypoints",
        {
            "name": "WfEntry",
            "data": {
                "type": "slack",
                "target_kind": "workflow",
                "target_id": "input-workflow",
                "workflow_threads": True,
                "token_env": "SLACK_SECRET",
                "identity_map": {"alice": "desktop"},
                "allowed_channels": ["C123"],
                "enabled": True,
            },
        },
    )

    # Synthetic session id
    session_id = "slack-test-wf-session"
    waiting_run = {
        "id": "run-wf-1",
        "kind": "workflow",
        "status": "waiting",
        "session_id": session_id,
        "awaiting_input": True,
        "task": "Original task",
    }
    studio._write(studio._path("runs", "run-wf-1"), waiting_run)

    # Verify resume hook
    studio.graph = MagicMock()
    # Trigger resumption via thread message
    event = {
        "id": "ev-1",
        "entrypoint_id": "wfentry",
        "sender": "alice",
        "channel": "C123",
        "task": "Submitted response answer",
        "status": "slack_received",
        "callback": "",
        "requester_nexus_id": "desktop",
        "slack_thread": {
            "format": "nexus-slack-thread/v1",
            "entrypoint_id": "wfentry",
            "entrypoint_revision": 1,
            "workspace_id": "W123",
            "channel": "C123",
            "sender": "alice",
            "owner_id": "desktop",
            "thread_ts": "1700000001.000001",
            "target_kind": "workflow",
            "target_id": "input-workflow",
        },
    }
    studio._write(studio._path("channel-events", "ev-1"), event)

    with patch("nexus.core.studio_slack_threads.context", return_value={"session_id": session_id}):
        studio.messaging.tick()

    studio.graph.resume.assert_called_once_with("run-wf-1", "Submitted response answer")
    studio.close()


# ---------------------------------------------------------------------------
# 4. Feedback Controls & Task Cancellation Tests
# ---------------------------------------------------------------------------


def test_feedback_action_button_recording(tmp_path):
    studio = StudioService(tmp_path)
    run = {
        "id": "run-feedback-test",
        "kind": "chat",
        "status": "completed",
        "task": "Test task",
        "requester_nexus_id": "desktop",
    }
    studio._write(studio._path("runs", "run-feedback-test"), run)

    # Seed feedback action token
    action_token = "0" * 32
    studio._write(
        studio._path("channel-actions", action_token),
        {
            "entrypoint_id": "incoming",
            "requester_nexus_id": "desktop",
            "sender": "alice",
            "channel": "C123",
            "type": "feedback",
            "run_id": "run-feedback-test",
            "rating": 1,
            "comment": "Great help!",
            "used": False,
            "expires": time.time() + 3600,
        },
    )

    event = {
        "entrypoint_id": "incoming",
        "requester_nexus_id": "desktop",
        "sender": "alice",
        "channel": "C123",
    }
    studio.messaging.approval("nexus_feedback:" + action_token, event)

    feedback_records = studio.collaboration.list_feedback("desktop")
    assert len(feedback_records) == 1
    assert feedback_records[0]["run_id"] == "run-feedback-test"
    assert feedback_records[0]["rating"] == 1

    studio.close()


def test_task_cancellation_button_and_command(tmp_path):
    studio = StudioService(tmp_path)
    run = {
        "id": "run-cancel-test",
        "kind": "chat",
        "status": "waiting",
        "task": "Long running task",
        "requester_nexus_id": "desktop",
    }
    studio._write(studio._path("runs", "run-cancel-test"), run)

    # Cancel via action token
    cancel_token = "1" * 32
    studio._write(
        studio._path("channel-actions", cancel_token),
        {
            "entrypoint_id": "incoming",
            "requester_nexus_id": "desktop",
            "sender": "alice",
            "channel": "C123",
            "type": "cancel",
            "run_id": "run-cancel-test",
            "used": False,
            "expires": time.time() + 3600,
        },
    )

    event = {
        "entrypoint_id": "incoming",
        "requester_nexus_id": "desktop",
        "sender": "alice",
        "channel": "C123",
    }
    studio.messaging.approval("nexus_cancel:" + cancel_token, event)

    updated_run = studio.run("run-cancel-test")
    assert updated_run["status"] == "cancelled"

    studio.close()


# ---------------------------------------------------------------------------
# 5. Socket Mode Client Tests
# ---------------------------------------------------------------------------


def test_socket_mode_initialization_and_envelope_dispatch(tmp_path):
    studio = StudioService(tmp_path)

    # Mock client simulating Socket Mode
    mock_client = MagicMock()
    mock_client.socket_mode_request_listeners = []

    received_events = []

    def on_event(req_type, payload):
        received_events.append((req_type, payload))

    socket_mode = StudioSlackSocketMode(
        studio=studio,
        app_token="xapp-test-app-token",
        bot_token="xoxb-test-bot-token",
        on_event=on_event,
        custom_client=mock_client,
    )

    assert socket_mode.connect() is True
    assert socket_mode.is_connected is True
    assert len(mock_client.socket_mode_request_listeners) == 1

    listener = mock_client.socket_mode_request_listeners[0]

    # Simulate incoming envelope
    fake_req = MagicMock()
    fake_req.envelope_id = "env-12345"
    fake_req.type = "events_api"
    fake_req.payload = {"event": {"type": "message", "text": "Hello Socket Mode"}}

    listener(mock_client, fake_req)

    # Verifies immediate response envelope acknowledgment
    mock_client.send_socket_mode_response.assert_called_once()
    sent_resp = mock_client.send_socket_mode_response.call_args[0][0]
    assert sent_resp.envelope_id == "env-12345"

    assert len(received_events) == 1
    assert received_events[0][0] == "events_api"
    assert received_events[0][1]["event"]["text"] == "Hello Socket Mode"

    socket_mode.disconnect()
    assert socket_mode.is_connected is False
    studio.close()
