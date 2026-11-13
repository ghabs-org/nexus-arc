"""Transport-neutral gateway adapter: one small file per new chat transport.

A gateway translates between an outside channel (webhook, socket, poller)
and the core as plain data::

    adapter = EchoGatewayAdapter()
    task = adapter.to_task({"user_id": "7", "text": "hi"})
    await adapter.run(task, handle_text=lambda tid, text: ...)

Porting Telegram/Discord to this shape is the follow-up; the ABC below is
the contract they will implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GatewayTask:
    """Normalized inbound request."""

    task_id: str
    user_id: str
    text: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayMessage:
    """Normalized outbound chunk (final when ``done`` is True)."""

    task_id: str
    text: str = ""
    done: bool = False
    error: str | None = None


class BaseGatewayAdapter(ABC):
    """Seven small handlers; transport code stays out of the core."""

    @abstractmethod
    def extract_identity(self, raw: Any) -> dict[str, str]:
        """Return at least ``user_id`` from a raw inbound payload."""

    @abstractmethod
    def to_task(self, raw: Any) -> GatewayTask:
        """Convert a raw inbound payload to a normalized task."""

    @abstractmethod
    async def send(self, message: GatewayMessage) -> None:
        """Deliver one outbound chunk through the transport."""

    async def on_text(self, task: GatewayTask, chunk: str) -> None:
        """Stream one partial reply chunk."""
        await self.send(GatewayMessage(task_id=task.task_id, text=chunk))

    async def on_complete(self, task: GatewayTask, text: str) -> None:
        """Deliver the final reply."""
        await self.send(GatewayMessage(task_id=task.task_id, text=text, done=True))

    async def on_error(self, task: GatewayTask, error: str) -> None:
        """Deliver a failure notice."""
        await self.send(GatewayMessage(task_id=task.task_id, error=error, done=True))

    async def run(
        self,
        task: GatewayTask,
        handle: Callable[[GatewayTask], Awaitable[str]],
    ) -> None:
        """Execute *handle* for *task*, routing reply/error back out."""
        try:
            await self.on_complete(task, await handle(task))
        except Exception as exc:
            await self.on_error(task, str(exc))


class EchoGatewayAdapter(BaseGatewayAdapter):
    """In-memory adapter for tests and transport prototyping."""

    def __init__(self) -> None:
        self.sent: list[GatewayMessage] = []

    def extract_identity(self, raw: Any) -> dict[str, str]:
        payload = raw if isinstance(raw, dict) else {}
        return {"user_id": str(payload.get("user_id") or "")}

    def to_task(self, raw: Any) -> GatewayTask:
        payload = raw if isinstance(raw, dict) else {}
        identity = self.extract_identity(payload)
        return GatewayTask(
            task_id=str(payload.get("task_id") or f"{identity['user_id']}-1"),
            user_id=identity["user_id"],
            text=str(payload.get("text") or ""),
            files=list(payload.get("files") or []),
            metadata={k: v for k, v in payload.items() if k not in {"task_id", "user_id", "text", "files"}},
        )

    async def send(self, message: GatewayMessage) -> None:
        self.sent.append(message)
