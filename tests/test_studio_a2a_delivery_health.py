"""Owner-scoped health never decrypts credentials or dispatches notifications."""

import json
import urllib.request

import pytest

from tests.test_studio_a2a_http import gateway as gateway
from tests.test_studio_a2a_push import callback as callback
from tests.test_studio_a2a_push import send_with_push
from tests.test_studio_team import team as team


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_health_reports_delivery_attempts_without_private_fields(callback, gateway):
    studio, entry, request, config, observed, control, _, _ = callback
    send_with_push(request, config)
    control["status"] = 502
    studio.a2a.push.tick(100)
    origin = gateway[2].split("/api/")[0]
    with urllib.request.urlopen(
        origin + "/api/studio/entrypoints/" + entry["id"] + "/a2a-deliveries", timeout=5
    ) as response:
        health = json.load(response)
    assert health["version"] == 1 and health["ready"]
    assert health["items"][0]["status"] == "retrying"
    assert health["items"][0]["attempt"] == 1 and health["items"][0]["next_due"] == 105
    for secret in [
        config["url"],
        "synthetic-callback-secret",
        "synthetic-correlation",
        "envelope",
        "payload",
        "Remote report",
    ]:
        assert secret not in json.dumps(health)
    control["status"] = 204
    studio.a2a.push.tick(105)
    item = studio.a2a.push.health(entry["id"], None)["items"][0]
    assert item["status"] == "delivered" and item["attempt"] == 2 and item["next_due"] is None
    assert len(observed) == 2
    # Reading status never posts another notification.
    studio.a2a.push.health(entry["id"], None)
    assert len(observed) == 2


def test_health_pagination_and_binding_changes(callback):
    studio, entry, request, config, observed, _, _, _ = callback
    task = send_with_push(request, config)
    studio.a2a.push.create(entry, task["id"], {**config, "id": "second"}, "1.0")
    first = studio.a2a.push.health(entry["id"], None, limit=1)
    second = studio.a2a.push.health(entry["id"], None, limit=1, after=first["next_cursor"])
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert second["next_cursor"] is None
    for limit in [0, 101, True]:
        with pytest.raises(ValueError):
            studio.a2a.push.health(entry["id"], None, limit=limit)
    with pytest.raises(ValueError):
        studio.a2a.push.health(entry["id"], None, after="foreign-cursor")
    entry["data"]["a2a_push"]["destinations"] = []
    studio._write(studio.catalog._path("entrypoints", entry["id"]), entry)
    health = studio.a2a.push.health(entry["id"], None)
    assert not health["ready"]
    assert all(item["status"] == "configuration_changed" for item in health["items"])
    assert not observed


def test_health_does_not_unseal_private_payloads(callback, monkeypatch):
    from nexus.core import studio_a2a_push

    studio, entry, request, config, _, _, _, _ = callback
    send_with_push(request, config)

    def forbidden(*args):
        pytest.fail("Health tried to decrypt private callback data")

    monkeypatch.setattr(studio_a2a_push, "unseal", forbidden)
    assert studio.a2a.push.health(entry["id"], None)["items"][0]["status"] == "pending"
    # Older private records did not persist attempt counters; do not invent zero.
    row = studio.records("a2a-push")[0]
    row.update(status="delivered")
    row.pop("attempts", None)
    studio._write(studio._path("a2a-push", row["id"]), row)
    assert studio.a2a.push.health(entry["id"], None)["items"][0]["attempt"] is None


def test_health_denies_other_accounts_including_administrators(team):
    studio, _, _, request, _ = team
    entry = studio.catalog.save(
        "entrypoints",
        {
            "name": "Alice callbacks",
            "requester_nexus_id": "alice",
            "data": {
                "target_kind": "agent",
                "target_id": "reviewer",
                "token_env": "ALICE_TOKEN",
                "a2a_enabled": True,
            },
        },
    )
    route = "/api/studio/entrypoints/" + entry["id"] + "/a2a-deliveries"
    assert request(route, user="alice")[0] == 200
    for user in ["bob", "viewer", "owner"]:
        assert request(route, user=user)[0] == 403
    assert request(route)[0] == 401
