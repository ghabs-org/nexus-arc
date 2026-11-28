"""Slack channel scope and single-attempt mutation guards without optional SDK I/O."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from nexus.adapters.notifications.base import Message
from nexus.core.studio import StudioService


@pytest.fixture
def service(tmp_path):
    adapter = AsyncMock()
    studio = StudioService(
        tmp_path, connection_options={"factories": {"slack": lambda *_: adapter}}
    )
    studio.catalog.save(
        "connectors",
        {
            "name": "Slack",
            "data": {
                "type": "slack",
                "channel": "CSELECTED",
                "enabled": True,
                "actions": ["send", "update_message"],
            },
        },
    )
    try:
        yield studio, adapter
    finally:
        studio.close()


def update(studio, message_id, *, approved=False):
    return asyncio.run(
        studio.connections.execute(
            "slack",
            {
                "action": "update_message",
                "confirm": True,
                "arguments": {"message_id": message_id, "text": "Reviewed update"},
            },
            approved=approved,
        )
    )


@pytest.mark.parametrize("approved", [False, True])
def test_slack_update_cannot_override_selected_channel(service, approved):
    studio, adapter = service
    with pytest.raises(PermissionError, match="channel"):
        update(studio, "COTHER:1700000000.000001", approved=approved)
    adapter.update_message.assert_not_called()


@pytest.mark.parametrize(
    "message_id",
    [
        None,
        1,
        1700000000.1,
        {},
        [],
        "",
        "1",
        "1.",
        ".1",
        "1e3.1",
        "1.2\n",
        "CSELECTED:other:1.2",
        "x" * 251,
    ],
)
def test_slack_update_rejects_invalid_timestamps_before_adapter(service, message_id):
    studio, adapter = service
    with pytest.raises(ValueError, match="timestamp"):
        update(studio, message_id)
    adapter.update_message.assert_not_called()


@pytest.mark.parametrize("message_id", ["1700000000.000001", "CSELECTED:1700000000.000001"])
def test_slack_update_uses_only_configured_channel(service, message_id):
    studio, adapter = service
    update(studio, message_id)
    adapter.update_message.assert_awaited_once_with("1700000000.000001", "Reviewed update")


def test_slack_sdk_does_not_retry_mutations(tmp_path, monkeypatch):
    import nexus.adapters.notifications.slack as module

    clients = []
    monkeypatch.setattr(module, "_SLACK_SDK_AVAILABLE", True)
    monkeypatch.setattr(
        module, "WebClient", lambda **kwargs: clients.append(kwargs) or object(), raising=False
    )
    monkeypatch.setenv("SLACK_UNIT_TOKEN", "synthetic-token")
    studio = StudioService(tmp_path)
    try:
        asyncio.run(
            studio.connections.adapter(
                {"type": "slack", "channel": "CSELECTED", "token_env": "SLACK_UNIT_TOKEN"}, None
            )
        )
        assert clients[0]["retry_handlers"] == []
    finally:
        studio.close()


def test_long_slack_messages_fit_section_limits():
    from nexus.adapters.notifications.slack import SlackNotificationChannel

    adapter = SlackNotificationChannel.__new__(SlackNotificationChannel)
    text = "Evidence " * 1000
    blocks = adapter._build_blocks(Message(text=text))
    assert "".join(block["text"]["text"] for block in blocks) == text
    assert all(len(block["text"]["text"]) <= 3000 for block in blocks)


def test_slack_send_and_update_logs_exclude_provider_error_values(monkeypatch, caplog):
    import nexus.adapters.notifications.slack as module

    class ProviderError(Exception):
        response = {"error": "PRIVATE_PROVIDER_DETAIL"}

    class Client:
        def chat_postMessage(self, **kwargs):  # noqa: N802 - Slack SDK method name
            raise ProviderError("PRIVATE_PROVIDER_DETAIL")

        def chat_update(self, **kwargs):
            raise ProviderError("PRIVATE_PROVIDER_DETAIL")

    monkeypatch.setattr(module, "SlackApiError", ProviderError, raising=False)
    adapter = module.SlackNotificationChannel.__new__(module.SlackNotificationChannel)
    adapter._client = Client()
    adapter._default_channel = "CSELECTED"
    with pytest.raises(ProviderError):
        asyncio.run(adapter.send_message("CSELECTED", Message(text="Reviewed")))
    with pytest.raises(ProviderError):
        asyncio.run(adapter.update_message("1.2", "Reviewed"))
    assert "PRIVATE_PROVIDER_DETAIL" not in caplog.text
    assert "inspect the remote outcome" in caplog.text


def test_standalone_adapter_retains_explicit_cross_channel_update():
    from unittest.mock import Mock

    from nexus.adapters.notifications.slack import SlackNotificationChannel

    adapter = SlackNotificationChannel.__new__(SlackNotificationChannel)
    adapter._client = Mock()
    adapter._default_channel = "CSELECTED"
    asyncio.run(adapter.update_message("COTHER:1700000000.000001", "Explicit adapter destination"))
    adapter._client.chat_update.assert_called_once_with(
        channel="COTHER", ts="1700000000.000001", text="Explicit adapter destination"
    )
