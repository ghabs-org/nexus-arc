"""Channel authenticity, durable deduplication and user-bound workflow approvals."""

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def done(app, run):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        record = app.run(run["id"])
        if record["status"] not in {"queued", "running"}:
            return record
        time.sleep(0.01)
    pytest.fail("Run did not stop")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=True, output="Reviewed")

    service = StudioService(tmp_path, provider_factory=lambda _: Provider())
    service.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    yield service
    service.close()


def entry(app, kind="telegram", target="agent", target_id="reviewer", **extra):
    return app.catalog.save(
        "entrypoints",
        {
            "name": "Incoming",
            "data": {
                "type": kind,
                "target_kind": target,
                "target_id": target_id,
                "token_env": "EVENT_SECRET",
                "enabled": True,
                "identity_map": {"alice": "desktop", "bob": "desktop"},
                "allowed_channels": ["room"],
                **extra,
            },
        },
    )


def telegram(app, body):
    return app.messaging.receive(
        "incoming", {"X-Telegram-Bot-Api-Secret-Token": "s" * 32}, json.dumps(body).encode()
    )


def message(number=1, sender="alice", channel="room"):
    return {
        "update_id": number,
        "message": {"from": {"id": sender}, "chat": {"id": channel}, "text": "Review this"},
    }


def test_signed_receipt_deduplicates_and_rechecks_mapping(app):
    entry(app)
    with pytest.raises(PermissionError, match="verification"):
        app.messaging.receive("incoming", {}, json.dumps(message()).encode())
    with pytest.raises(PermissionError, match="mapped"):
        telegram(app, message(sender="stranger"))
    telegram(app, message())
    telegram(app, message())
    assert len(app.records("channel-events")) == 1
    app.messaging.tick()
    run = done(app, app.records("runs")[0])
    assert run["status"] == "completed" and run["task"] == "Review this"
    app.messaging.tick()
    assert len(app.records("runs")) == 1
    telegram(app, message(2))
    saved = app.catalog.get("entrypoints", "incoming")
    app.catalog.save(
        "entrypoints", {**saved, "data": {**saved["data"], "identity_map": {"bob": "desktop"}}}
    )
    app.messaging.tick()
    assert len(app.records("runs")) == 1
    assert any(event["status"] == "failed" for event in app.records("channel-events"))


def test_slack_requires_raw_body_signature_and_fresh_timestamp(app):
    entry(app, kind="slack")
    body = json.dumps({"type": "url_verification", "challenge": "proof"}).encode()
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(b"s" * 32, b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    )
    headers = {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature}
    assert app.messaging.receive("incoming", headers, body) == {"challenge": "proof"}
    with pytest.raises(PermissionError):
        app.messaging.receive("incoming", headers, body + b" ")
    headers["X-Slack-Request-Timestamp"] = "1"
    with pytest.raises(PermissionError, match="timestamp"):
        app.messaging.receive("incoming", headers, body)


def test_approval_button_bound_to_platform_sender_and_consumed_once(app):
    adapter = AsyncMock()
    adapter.send_message.return_value = "sent"
    app.connections.factories["telegram"] = lambda *args: adapter
    # Use Slack adapter interface for this test; Telegram Bot has its own call shape.
    app.connections.factories["slack"] = lambda *args: adapter
    app.catalog.save(
        "connectors",
        {
            "name": "Replies",
            "data": {"type": "slack", "channel": "room", "enabled": True, "actions": ["send"]},
        },
    )
    app.save_definition(
        "workflows",
        {
            "name": "Approval",
            "graph": {
                "nodes": [
                    {"id": "gate", "name": "Review", "kind": "approval"},
                    {"id": "agent", "name": "Reviewer", "agent_id": "reviewer", "prompt": "{task}"},
                ],
                "edges": [
                    {"source": "START", "target": "gate"},
                    {"source": "gate", "target": "agent"},
                ],
            },
        },
    )
    entry(
        app,
        kind="slack",
        target="workflow",
        target_id="approval",
        notification_connector_id="replies",
        approval_notifications=True,
    )
    event = {
        "id": "event",
        "entrypoint_id": "incoming",
        "sender": "alice",
        "channel": "room",
        "requester_nexus_id": None,
    }
    run = done(
        app,
        app.operations.launch_target(
            {"target_kind": "workflow", "target_id": "approval"}, "Review"
        ),
    )
    assert run["status"] == "waiting"
    app.messaging.notify(event, app.catalog.get("entrypoints", "incoming")["data"], run)
    sent = adapter.send_message.await_args.args[1]
    token = sent.buttons[0].callback_data
    with pytest.raises(PermissionError, match="another user"):
        app.messaging.approval(token, {**event, "sender": "bob"})
    app.messaging.approval(token, event)
    assert done(app, run)["status"] == "completed"
    with pytest.raises(PermissionError, match="used"):
        app.messaging.approval(token, event)


def test_discord_real_ed25519_signature(app, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = Ed25519PrivateKey.generate()
    monkeypatch.setenv(
        "EVENT_SECRET", private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    )
    entry(app, kind="discord")
    raw = b'{"type":1}'
    timestamp = str(int(time.time()))
    headers = {
        "X-Signature-Timestamp": timestamp,
        "X-Signature-Ed25519": private.sign(timestamp.encode() + raw).hex(),
    }
    assert app.messaging.receive("incoming", headers, raw) == {"type": 1}
    with pytest.raises(PermissionError):
        app.messaging.receive("incoming", headers, raw + b" ")


def test_whatsapp_webhook_hmac_verification_and_normalization(app, monkeypatch):
    import hashlib
    import hmac

    secret = "whatsapp-app-secret-for-webhook-test"
    monkeypatch.setenv("EVENT_SECRET", secret)
    entry(app, kind="whatsapp")
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "room"},
                            "messages": [
                                {
                                    "id": "wamid.123",
                                    "from": "alice",
                                    "text": {"body": "Summarize release"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert app.messaging.receive("incoming", {"X-Hub-Signature-256": sig}, raw) == {"ok": True}
    app.messaging.tick()
    events = app.records("channel-events")
    assert len(events) == 1 and events[0]["task"] == "Summarize release"
    with pytest.raises(PermissionError):
        app.messaging.receive("incoming", {"X-Hub-Signature-256": sig + "0"}, raw)
