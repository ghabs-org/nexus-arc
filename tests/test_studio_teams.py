"""Independent Graph-shaped HTTP peer; real connector dispatch and approval controls."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from nexus.core.studio import StudioService
from nexus.core.studio_teams import GRAPH, validate_teams
from tests.test_studio_team import team as team

TEAM = "11111111-2222-3333-4444-555555555555"
CHANNEL = "19:test@thread.tacv2"


@pytest.fixture
def graph(tmp_path, monkeypatch):
    seen, control = [], {"status": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def dispatch(self):
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null"
            )
            seen.append((self.command, self.path, self.headers.get("Authorization"), body))
            content = json.dumps(
                {
                    "id": "message-123",
                    "value": [{"id": "message-123", "body": {"content": "Fixture text"}}],
                }
            ).encode()
            self.send_response(control["status"])
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def do_PATCH(self):
            self.dispatch()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, url, headers, body):
        assert url.startswith(GRAPH + "/teams/" + TEAM + "/channels/")
        response = requests.request(
            method,
            url.replace(GRAPH, f"http://127.0.0.1:{server.server_port}"),
            headers=headers,
            json=body,
            timeout=3,
            allow_redirects=False,
        )
        response.raise_for_status()
        return response.json()

    monkeypatch.setenv("TEAMS_TOKEN", "synthetic-delegated-token")
    app = StudioService(
        tmp_path, connection_options={"enterprise_options": {"request_client": request}}
    )
    data = {
        "type": "teams",
        "team_id": TEAM,
        "channel_id": CHANNEL,
        "token_env": "TEAMS_TOKEN",
        "enabled": True,
        "actions": ["status", "query", "send", "update_message"],
    }
    connector = app.catalog.save("connectors", {"name": "Team channel", "data": data})
    try:
        yield app, connector, seen, control, request
    finally:
        app.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


def call(graph, action, arguments=None, confirm=False):
    app, connector = graph[:2]
    return asyncio.run(
        app.connections.execute(
            connector["id"], {"action": action, "arguments": arguments or {}, "confirm": confirm}
        )
    )


def test_read_send_reply_update_and_pagination_contracts(graph):
    app, connector, seen, _, _ = graph
    assert validate_teams(connector["data"]) == connector["data"]
    call(graph, "status")
    call(graph, "query", {"limit": 3})
    call(graph, "query", {"message_id": "message-123", "replies": True})
    cursor = (
        GRAPH + "/teams/" + TEAM + "/channels/19%3Atest%40thread.tacv2/messages?$skiptoken=next"
    )
    call(graph, "query", {"next_page": cursor})
    for action, args in [
        ("send", {"text": "Reviewed"}),
        ("send", {"text": "Reply", "reply_to": "message-123"}),
        ("update_message", {"text": "Edited", "message_id": "message-123"}),
    ]:
        count = len(seen)
        with pytest.raises(ValueError, match="confirm"):
            call(graph, action, args)
        assert len(seen) == count
        call(graph, action, args, confirm=True)
    assert [r[0] for r in seen] == ["GET"] * 4 + ["POST", "POST", "PATCH"]
    assert all(r[2] == "Bearer synthetic-delegated-token" for r in seen)
    assert seen[-2][1].endswith("/messages/message-123/replies")
    assert seen[-1][3] == {"body": {"contentType": "text", "content": "Edited"}}


@pytest.mark.parametrize(
    "arguments",
    [
        {"next_page": "https://other.example/messages"},
        {"next_page": GRAPH + "/v1.0/me/messages"},
        {"limit": 51},
        {"limit": True},
        {"message_id": "../other"},
        {"replies": True},
        {"headers": {"Authorization": "other"}},
    ],
)
def test_invalid_queries_fail_before_network(graph, arguments):
    with pytest.raises(ValueError):
        call(graph, "query", arguments)
    assert not graph[2]


def test_failure_never_retries_or_exposes_private_error(graph):
    graph[3]["status"] = 503
    with pytest.raises(ValueError, match="recorded remote outcome") as exc:
        call(graph, "send", {"text": "Reviewed"}, confirm=True)
    assert len(graph[2]) == 1
    assert "synthetic-delegated-token" not in str(exc.value)


def test_selected_actions_and_disabled_connection_are_enforced(graph):
    app, connector = graph[:2]
    connector["data"]["actions"] = ["status"]
    app._write(app.catalog._path("connectors", connector["id"]), connector)
    with pytest.raises(PermissionError):
        call(graph, "send", {"text": "No"}, confirm=True)
    connector["data"]["enabled"] = False
    app._write(app.catalog._path("connectors", connector["id"]), connector)
    with pytest.raises(ValueError, match="disabled"):
        call(graph, "status")
    assert not graph[2]


def test_team_execution_resolves_personal_token_and_rechecks_role(graph, team):
    studio, policy = team[:2]
    studio.connections.enterprise.request_client = graph[4]
    connector = studio.catalog.save(
        "connectors",
        {"name": "Alice Teams", "requester_nexus_id": "alice", "data": graph[1]["data"]},
    )
    policy.environment_resolver = lambda identity: ({"TEAMS_TOKEN": "personal-" + identity}, None)
    asyncio.run(studio.connections.execute(connector["id"], {"action": "status"}, "alice"))
    assert graph[2][-1][2] == "Bearer personal-alice"
    policy.bindings["alice"] = "viewer"
    with pytest.raises(PermissionError):
        asyncio.run(studio.connections.execute(connector["id"], {"action": "status"}, "alice"))
    assert len(graph[2]) == 1
