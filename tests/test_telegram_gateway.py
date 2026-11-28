"""Telegram gateway adapter: update translation and reply routing."""

import asyncio

import pytest


def _adapter(sent=None):
    from nexus.adapters.telegram_gateway import TelegramGatewayAdapter

    outbox = sent if sent is not None else []
    return (
        TelegramGatewayAdapter(send=lambda chat_id, text: outbox.append((chat_id, text)) or "1"),
        outbox,
    )


def test_message_update_roundtrip():
    adapter, outbox = _adapter()
    task = adapter.to_task(
        {"update_id": 5, "message": {"from": {"id": 7}, "chat": {"id": 7}, "text": "hi"}}
    )
    assert (task.user_id, task.text) == ("7", "hi")
    asyncio.run(adapter.run(task, lambda task: asyncio.sleep(0, result=f"echo:{task.text}")))
    assert outbox == [("7", "echo:hi")]


def test_photo_and_callback_query():
    adapter, _ = _adapter()
    task = adapter.to_task(
        {
            "update_id": 6,
            "message": {
                "from": {"id": 8},
                "chat": {"id": 8},
                "photo": [{"file_id": "small"}, {"file_id": "big"}],
            },
        }
    )
    assert task.files == [{"file_id": "big", "kind": "photo", "mime_type": "image/jpeg"}]
    task = adapter.to_task(
        {"update_id": 7, "callback_query": {"data": "chat:new", "message": {"message_id": 1},
                                            "from": {"id": 9}}},
    )
    assert (task.user_id, task.text) == ("9", "chat:new")


def test_error_reaches_user():
    adapter, outbox = _adapter()

    async def _boom(task):
        raise ValueError("kaput")

    task = adapter.to_task({"update_id": 8, "message": {"from": {"id": 1}, "text": "x"}})
    asyncio.run(adapter.run(task, _boom))
    assert outbox == [("1", "Something went wrong: kaput")]


def test_plugin_normalizes_through_gateway():
    """Live plugin must parse inbound updates via the gateway adapter."""
    telegram = pytest.importorskip("telegram")

    from nexus.adapters.telegram_gateway import TelegramGatewayAdapter
    from nexus.plugins.builtin.telegram_interactive_plugin import TelegramInteractivePlugin

    plugin = TelegramInteractivePlugin({"bot_token": "test-token"})
    assert isinstance(plugin._gateway, TelegramGatewayAdapter)
    update_dict = {
        "update_id": 42,
        "message": {
            "message_id": 1,
            "from": {"id": 7},
            "chat": {"id": 7},
            "text": "/status",
        },
    }
    task = plugin._gateway.to_task(update_dict)
    assert (task.user_id, task.text) == ("7", "/status")
