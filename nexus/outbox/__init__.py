"""Transactional outbox: durable events with at-least-once delivery.

Producers append; a poller drains through a handler, acking successes and
parking poison events in the dead-letter list after ``max_attempts``::

    outbox = FileOutbox("./data/outbox.json")
    outbox.enqueue("workflow.done", {"workflow_id": "wf-1"})
    delivered, dead = outbox.drain(send_to_webhook)

Single JSON file backend — fine for operator pace; move to Postgres when
multiple drainers or high volume need it.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OutboxEvent:
    """One queued event with delivery attempts."""

    id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    last_error: str = ""


class FileOutbox:
    """JSON-file outbox: append on enqueue, rewrite on drain."""

    def __init__(self, path: str | Path, max_attempts: int = 5):
        self._path = Path(path)
        self._max_attempts = max(1, int(max_attempts))
        self._events: list[OutboxEvent] = []
        self._dead: list[OutboxEvent] = []
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            stored = json.loads(self._path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            stored = {}
        fields = set(OutboxEvent.__dataclass_fields__)
        for raw in stored.get("pending") or []:
            try:
                self._events.append(
                    OutboxEvent(**{k: v for k, v in raw.items() if k in fields})
                )
            except TypeError:
                continue
        for raw in stored.get("dead") or []:
            try:
                self._dead.append(
                    OutboxEvent(**{k: v for k, v in raw.items() if k in fields})
                )
            except TypeError:
                continue

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {"pending": [asdict(e) for e in self._events],
                 "dead": [asdict(e) for e in self._dead]}
            ),
            encoding="utf-8",
        )

    def enqueue(self, kind: str, payload: dict[str, Any] | None = None) -> str:
        """Append an event; returns its id."""
        event = OutboxEvent(id=uuid.uuid4().hex[:12], kind=str(kind),
                            payload=dict(payload or {}))
        self._events.append(event)
        self._save()
        return event.id

    def pending(self) -> list[OutboxEvent]:
        """Unacknowledged events, oldest first."""
        return list(self._events)

    def dead(self) -> list[OutboxEvent]:
        """Poison events parked after ``max_attempts`` failures."""
        return list(self._dead)

    def drain(self, handler: Callable[[OutboxEvent], None]) -> dict[str, int]:
        """Deliver every pending event; returns {delivered, dead} counts."""
        delivered = 0
        for event in list(self._events):
            try:
                handler(event)
            except Exception as exc:
                event.attempts += 1
                event.last_error = str(exc)[:500]
                if event.attempts >= self._max_attempts:
                    self._events.remove(event)
                    self._dead.append(event)
                continue
            self._events.remove(event)
            delivered += 1
        self._save()
        return {"delivered": delivered, "dead": len(self._dead)}
