"""Message transports: publish/subscribe beyond process boundaries.

One :class:`MessageTransport` shape for event buses (MQTT today, Kafka when
a deployment needs it) plus an in-memory transport for tests and local dev::

    transport = create_transport("mqtt", host="broker.local")
    transport.publish("nexus/events", {"type": "workflow.done"})
    transport.subscribe("nexus/events", on_event)

Client libraries stay optional: MQTT needs ``pip install nexus-arc[mqtt]``.
Kafka arrives the same way — subclass the ABC, no other changes.
"""

from __future__ import annotations

import os
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class MessageTransport(ABC):
    """Publish/subscribe transport with JSON-serializable payloads."""

    @abstractmethod
    def publish(self, topic: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def subscribe(self, topic: str, callback: Callable[[str, dict[str, Any]], None]) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class MemoryTransport(MessageTransport):
    """In-process fan-out: every subscriber gets every matching message."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        with self._lock:
            callbacks = list(self._subs.get(topic, []))
        for callback in callbacks:
            callback(topic, dict(payload))

    def subscribe(self, topic: str, callback: Callable[[str, dict[str, Any]], None]) -> None:
        with self._lock:
            self._subs.setdefault(topic, []).append(callback)

    def close(self) -> None:
        with self._lock:
            self._subs.clear()


class MqttTransport(MessageTransport):
    """MQTT transport via paho-mqtt (background network loop).

    Requires ``pip install nexus-arc[mqtt]``.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_id: str = "nexus-arc",
    ):
        try:
            import paho.mqtt.client as _mqtt
        except ImportError as exc:
            raise ImportError(
                "paho-mqtt is required for MQTT transport. "
                "Install it with: pip install nexus-arc[mqtt]"
            ) from exc
        self._mqtt = _mqtt
        self._callbacks: dict[str, list[Callable]] = {}
        self._client = _mqtt.Client(client_id=client_id, protocol=_mqtt.MQTTv5)
        if username:
            self._client.username_pw_set(username, password)
        self._client.on_message = self._on_message
        self._client.connect(host, int(port))
        self._client.loop_start()

    def _on_message(self, _client, _userdata, message) -> None:
        import json as _json

        try:
            payload = _json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        for callback in self._callbacks.get(message.topic, []):
            callback(message.topic, payload)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        import json as _json

        self._client.publish(topic, _json.dumps(payload))

    def subscribe(self, topic: str, callback: Callable[[str, dict[str, Any]], None]) -> None:
        if topic not in self._callbacks:
            self._client.subscribe(topic)
            self._callbacks[topic] = []
        self._callbacks[topic].append(callback)

    def close(self) -> None:
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass


def create_transport(kind: str = "", **kwargs: Any) -> MessageTransport:
    """Build a transport by kind (``memory`` default, ``mqtt``); env overrides.

    Env: ``NEXUS_TRANSPORT`` (memory|mqtt), ``MQTT_HOST``, ``MQTT_PORT``,
    ``MQTT_USERNAME``, ``MQTT_PASSWORD``, ``MQTT_CLIENT_ID``.
    """
    resolved = (kind or os.getenv("NEXUS_TRANSPORT", "memory")).strip().lower()
    if resolved == "memory":
        return MemoryTransport()
    if resolved == "mqtt":
        return MqttTransport(
            host=str(kwargs.get("host") or os.getenv("MQTT_HOST", "localhost")),
            port=int(kwargs.get("port") or os.getenv("MQTT_PORT", "1883")),
            username=kwargs.get("username") or os.getenv("MQTT_USERNAME"),
            password=kwargs.get("password") or os.getenv("MQTT_PASSWORD"),
            client_id=str(kwargs.get("client_id") or os.getenv("MQTT_CLIENT_ID", "nexus-arc")),
        )
    raise ValueError(f"Unknown transport: {resolved}")
