"""Message transports: publish/subscribe beyond process boundaries.

One :class:`MessageTransport` shape for event buses (memory, MQTT, Kafka)
plus an in-memory transport for tests and local dev::

    transport = create_transport("mqtt", host="broker.local")
    transport.publish("nexus/events", {"type": "workflow.done"})
    transport.subscribe("nexus/events", on_event)

Client libraries stay optional: MQTT needs ``pip install nexus-arc[mqtt]``,
Kafka needs ``pip install nexus-arc[kafka]``.
"""

from __future__ import annotations

import os
import threading
import uuid
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
        client_id: str = "",
        tls: bool = False,
        ca_certs: str | None = None,
        connection_timeout: float = 10,
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
        self._lock = threading.RLock()
        self._connected, self._connection_answer = threading.Event(), threading.Event()
        self._connection_failed, self._closed = False, False
        if not 0 < connection_timeout <= 60:
            raise ValueError("MQTT connection timeout must be between zero and sixty seconds")
        api_options = (
            {"callback_api_version": _mqtt.CallbackAPIVersion.VERSION2}
            if hasattr(_mqtt, "CallbackAPIVersion")
            else {}
        )
        self._client = _mqtt.Client(
            client_id=client_id or "nexus-arc-" + uuid.uuid4().hex,
            protocol=_mqtt.MQTTv5,
            **api_options,
        )
        if hasattr(self._client, "connect_timeout"):
            self._client.connect_timeout = connection_timeout
        if username:
            self._client.username_pw_set(username, password)
        if tls:
            self._client.tls_set(ca_certs=ca_certs)
        self._client.on_message = self._on_message
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        try:
            self._client.connect(host, int(port))
            self._client.loop_start()
            if not self._connection_answer.wait(connection_timeout):
                raise ConnectionError("MQTT broker did not confirm the connection")
            if self._connection_failed or not self._connected.is_set():
                raise ConnectionError("MQTT broker rejected the connection")
        except BaseException:
            self.close()
            raise

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        self._connection_failed = reason_code != 0
        if not self._connection_failed and not self._closed:
            # New MQTT sessions lose subscriptions after reconnect. Restore them
            # from the network callback without blocking that same network loop.
            with self._lock:
                topics = list(self._callbacks)
            for topic in topics:
                client.subscribe(topic)
            self._connected.set()
        self._connection_answer.set()

    def _on_disconnect(self, *_args):
        self._connected.clear()

    def _on_message(self, _client, _userdata, message) -> None:
        import json as _json

        try:
            payload = _json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            callbacks = list(self._callbacks.get(message.topic, []))
        for callback in callbacks:
            try:
                callback(message.topic, payload)
            except Exception:
                continue  # One subscriber must not stop the network loop.

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        import json as _json

        if not self._connected.is_set() or self._closed:
            raise ConnectionError("MQTT transport is disconnected")
        result = self._client.publish(topic, _json.dumps(payload))
        if getattr(result, "rc", 0) != 0:
            raise ConnectionError("MQTT transport did not accept the event")

    def subscribe(self, topic: str, callback: Callable[[str, dict[str, Any]], None]) -> None:
        with self._lock:
            if self._closed:
                raise ConnectionError("MQTT transport is closed")
            first = topic not in self._callbacks
            self._callbacks.setdefault(topic, []).append(callback)
            if first and self._connected.is_set():
                self._client.subscribe(topic)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._connected.clear()
        try:
            self._client.disconnect()
        except Exception:
            pass
        try:
            self._client.loop_stop()
        except Exception:
            pass


class KafkaTransport(MessageTransport):
    """Kafka via kafka-python (background consumer thread).

    Requires ``pip install nexus-arc[kafka]``. Unproven against a live
    broker — covered by fake-client tests only until a deployment needs it.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        group_id: str = "nexus-arc",
        **kwargs: Any,
    ):
        import json as _json

        try:
            import kafka as _kafka
        except ImportError as exc:
            raise ImportError(
                "kafka-python is required for Kafka transport. "
                "Install it with: pip install nexus-arc[kafka]"
            ) from exc
        self._json = _json
        self._callbacks: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._producer = _kafka.KafkaProducer(
            bootstrap_servers=str(bootstrap_servers),
            value_serializer=lambda value: _json.dumps(value).encode("utf-8"),
            **kwargs,
        )
        self._consumer = _kafka.KafkaConsumer(
            bootstrap_servers=str(bootstrap_servers),
            group_id=str(group_id),
            value_deserializer=lambda data: _json.loads(data.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
            **{
                key: value
                for key, value in kwargs.items()
                if key.startswith(("ssl_", "sasl_"))
                or key in {"security_protocol", "api_version", "client_id"}
            },
        )
        self._thread = threading.Thread(target=self._poll, name="nexus-kafka", daemon=True)
        self._thread.start()

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                records = self._consumer.poll(timeout_ms=100)
            except Exception:
                self._stop.wait(0.1)
                continue
            if not records:
                self._stop.wait(0.01)
            for _partition, messages in (records or {}).items():
                for message in messages:
                    topic = getattr(message, "topic", "")
                    payload = getattr(message, "value", None)
                    if not isinstance(payload, dict):
                        continue
                    with self._lock:
                        callbacks = list(self._callbacks.get(topic, []))
                    for callback in callbacks:
                        try:
                            callback(topic, payload)
                        except Exception:
                            pass

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self._producer.send(topic, payload)

    def subscribe(self, topic: str, callback: Callable[[str, dict[str, Any]], None]) -> None:
        # KafkaConsumer.subscribe replaces the subscription: always resend the full set.
        with self._lock:
            self._callbacks.setdefault(topic, []).append(callback)
            self._consumer.subscribe(sorted(self._callbacks))

    def close(self) -> None:
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
        try:
            self._producer.close()
            self._consumer.close()
        except Exception:
            pass


def create_transport(kind: str = "", **kwargs: Any) -> MessageTransport:
    """Build a transport by kind (``memory`` default, ``mqtt``, ``kafka``); env overrides.

    Env: ``NEXUS_TRANSPORT`` (memory|mqtt|kafka), ``MQTT_HOST``, ``MQTT_PORT``,
    ``MQTT_USERNAME``, ``MQTT_PASSWORD``, ``MQTT_CLIENT_ID``,
    ``KAFKA_BOOTSTRAP_SERVERS``, ``KAFKA_GROUP_ID``.
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
            client_id=str(kwargs.get("client_id") or os.getenv("MQTT_CLIENT_ID", "")),
            tls=bool(kwargs.get("tls", False)),
            ca_certs=kwargs.get("ca_certs"),
            connection_timeout=kwargs.get("connection_timeout", 10),
        )
    if resolved == "kafka":
        extra = {k: v for k, v in kwargs.items() if k not in {"bootstrap_servers", "group_id"}}
        return KafkaTransport(
            bootstrap_servers=str(
                kwargs.get("bootstrap_servers")
                or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            ),
            group_id=str(kwargs.get("group_id") or os.getenv("KAFKA_GROUP_ID", "nexus-arc")),
            **extra,
        )
    raise ValueError(f"Unknown transport: {resolved}")
