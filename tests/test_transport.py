"""Message transports: memory fan-out, MQTT wiring, registry creation."""


def test_memory_publish_subscribe():
    from nexus.adapters.transport import MemoryTransport

    received = []
    transport = MemoryTransport()
    transport.subscribe("nexus/events", lambda topic, payload: received.append((topic, payload)))
    transport.subscribe("other", lambda topic, payload: received.append(("wrong", payload)))
    transport.publish("nexus/events", {"type": "done"})
    assert received == [("nexus/events", {"type": "done"})]
    transport.close()


def test_factory_memory_and_unknown(monkeypatch):
    import pytest

    from nexus.adapters.transport import MemoryTransport, create_transport

    monkeypatch.delenv("NEXUS_TRANSPORT", raising=False)
    assert isinstance(create_transport(), MemoryTransport)
    with pytest.raises(ValueError, match="Unknown transport"):
        create_transport("kafka")


def test_mqtt_missing_dep_raises_helpfully(monkeypatch):
    import builtins
    import sys

    import pytest

    monkeypatch.delitem(sys.modules, "paho", raising=False)
    monkeypatch.delitem(sys.modules, "paho.mqtt", raising=False)
    monkeypatch.delitem(sys.modules, "paho.mqtt.client", raising=False)
    real_import = builtins.__import__

    def _guarded(name, *args, **kwargs):
        if name == "paho.mqtt.client" or name.startswith("paho.mqtt.client."):
            raise ImportError("No module named 'paho'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded)
    from nexus.adapters.transport import MqttTransport

    with pytest.raises(ImportError, match="nexus-arc\\[mqtt\\]"):
        MqttTransport(host="localhost")


def test_mqtt_publish_subscribe_with_fake_client(monkeypatch):
    import sys
    import types

    import nexus.adapters.transport as _transport

    sent = []
    paho_client = types.ModuleType("paho.mqtt.client")
    paho_client.MQTTv5 = 5

    class _Client:
        def __init__(self, *args, **kwargs):
            self.on_message = None

        def username_pw_set(self, *args):
            pass

        def connect(self, *args):
            pass

        def loop_start(self):
            pass

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

        def subscribe(self, topic):
            pass

        def publish(self, topic, payload):
            sent.append((topic, payload))
            message = types.SimpleNamespace(topic=topic, payload=payload.encode())
            self.on_message(self, None, message)

    paho_client.Client = _Client
    paho_mqtt = types.ModuleType("paho.mqtt")
    paho_mqtt.client = paho_client
    paho = types.ModuleType("paho")
    paho.mqtt = paho_mqtt
    monkeypatch.setitem(sys.modules, "paho", paho)
    monkeypatch.setitem(sys.modules, "paho.mqtt", paho_mqtt)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", paho_client)

    received = []
    transport = _transport.MqttTransport(host="localhost")
    transport.subscribe("t", lambda topic, payload: received.append(payload))
    transport.publish("t", {"n": 1})
    transport.close()
    assert received == [{"n": 1}]
    assert sent[0][0] == "t"


def test_registry_creates_memory_transport():
    from nexus.adapters.registry import AdapterRegistry
    from nexus.adapters.transport import MemoryTransport

    assert isinstance(AdapterRegistry().create_transport("memory"), MemoryTransport)
