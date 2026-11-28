"""Vendor-shaped Graph basic notifications over real local HTTP; never trust hints."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler
from tests.test_studio_team import team as team
from tests.test_studio_teams import CHANNEL, GRAPH, TEAM

TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SUBSCRIPTION = "11111111-aaaa-bbbb-cccc-222222222222"


def notice(message="1612293113399", **extra):
    # Microsoft Graph's documented basic channel notification schema.
    resource = f"teams('{TEAM}')/channels('{CHANNEL}')/messages('{message}')"
    return {
        "subscriptionId": SUBSCRIPTION,
        "tenantId": TENANT,
        "clientState": "s" * 32,
        "changeType": "created",
        "resource": resource,
        "resourceData": {
            "id": message,
            "@odata.type": "#Microsoft.Graph.chatMessage",
            "@odata.id": resource,
        },
        **extra,
    }


@pytest.fixture(params=["files", "sql"])
def receiver(tmp_path, monkeypatch, request):
    monkeypatch.setenv("TEAMS_EVENT_SECRET", "s" * 32)
    monkeypatch.setenv("TEAMS_READER_TOKEN", "synthetic-reader-token")
    seen = []
    control = {
        "body": {
            "id": "1612293113399",
            "messageType": "message",
            "from": {"user": {"id": "alice"}},
            "body": {"contentType": "html", "content": "<p>/nexus count words</p>"},
        },
        "status": 200,
    }

    class GraphHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            payload = json.dumps(control["body"]).encode()
            self.send_response(control["status"])
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    graph = ThreadingHTTPServer(("127.0.0.1", 0), GraphHandler)
    thread = threading.Thread(target=graph.serve_forever, daemon=True)
    thread.start()

    def fetch(method, url, headers, body):
        assert method == "GET" and url.startswith(GRAPH + "/teams/" + TEAM + "/channels/")
        response = requests.get(
            url.replace(GRAPH, f"http://127.0.0.1:{graph.server_port}"),
            headers=headers,
            timeout=3,
            allow_redirects=False,
        )
        response.raise_for_status()
        return response.json()

    options = {"connection_options": {"enterprise_options": {"request_client": fetch}}}
    if request.param == "sql":
        options["state_options"] = {
            "url": "sqlite:///" + str(tmp_path / "events.sqlite"),
            "namespace": "teams-events",
        }
    app = StudioService(tmp_path, **options)
    app.save_definition(
        "workflows",
        {
            "name": "Count words",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "count",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": {"ref": "input"}},
                    }
                ],
                "output_mapping": {"ref": "nodes.count"},
            },
        },
    )
    entry = app.catalog.save(
        "entrypoints",
        {
            "name": "Incoming Teams",
            "data": {
                "type": "teams",
                "target_kind": "workflow",
                "target_id": "count-words",
                "enabled": True,
                "token_env": "TEAMS_EVENT_SECRET",
                "graph_token_env": "TEAMS_READER_TOKEN",
                "team_id": TEAM,
                "channel_id": CHANNEL,
                "tenant_id": TENANT,
                "subscription_id": SUBSCRIPTION,
                "identity_map": {"alice": "desktop"},
                "allowed_channels": [CHANNEL],
            },
        },
    )
    try:
        yield app, entry, seen, control, options
    finally:
        app.close()
        graph.shutdown()
        graph.server_close()
        thread.join(5)


def receive(app, payload=None):
    return app.messaging.receive(
        "incoming-teams", {}, json.dumps({"value": [payload or notice()]}).encode()
    )


def finish(app):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        runs = app.records("runs")
        if runs and runs[0]["status"] not in {"queued", "running"}:
            return runs[0]
        time.sleep(0.01)
    pytest.fail("Task did not finish")


def test_hint_is_durable_untrusted_deduplicated_and_recoverable(receiver):
    app, entry, seen, _, options = receiver
    forged = notice()
    forged["resourceData"].update(
        body={"content": "/nexus forged task"},
        sender="administrator",
        requester_nexus_id="administrator",
    )
    assert receive(app, forged) == {"ok": True}
    receive(app, notice())
    assert not seen and not app.records("runs")
    assert len(app.records("channel-events")) == 1
    app.close()
    restarted = StudioService(app.root, **options)
    try:
        restarted.messaging.tick()
        result = finish(restarted)
        assert result["status"] == "completed" and result["task"] == "count words"
        restarted.messaging.tick()
        receive(restarted)
        assert len(restarted.records("runs")) == 1
        assert len(seen) == 1 and seen[0][1] == "Bearer synthetic-reader-token"
        assert "clientState" not in json.dumps(restarted.records("channel-events"))
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "change",
    [
        {"clientState": "wrong"},
        {"subscriptionId": TENANT},
        {"tenantId": SUBSCRIPTION},
        {"resource": "https://attacker.invalid/credential"},
    ],
)
def test_unverified_or_wrong_scope_events_never_fetch(receiver, change):
    app, _, seen, _, _ = receiver
    with pytest.raises(PermissionError):
        receive(app, notice(**change))
    assert not seen and not app.records("channel-events") and not app.records("runs")


def test_mutations_noncommands_and_unmapped_senders_do_not_start_tasks(receiver):
    app, _, seen, control, _ = receiver
    receive(app, notice(changeType="updated"))
    receive(app, notice(changeType="deleted"))
    assert not app.records("channel-events")
    control["body"]["body"]["content"] = "Ordinary channel conversation"
    receive(app)
    app.messaging.tick()
    assert not app.records("runs") and app.records("channel-events")[0]["status"] == "ignored"
    control["body"]["id"] = "another"
    control["body"]["body"]["content"] = "/nexus run task"
    control["body"]["from"]["user"]["id"] = "stranger"
    receive(app, notice("another"))
    app.messaging.tick()
    assert not app.records("runs") and len(seen) == 2
    assert any(row["status"] == "failed" for row in app.records("channel-events"))


def test_config_revocation_and_graph_failure_never_launch(receiver):
    app, entry, seen, control, _ = receiver
    receive(app)
    app.catalog.save(
        "entrypoints", {**entry, "data": {**entry["data"], "identity_map": {"bob": "desktop"}}}
    )
    app.messaging.tick()
    assert not seen and not app.records("runs")
    control["status"] = 403
    receive(app, notice("another"))
    app.messaging.tick()
    app.messaging.tick()
    assert len(seen) == 1 and not app.records("runs")
    assert "synthetic-reader-token" not in json.dumps(app.records("channel-events"))


def test_real_http_challenge_and_batch_preflight(receiver):
    app = receiver[0]
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(app, "test-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/channel-events/incoming-teams"
    try:
        response = requests.post(url, params={"validationToken": "plain + token"}, timeout=3)
        assert response.status_code == 200 and response.text == "plain + token"
        assert response.headers["Content-Type"].startswith("text/plain")
        response = requests.post(
            url, json={"value": [notice(), notice(clientState="wrong")]}, timeout=3
        )
        assert response.status_code == 403 and not app.records("channel-events")
        assert requests.post(url, json={"value": [notice()]}, timeout=3).status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_reply_notification_reads_only_its_fixed_reply_path(receiver):
    app, _, seen, control, _ = receiver
    payload = notice()
    payload["resource"] += "/replies('reply-1')"
    control["body"]["id"] = "reply-1"
    receive(app, payload)
    app.messaging.tick()
    assert finish(app)["status"] == "completed"
    assert seen[0][0].endswith("/messages/1612293113399/replies/reply-1")


def test_team_reader_and_sender_roles_are_rechecked(receiver, team):
    source, entry, seen, _, options = receiver
    app, policy = team[:2]
    app.connections.enterprise.request_client = options["connection_options"]["enterprise_options"][
        "request_client"
    ]
    policy.environment_resolver = lambda identity: (
        {"TEAMS_READER_TOKEN": "reader-" + identity},
        None,
    )
    data = {
        **entry["data"],
        "target_kind": "agent",
        "target_id": "reviewer",
        "identity_map": {"alice": "bob"},
    }
    app.catalog.save(
        "entrypoints", {"name": "Incoming Teams", "requester_nexus_id": "owner", "data": data}
    )
    receive(app)
    assert app.records("channel-events")[0]["requester_nexus_id"] == "owner"
    policy.bindings["bob"] = "viewer"
    app.messaging.tick()
    assert not app.records("runs")
    assert seen[-1][1] == "Bearer reader-owner"
    receive(app, notice("second"))
    policy.bindings["owner"] = "viewer"
    count = len(seen)
    app.messaging.tick()
    assert not app.records("runs") and len(seen) == count


def test_disabled_setup_can_validate_before_subscription_exists(receiver):
    app, entry = receiver[:2]
    saved = app.catalog.save(
        "entrypoints", {**entry, "data": {**entry["data"], "enabled": False, "subscription_id": ""}}
    )
    assert app.messaging.teams.challenge(saved["id"], "setup-token") == b"setup-token"
    with pytest.raises(PermissionError):
        receive(app)
    with pytest.raises(ValueError):
        app.catalog.save("entrypoints", {**saved, "data": {**saved["data"], "enabled": True}})
    assert not app.records("channel-events") and not app.records("runs")
