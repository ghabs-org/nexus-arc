"""nexus-router plugin alerts reach Telegram once per incident."""

import asyncio

import pytest

alerting = pytest.importorskip("src.alerting")

ALERT = {
    "code": "message",
    "since": "2026-11-24T11:16:45+00:00",
    "count": 2,
    "message": "OpenClaw stopped passing your messages to the router plugin.",
}


class FakeRouter:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    def diagnostics(self, identity=None):
        if self.error:
            raise self.error
        return self.payload


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(alerting, "emit_alert", lambda message, **kw: messages.append((message, kw)) or True)
    return messages


def use_router(monkeypatch, router):
    monkeypatch.setattr(alerting.RouterClient, "from_env", classmethod(lambda cls, *a, **k: router))


def test_each_incident_is_sent_once(monkeypatch, sent):
    payload = {"openclaw_plugin": {"alerts": [dict(ALERT)]}}
    use_router(monkeypatch, FakeRouter(payload))
    system = alerting.AlertingSystem()
    asyncio.run(system._check_router_plugin())
    asyncio.run(system._check_router_plugin())
    assert len(sent) == 1
    message, options = sent[0]
    assert message.startswith("🧭 Nexus Router") and ALERT["message"] in message
    assert options["severity"] == "warning" and options["dedup_key"] == f"router_plugin:message:{ALERT['since']}"
    # A new failure streak is a new incident.
    payload["openclaw_plugin"]["alerts"][0]["since"] = "2026-11-25T09:00:00+00:00"
    asyncio.run(system._check_router_plugin())
    assert len(sent) == 2


def test_no_router_an_unreachable_one_or_no_alerts_send_nothing(monkeypatch, sent):
    system = alerting.AlertingSystem()
    for router in (None, FakeRouter(error=ConnectionError("down")), FakeRouter({}),
                   FakeRouter({"openclaw_plugin": {"alerts": []}})):
        use_router(monkeypatch, router)
        asyncio.run(system._check_router_plugin())
    assert sent == []
