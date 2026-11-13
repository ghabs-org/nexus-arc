"""Authenticated callback delivery over real HTTP, file and transactional state."""

import base64
import concurrent.futures
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nexus.core.studio_a2a_push import settings
from tests.test_studio_a2a_http import gateway as gateway
from tests.test_studio_a2a_http import message


@pytest.fixture
def callback(gateway, monkeypatch):
    studio, entry, base, request = gateway
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", base64.b64encode(b"x" * 32).decode())
    observed = []
    control = {"status": 204, "block": False}
    entered, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append((self.path, dict(self.headers), body))
            entered.set()
            if control["block"]:
                release.wait(5)
            self.send_response(control["status"])
            if control["status"] == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            if control["status"] == 502:
                self.wfile.write(b"<html>PRIVATE_RECEIVER_ERROR</html>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/callback"
    entry = studio.catalog.save(
        "entrypoints",
        {
            "id": entry["id"],
            "name": entry["name"],
            "revision": entry["revision"],
            "data": {**entry["data"], "a2a_push": {"destinations": [url], "allow_private": True}},
        },
    )
    config = {
        "id": "receiver",
        "url": url,
        "token": "synthetic-correlation",
        "authentication": {"scheme": "Bearer", "credentials": "synthetic-callback-secret"},
    }
    try:
        yield studio, entry, request, config, observed, control, entered, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def send_with_push(request, config):
    status, result, _ = request(
        "/rest/message:send", {**message(), "configuration": {"taskPushNotificationConfig": config}}
    )
    assert status == 200, result
    return result["task"]


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_callback_registration_delivery_cross_binding_and_redaction(callback):
    studio, entry, request, config, observed, _, _, _ = callback
    task = send_with_push(request, config)
    path = "/rest/tasks/" + task["id"] + "/pushNotificationConfigs"
    saved = request(path + "/receiver")[1]
    assert saved["authentication"] == {"scheme": "Bearer"}
    assert request(path)[1]["configs"] == [saved]
    assert request("/rest/extendedAgentCard")[1]["capabilities"]["pushNotifications"]
    studio.a2a.push.tick(100)
    assert len(observed) == 1
    _, headers, body = observed[0]
    assert headers["Authorization"] == "Bearer synthetic-callback-secret"
    assert headers["Content-Type"] == "application/a2a+json"
    assert headers["X-A2A-Notification-Token"] == "synthetic-correlation"
    assert body == {"task": task}
    assert studio.records("a2a-push")[0]["status"] == "delivered"
    studio.a2a.push.tick(101)
    assert len(observed) == 1
    assert send_with_push(request, config)["id"] == task["id"]
    assert len(studio.records("runs")) == 1 and len(studio.records("a2a-push")) == 1
    raw = json.dumps(studio.records("a2a-push"))
    assert "synthetic-callback-secret" not in raw and '"task"' not in raw
    legacy = request(
        "",
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tasks/pushNotificationConfig/get",
            "params": {"id": task["id"], "pushNotificationConfigId": "receiver"},
        },
        headers={"A2A-Version": "0.3"},
    )[1]["result"]
    assert legacy["pushNotificationConfig"]["authentication"] == {"schemes": ["Bearer"]}
    assert request(path + "/receiver", method="DELETE")[1] == {}
    assert request(path + "/receiver")[0] == 404
    assert request(path)[1]["configs"] == []


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
@pytest.mark.parametrize("failure", [302, 401, 502])
def test_failed_deliveries_retry_same_event_only_three_times(callback, failure):
    studio, entry, request, config, observed, control, _, _ = callback
    send_with_push(request, config)
    control["status"] = failure
    for now in (100, 105, 115, 200):
        studio.a2a.push.tick(now)
    assert len(observed) == 3
    assert len({r[1]["X-Nexus-Delivery-ID"] for r in observed}) == 1
    assert all(r[0] == "/callback" for r in observed)
    assert observed[0][2] == observed[1][2] == observed[2][2]
    row = studio.records("a2a-push")[0]
    assert row["status"] == "failed" and row["flight"] is None
    assert "PRIVATE_RECEIVER_ERROR" not in json.dumps(row)
    assert len(studio.records("runs")) == 1


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_success_after_retry_still_one_task(callback):
    studio, _, request, config, observed, control, _, _ = callback
    send_with_push(request, config)
    control["status"] = 502
    studio.a2a.push.tick(100)
    studio.a2a.push.tick(101)
    assert len(observed) == 1
    control["status"] = 204
    studio.a2a.push.tick(105)
    assert len(observed) == 2
    assert observed[0][1]["X-Nexus-Delivery-ID"] == observed[1][1]["X-Nexus-Delivery-ID"]
    assert studio.records("a2a-push")[0]["status"] == "delivered"


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_delete_during_dispatch_never_recreates_configuration(callback):
    studio, _, request, config, observed, control, entered, release = callback
    task = send_with_push(request, config)
    control["block"] = True
    with concurrent.futures.ThreadPoolExecutor() as pool:
        delivery = pool.submit(studio.a2a.push.tick, 100)
        assert entered.wait(5)
        assert (
            request(
                "/rest/tasks/" + task["id"] + "/pushNotificationConfigs/receiver", method="DELETE"
            )[0]
            == 200
        )
        release.set()
        delivery.result(5)
    assert not studio.records("a2a-push") and len(observed) == 1


@pytest.mark.parametrize("change", ["disabled", "policy", "owner", "scope"])
def test_entrypoint_changes_revoke_dispatch(callback, change):
    studio, entry, request, config, observed, _, _, _ = callback
    send_with_push(request, config)
    if change == "scope":
        row = studio.records("a2a-push")[0]
        row["envelope"] = row["envelope"][:-1]
        studio._write(studio._path("a2a-push", row["id"]), row)
    else:
        if change == "disabled":
            entry["data"]["enabled"] = False
        elif change == "policy":
            entry["data"]["a2a_push"]["destinations"] = []
        else:
            entry["owner_id"] = "bob"
        studio._write(studio.catalog._path("entrypoints", entry["id"]), entry)
    studio.a2a.push.tick(100)
    assert not observed and studio.records("a2a-push")[0]["status"] == "blocked"


@pytest.mark.parametrize(
    "override",
    [
        {"url": "https://unapproved.example/callback"},
        {"authentication": {"scheme": "Basic", "credentials": "secret"}},
        {"authentication": {"scheme": "Bearer", "credentials": "injected\r\nHeader"}},
        {"taskId": "different"},
        {"id": "../escape"},
    ],
)
def test_invalid_configuration_fails_before_task_creation(callback, override):
    studio, _, request, config, observed, _, _, _ = callback
    result = request(
        "/rest/message:send",
        {**message(), "configuration": {"taskPushNotificationConfig": {**config, **override}}},
    )
    assert result[0] == 400
    assert not studio.records("runs") and not studio.records("a2a-push") and not observed


def test_missing_encryption_key_fails_before_task_creation(callback, monkeypatch):
    studio, _, request, config, _, _, _, _ = callback
    monkeypatch.delenv("NEXUS_CREDENTIALS_MASTER_KEY")
    assert (
        request(
            "/rest/message:send",
            {**message(), "configuration": {"taskPushNotificationConfig": config}},
        )[0]
        == 400
    )
    assert not studio.records("runs")


@pytest.mark.parametrize(
    "value",
    [
        {"destinations": ["http://example.com/callback"], "allow_private": True},
        {"destinations": ["https://user:password@example.com/callback"]},
        {"destinations": ["https://example.com/callback?secret=value"]},
        {"destinations": "https://example.com"},
        {"allow_private": "yes"},
    ],
)
def test_owner_policy_rejects_unsafe_urls(value):
    with pytest.raises(ValueError):
        settings(value)


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_interrupted_acknowledgement_recovers_same_delivery_after_restart(callback):
    from nexus.core.studio import StudioService

    studio, _, request, config, observed, control, entered, release = callback
    send_with_push(request, config)
    control["block"] = True
    with concurrent.futures.ThreadPoolExecutor() as pool:
        delivery = pool.submit(studio.a2a.push.tick, 100)
        assert entered.wait(5)
        interrupted = studio.records("a2a-push")[0]
        release.set()
        delivery.result(5)
    # Model process death after receiver accepted the POST but before local ack.
    studio._write(studio._path("a2a-push", interrupted["id"]), interrupted)
    options = (
        {
            "url": str(studio.sql_state.engine.url),
            "namespace": studio.sql_state.namespace,
            "workers": 1,
        }
        if studio.sql_state
        else None
    )
    studio.close()
    copy = StudioService(studio.root, state_options=options)
    try:
        copy.a2a.push.tick(159)
        assert len(observed) == 1
        copy.a2a.push.tick(160)
        assert len(observed) == 2
        assert observed[0][1]["X-Nexus-Delivery-ID"] == observed[1][1]["X-Nexus-Delivery-ID"]
        assert observed[0][2] == observed[1][2]
        assert copy.records("a2a-push")[0]["status"] == "delivered"
        assert len(copy.records("runs")) == 1
    finally:
        copy.close()


@pytest.mark.parametrize("gateway", ["sqlite"], indirect=True)
def test_two_sql_controllers_claim_callback_once(callback):
    from nexus.core.studio import StudioService

    studio, _, request, config, observed, control, entered, release = callback
    send_with_push(request, config)
    copy = StudioService(
        studio.root,
        state_options={
            "url": str(studio.sql_state.engine.url),
            "namespace": studio.sql_state.namespace,
            "workers": 1,
        },
    )
    control["block"] = True
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            delivery = pool.submit(studio.a2a.push.tick, 100)
            assert entered.wait(5)
            copy.a2a.push.tick(100)
            assert len(observed) == 1
            release.set()
            delivery.result(5)
        assert copy.records("a2a-push")[0]["status"] == "delivered"
    finally:
        release.set()
        copy.close()


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_push_secrets_are_excluded_from_backup_restore(callback, tmp_path):
    from nexus.core.studio import StudioService
    from nexus.core.studio_backup import create_snapshot, restore_snapshot

    studio, _, request, config, _, _, _, _ = callback
    send_with_push(request, config)
    snapshot = tmp_path.parent / (tmp_path.name + "-snapshot")
    create_snapshot(studio.root, snapshot, quiesced=True, sql_state=studio.sql_state)
    assert not (snapshot / "state" / "a2a-push").exists()
    result = restore_snapshot(snapshot, tmp_path.parent / (tmp_path.name + "-restored"))
    copy = StudioService(result["root"], state_dir=result["state_dir"])
    try:
        assert not copy.records("a2a-push")
    finally:
        copy.close()


def test_rest_and_legacy_registration_share_one_configuration(callback):
    studio, entry, request, config, _, _, _, _ = callback
    task = request("/rest/message:send", message())[1]["task"]
    old = {
        **config,
        "authentication": {
            "schemes": ["Bearer"],
            "credentials": config["authentication"]["credentials"],
        },
    }
    status, response, _ = request(
        "",
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tasks/pushNotificationConfig/set",
            "params": {"id": task["id"], "pushNotificationConfig": old},
        },
        headers={"A2A-Version": "0.3"},
    )
    assert status == 200 and "result" in response, response
    path = "/rest/tasks/" + task["id"] + "/pushNotificationConfigs"
    assert request(path + "/receiver")[1]["taskId"] == task["id"]
    assert request(path, {**config, "taskId": "different"})[0] == 400
    assert request(path, config)[1]["id"] == "receiver"
    assert request(path, {**config, "url": "https://other.example"})[0] == 400
    assert len(studio.records("a2a-push")) == 1
    assert request(path + "?pageSize=0")[0] == 400


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_replacement_configuration_cannot_authorize_older_claim(callback, monkeypatch):
    from nexus.core import studio_a2a_push

    studio, _, request, config, observed, _, _, _ = callback
    task = send_with_push(request, config)
    entered, release = threading.Event(), threading.Event()
    original = studio_a2a_push.validate_destination

    def checked(url, private):
        entered.set()
        release.wait(5)
        return original(url, private)

    monkeypatch.setattr(studio_a2a_push, "validate_destination", checked)
    path = "/rest/tasks/" + task["id"] + "/pushNotificationConfigs"
    with concurrent.futures.ThreadPoolExecutor() as pool:
        delivery = pool.submit(studio.a2a.push.tick, 100)
        assert entered.wait(5)
        assert request(path + "/receiver", method="DELETE")[0] == 200
        assert request(path, {**config, "token": "replacement"})[0] == 200
        release.set()
        delivery.result(5)
    assert not observed
    assert studio.records("a2a-push")[0]["status"] == "pending"
    studio.a2a.push.tick(101)
    assert len(observed) == 1 and observed[0][1]["X-A2A-Notification-Token"] == "replacement"
