"""Telegram transport on the gateway contract.

Translates Bot API update payloads (plain dicts, as delivered by webhooks
or polling) into :class:`GatewayTask` and sends replies through an injected
``sender`` callable — no ``python-telegram-bot`` import here, so the adapter
is usable from framework code and tests::

    adapter = TelegramGatewayAdapter(send=my_bot.send_text)
    await adapter.run(adapter.to_task(update_dict), handle)

The live ``TelegramInteractivePlugin`` still drives the production bot;
swapping it to this adapter is a follow-up (same contract, smaller core).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nexus.adapters.gateway import BaseGatewayAdapter, GatewayMessage, GatewayTask


def _get(payload: Any, *path: str, default: Any = "") -> Any:
    """Read nested dict attributes (supports dicts and objects)."""
    current = payload
    for key in path:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            current = getattr(current, key, default)
        if current is None:
            return default
    return current


def _update_to_parts(raw: Any) -> tuple[str, str, list[dict[str, Any]]]:
    """Return (user_id, text, files) from a Telegram update or message."""
    message = _get(raw, "message", default=None) or _get(raw, "callback_query", "message", default=None) or raw
    user = (
        _get(message, "from", default=None)
        or _get(message, "chat", default=None)
        or _get(raw, "callback_query", "from", default=None)
        or {}
    )
    user_id = str(_get(user, "id", default=""))
    text = str(_get(message, "text", default="") or _get(message, "caption", default="") or "")
    query_data = str(_get(raw, "callback_query", "data", default="") or "")
    if query_data and not text:
        text = query_data
    files: list[dict[str, Any]] = []
    photos = _get(message, "photo", default=[]) or []
    if isinstance(photos, list) and photos:
        biggest = photos[-1]
        files.append(
            {
                "file_id": str(_get(biggest, "file_id", default="")),
                "kind": "photo",
                "mime_type": "image/jpeg",
            }
        )
    document = _get(message, "document", default=None)
    if document:
        files.append(
            {
                "file_id": str(_get(document, "file_id", default="")),
                "kind": "document",
                "filename": str(_get(document, "file_name", default="file")),
                "mime_type": str(_get(document, "mime_type", default="")),
            }
        )
    return user_id, text, files


class TelegramGatewayAdapter(BaseGatewayAdapter):
    """Gateway contract over Telegram; ``send`` does the Bot API call.

    Args:
        send: ``send(chat_id, text) -> message_id`` callable.
    """

    def __init__(self, send: Callable[[str, str], str]):
        self._send = send
        self.sent: list[GatewayMessage] = []
        self._chats: dict[str, str] = {}

    def extract_identity(self, raw: Any) -> dict[str, str]:
        user_id, _, _ = _update_to_parts(raw)
        return {"user_id": user_id}

    def to_task(self, raw: Any) -> GatewayTask:
        user_id, text, files = _update_to_parts(raw)
        update_id = str(_get(raw, "update_id", default="") or "0")
        task = GatewayTask(
            task_id=f"tg-{update_id}-{user_id or 'unknown'}",
            user_id=user_id,
            text=text,
            files=files,
            metadata={"update_id": update_id},
        )
        self._chats[task.task_id] = user_id
        return task

    async def send(self, message: GatewayMessage) -> None:
        self.sent.append(message)
        text = message.text
        if message.error and not text:
            text = f"Something went wrong: {message.error}"
        if not text:
            return
        self._send(self._chats.get(message.task_id, ""), text)
