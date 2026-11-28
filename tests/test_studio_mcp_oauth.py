"""Delegation over real HTTP, with synthetic accounts only."""

import base64
import hashlib
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from nexus.core.auth.delegated_credentials import seal, unseal
from nexus.core.studio import StudioService
from nexus.core.studio_connections import validate_connection
from nexus.core.studio_oauth import fingerprint
from tests.test_studio_team import team as team


@pytest.fixture
def authorization_server():
    state = {
        "requests": [],
        "mode": "ok",
        "refreshes": 0,
        "mcp": [],
        "streams": {},
        "stop": threading.Event(),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state["requests"].append(("GET", self.path))
            origin = state["origin"]
            if self.path in {"/sse", "/mcp"}:
                state["mcp"].append(("GET", self.headers.get("Authorization")))
                if any(
                    self.headers.get(key) != value
                    for key, value in state.get(
                        "expected_headers", {"Authorization": "Bearer synthetic-access"}
                    ).items()
                ):
                    return self.reply({}, 401)
                if self.path == "/mcp":
                    return self.reply({}, 405)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                stream_id = str(len(state["streams"]))
                messages = queue.Queue()
                state["streams"][stream_id] = messages
                self.wfile.write(
                    ("event: endpoint\ndata: /messages/" + stream_id + "\n\n").encode()
                )
                self.wfile.flush()
                while not state["stop"].is_set():
                    try:
                        message = messages.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        self.wfile.write(
                            ("event: message\ndata: " + json.dumps(message) + "\n\n").encode()
                        )
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                return
            if self.path in {
                "/.well-known/oauth-protected-resource/mcp",
                "/.well-known/oauth-protected-resource/sse",
            }:
                return self.reply(
                    {
                        "resource": origin + "/mcp",
                        "authorization_servers": [origin],
                        "scopes_supported": ["unrelated"],
                    }
                )
            if self.path == "/.well-known/oauth-authorization-server" and state["mode"] == "oidc":
                return self.reply({}, 404)
            if self.path in {
                "/.well-known/oauth-authorization-server",
                "/.well-known/openid-configuration",
            }:
                return self.reply(
                    {
                        "issuer": origin + ("/" if state["mode"] == "wrong-issuer" else ""),
                        "authorization_endpoint": origin + "/authorize",
                        "token_endpoint": origin + "/token",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
                        "authorization_response_iss_parameter_supported": True,
                    }
                )
            return self.reply({}, 501)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
            if self.path == "/mcp" or self.path.startswith("/messages/"):
                state["mcp"].append(("POST", self.headers.get("Authorization")))
                if any(
                    self.headers.get(key) != value
                    for key, value in state.get(
                        "expected_headers", {"Authorization": "Bearer synthetic-access"}
                    ).items()
                ):
                    return self.reply({}, 401)
                message = json.loads(raw)
                method = message["method"]
                if "id" not in message:
                    return self.reply({}, 202)
                if method == "initialize":
                    result = {
                        "protocolVersion": message["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "synthetic", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "lookup",
                                "description": "Fixture",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                elif method == "tools/call":
                    state.setdefault("calls", []).append(message["params"])
                    result = {
                        "content": [{"type": "text", "text": "cited fixture"}],
                        "isError": state["mode"] == "tool-error",
                    }
                else:
                    return self.reply({}, 501)
                response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
                if self.path.startswith("/messages/"):
                    state["streams"][self.path.rsplit("/", 1)[-1]].put(response)
                    return self.reply({}, 202)
                return self.reply(response)
            form = parse_qs(raw)
            state["requests"].append(("POST", form))
            if state.get("entered"):
                state["entered"].set()
                state["release"].wait(5)
            if state["mode"] == "failure":
                return self.reply({"error": "invalid_grant", "private": "DO_NOT_LEAK"}, 400)
            if state["mode"] == "redirect":
                self.send_response(302)
                self.send_header("Location", state["origin"] + "/must-not-follow")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if form["grant_type"] == ["refresh_token"]:
                state["refreshes"] += 1
            return self.reply(
                {
                    "access_token": "synthetic-access",
                    "refresh_token": "rotated-" + str(state["refreshes"]),
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "read extra" if state["mode"] == "scope-expansion" else "read",
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    state["stop"].set()
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture(params=["files", "sqlite"])
def app(request, tmp_path, monkeypatch, authorization_server):
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", base64.b64encode(b"s" * 32).decode())
    options = (
        {"state_options": {"url": "sqlite:///" + str(tmp_path / "state.db"), "namespace": "oauth"}}
        if request.param == "sqlite"
        else {}
    )
    service = StudioService(tmp_path, **options)
    origin = authorization_server["origin"]
    service.catalog.save(
        "connectors",
        {
            "id": "remote",
            "name": "Remote",
            "data": {
                "type": "mcp",
                "transport": "streamable-http",
                "url": origin + "/mcp",
                "allow_private": True,
                "auth": {
                    "type": "oauth",
                    "issuer": origin,
                    "client_id": "synthetic-client",
                    "scopes": ["read"],
                },
                "actions": ["discover", "call"],
                "tools": ["lookup"],
                "read_only_tools": ["lookup"],
                "enabled": True,
            },
        },
    )
    yield service
    service.close()


def begin(app, server, actor=None):
    result, cookie = app.oauth.start("remote", actor, "http://localhost:5004")
    query = parse_qs(urlsplit(result["authorization_url"]).query)
    return (
        result,
        cookie,
        {"state": query["state"], "code": ["synthetic-code"], "iss": [server["origin"]]},
        query,
    )


def connect(app, server, actor=None):
    result, cookie, callback, query = begin(app, server, actor)
    assert app.oauth.callback(callback, cookie, actor)["status"] == "connected"
    return result, cookie, callback, query


def expire(app, actor=None):
    key = app.oauth.key("remote", actor)
    with app.lock:
        grant = app.oauth.read("delegated-credentials", key)
        tokens = unseal(key, grant["envelope"])
        tokens["expires_at"] = time.time() - 1
        grant["envelope"] = seal(key, tokens)
        app.oauth.write("delegated-credentials", key, grant)


def test_consent_pkce_audience_encryption_replay_disconnect(app, authorization_server):
    result, cookie, callback, query = connect(app, authorization_server)
    posts = [value for method, value in authorization_server["requests"] if method == "POST"]
    verifier = posts[0]["code_verifier"][0]
    assert query["code_challenge"] == [
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    ]
    assert query["resource"] == posts[0]["resource"] == [authorization_server["origin"] + "/mcp"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert app.oauth.headers("remote") == {"Authorization": "Bearer synthetic-access"}
    assert app.oauth.status("remote")["connected"]
    persisted = json.dumps(
        app.records("delegated-credentials", limit=None) + app.records("oauth-requests", limit=None)
    )
    assert all(
        secret not in persisted
        for secret in ["synthetic-access", "synthetic-code", verifier, "rotated-0"]
    )
    with pytest.raises(ValueError, match="already used"):
        app.oauth.callback(callback, cookie)
    app.oauth.disconnect("remote")
    with pytest.raises(PermissionError, match="Connect"):
        app.oauth.headers("remote")
    assert not app.oauth.read("delegated-credentials", app.oauth.key("remote", None))["envelope"]


@pytest.mark.parametrize(
    "failure",
    ["cookie", "session", "issuer", "missing-issuer", "duplicate-state", "expired", "changed"],
)
def test_callback_rejects_before_token_exchange(app, authorization_server, failure):
    actor = {"nexus_id": "alice", "session_id": "a" * 32}
    result, cookie, callback, query = begin(app, authorization_server, actor)
    if failure == "cookie":
        cookie = ""
    if failure == "session":
        actor = {**actor, "session_id": "b" * 32}
    if failure == "issuer":
        callback["iss"] = [authorization_server["origin"] + "/"]
    if failure == "missing-issuer":
        callback.pop("iss")
    if failure == "duplicate-state":
        callback["state"] *= 2
    if failure == "expired":
        pending = app.oauth.read("oauth-requests", result["flow_id"])
        pending["expires_at"] = 0
        app.oauth.write("oauth-requests", result["flow_id"], pending)
    if failure == "changed":
        record = app.catalog.get("connectors", "remote")
        record["data"]["auth"]["client_id"] = "other"
        app._write(app.catalog._path("connectors", "remote"), record)
    with pytest.raises((ValueError, PermissionError)):
        app.oauth.callback(callback, cookie, actor)
    assert not [item for item in authorization_server["requests"] if item[0] == "POST"]


@pytest.mark.parametrize("mode", ["failure", "redirect", "scope-expansion"])
def test_failed_exchange_consumes_flow_without_leaking_error(app, authorization_server, mode):
    result, cookie, callback, query = begin(app, authorization_server)
    authorization_server["mode"] = mode
    with pytest.raises(ValueError) as error:
        app.oauth.callback(callback, cookie)
    assert "DO_NOT_LEAK" not in str(error.value)
    assert app.oauth.read("oauth-requests", result["flow_id"])["status"] == "failed"
    with pytest.raises(ValueError, match="already used"):
        app.oauth.callback(callback, cookie)
    assert len([v for m, v in authorization_server["requests"] if m == "POST"]) == 1


def test_oidc_discovery_and_issuer_exactness(app, authorization_server):
    authorization_server["mode"] = "oidc"
    connect(app, authorization_server)
    assert any("openid-configuration" in str(p) for _, p in authorization_server["requests"])
    authorization_server["mode"] = "wrong-issuer"
    with pytest.raises(ValueError, match="issuer mismatch"):
        begin(app, authorization_server)


def test_refresh_once_concurrently_and_failed_rotation_is_not_replayed(app, authorization_server):
    connect(app, authorization_server)
    expire(app)
    other = (
        StudioService(
            app.root,
            state_options={
                "url": str(app.sql_state.engine.url),
                "namespace": app.sql_state.namespace,
            },
        )
        if app.sql_state
        else app
    )
    try:
        with ThreadPoolExecutor(4) as pool:
            results = list(
                pool.map(
                    lambda index: (app if index % 2 else other).oauth.headers("remote"), range(4)
                )
            )
    finally:
        if other is not app:
            other.close()
    assert results == [{"Authorization": "Bearer synthetic-access"}] * 4
    assert authorization_server["refreshes"] == 1
    expire(app)
    authorization_server["mode"] = "failure"
    with pytest.raises(PermissionError, match="refresh failed"):
        app.oauth.headers("remote")
    authorization_server["mode"] = "ok"
    with pytest.raises(PermissionError, match="Connect"):
        app.oauth.headers("remote")
    assert app.oauth.status("remote")["status"] == "reconnect_required"
    assert authorization_server["refreshes"] == 1


def test_disconnect_fences_inflight_callback(app, authorization_server):
    result, cookie, callback, query = begin(app, authorization_server)
    authorization_server.update(entered=threading.Event(), release=threading.Event())
    with ThreadPoolExecutor() as pool:
        future = pool.submit(app.oauth.callback, callback, cookie)
        assert authorization_server["entered"].wait(3)
        app.oauth.disconnect("remote")
        authorization_server["release"].set()
        with pytest.raises(ValueError, match="invalidated"):
            future.result(5)
    assert not app.oauth.status("remote")["connected"]


def test_owner_scope_is_not_shareable_and_binding_ignores_activation(app, authorization_server):
    connect(app, authorization_server, {"nexus_id": "alice", "session_id": "a"})
    with pytest.raises(PermissionError, match="Connect"):
        app.oauth.headers("remote", {"nexus_id": "bob", "role": "owner", "session_id": "b"})
    data = app.catalog.get("connectors", "remote")["data"]
    assert fingerprint(data) == fingerprint({**data, "enabled": False, "tools": []})
    assert fingerprint(data) != fingerprint({**data, "url": data["url"] + "/other"})
    key = app.oauth.key("remote", "alice")
    grant = app.oauth.read("delegated-credentials", key)
    with pytest.raises(PermissionError):
        unseal(app.oauth.key("remote", "bob"), grant["envelope"])


@pytest.mark.parametrize(
    "auth",
    [
        {"issuer": "http://example.com", "client_id": "id"},
        {"issuer": "https://example.com", "client_id": "id", "token": "secret"},
        {"issuer": "https://example.com", "client_id": "id", "scopes": ["bad scope"]},
        {
            "issuer": "https://example.com",
            "client_id": "id",
            "endpoint_origins": ["https://example.com/path"],
        },
    ],
)
def test_oauth_configuration_validation(auth):
    with pytest.raises(ValueError):
        validate_connection(
            {
                "type": "mcp",
                "transport": "sse",
                "url": "https://mcp.example.com",
                "auth": {"type": "oauth", **auth},
            }
        )
    with pytest.raises(ValueError, match="selected MCP server origin"):
        validate_connection(
            {
                "type": "mcp",
                "transport": "sse",
                "url": "https://mcp.example.com",
                "auth": {
                    "type": "oauth",
                    "issuer": "https://issuer.example.com",
                    "client_id": "id",
                    "resource": "https://other.example.com/mcp",
                },
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
async def test_real_mcp_discovery_selected_tools_and_no_failed_mutation_replay(
    app, authorization_server, transport
):
    record = app.catalog.get("connectors", "remote")
    record["data"].update(
        transport=transport,
        url=authorization_server["origin"] + ("/sse" if transport == "sse" else "/mcp"),
    )
    app._write(app.catalog._path("connectors", "remote"), record)
    connect(app, authorization_server)
    tools = await app.connections.execute("remote", {"action": "discover"})
    assert [tool["name"] for tool in tools] == ["lookup"]
    output = await app.connections.execute("remote", {"action": "call", "tool": "lookup"})
    assert output == "cited fixture"
    with pytest.raises(PermissionError, match="not enabled"):
        await app.connections.execute(
            "remote", {"action": "call", "tool": "not-selected", "confirm": True}
        )
    authorization_server["mode"] = "tool-error"
    with pytest.raises(ValueError, match="Integration action failed"):
        await app.connections.execute("remote", {"action": "call", "tool": "lookup"})
    assert len(authorization_server["calls"]) == 2
    assert all(auth == "Bearer synthetic-access" for _, auth in authorization_server["mcp"])


def test_workspace_backup_omits_both_private_collections(app, authorization_server, tmp_path):
    from nexus.core.studio_backup import create_snapshot, restore_snapshot

    connect(app, authorization_server)
    begin(app, authorization_server)
    destination = tmp_path.parent / (tmp_path.name + "-snapshot")
    create_snapshot(app.root, destination, quiesced=True, sql_state=app.sql_state)
    assert not (destination / "state" / "delegated-credentials").exists()
    assert not (destination / "state" / "oauth-requests").exists()
    restored = restore_snapshot(destination, tmp_path.parent / (tmp_path.name + "-restored"))
    copy = StudioService(restored["root"], state_dir=restored["state_dir"])
    try:
        assert copy.oauth.status("remote")["status"] == "disconnected"
        assert (
            copy.catalog.get("connectors", "remote")["data"]["auth"]["client_id"]
            == "synthetic-client"
        )
    finally:
        copy.close()


def test_http_cookie_csrf_and_callback_routes(app, authorization_server):
    import httpx

    from nexus.studio import make_studio_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(app, "synthetic-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        path = "/api/studio/connectors/remote/oauth"
        with httpx.Client(base_url=base, trust_env=False) as browser:
            assert browser.post(path + "/start", json={}).status_code == 403
            response = browser.post(
                path + "/start", json={}, headers={"X-Nexus-Studio-Token": "synthetic-csrf"}
            )
            assert response.status_code == 200, response.text
            assert "HttpOnly" in response.headers["set-cookie"]
            params = parse_qs(urlsplit(response.json()["authorization_url"]).query)
            assert params["redirect_uri"] == [base + "/api/studio/oauth/callback"]
            callback = {
                "state": params["state"][0],
                "iss": authorization_server["origin"],
                "code": "synthetic",
            }
            result = browser.get("/api/studio/oauth/callback", params=callback)
            assert result.status_code == 200, result.text
            assert browser.get(path).json()["connected"]
            result = browser.post(
                path + "/disconnect", json={}, headers={"X-Nexus-Studio-Token": "synthetic-csrf"}
            )
            assert result.status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_missing_encryption_key_does_not_start_network(app, authorization_server, monkeypatch):
    monkeypatch.delenv("NEXUS_CREDENTIALS_MASTER_KEY")
    with pytest.raises(ValueError, match="MASTER_KEY"):
        begin(app, authorization_server)
    assert not authorization_server["requests"]


def test_interrupted_refresh_survives_restart(app, authorization_server):
    connect(app, authorization_server)
    key = app.oauth.key("remote", None)
    with app.lock:
        grant = app.oauth.read("delegated-credentials", key)
        grant.update(status="refreshing", receipt="interrupted")
        app.oauth.write("delegated-credentials", key, grant)
    assert app.oauth.status("remote")["status"] == "refreshing"
    # A new controller observes the committed uncertain claim, never refreshes it.
    options = (
        {
            "state_options": {
                "url": str(app.sql_state.engine.url),
                "namespace": app.sql_state.namespace,
            }
        }
        if app.sql_state
        else {}
    )
    copy = StudioService(app.root, **options)
    try:
        assert copy.oauth.status("remote")["status"] == "refreshing"
        posts = len([v for m, v in authorization_server["requests"] if m == "POST"])
        copy.oauth.disconnect("remote")
        with pytest.raises(PermissionError, match="Connect"):
            copy.oauth.headers("remote")
        assert len([v for m, v in authorization_server["requests"] if m == "POST"]) == posts
    finally:
        copy.close()


def test_team_session_ownership_revocation_and_no_cross_user_grant(
    team, authorization_server, monkeypatch
):
    app, policy, manager, request, _ = team
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", base64.b64encode(b"s" * 32).decode())
    origin = authorization_server["origin"]
    app.catalog.save(
        "connectors",
        {
            "id": "remote",
            "name": "Remote",
            "requester_nexus_id": "alice",
            "data": {
                "type": "mcp",
                "transport": "streamable-http",
                "url": origin + "/mcp",
                "allow_private": True,
                "auth": {
                    "type": "oauth",
                    "issuer": origin,
                    "client_id": "synthetic-client",
                    "scopes": ["read"],
                },
            },
        },
    )
    route = "/api/studio/connectors/remote/oauth"
    assert request(route + "/start", {}, user="bob")[0] == 403
    assert request(route + "/start", {}, user="viewer")[0] == 403
    assert request(route + "/start", {}, user="alice", csrf="incorrect")[0] == 403
    alice = {"nexus_id": "alice", "session_id": "a" * 32}
    result, cookie, callback, query = begin(app, authorization_server, alice)
    # HTTP start uses the configured HTTPS origin, never the proxy request host.
    status, started = request(route + "/start", {}, user="alice")
    assert status == 200
    assert parse_qs(urlsplit(started["authorization_url"]).query)["redirect_uri"] == [
        policy.public_url + "/api/studio/oauth/callback"
    ]
    with pytest.raises(PermissionError, match="session mismatch"):
        app.oauth.callback(callback, cookie, {"nexus_id": "owner", "session_id": "d" * 32})
    app.oauth.callback(callback, cookie, alice)
    assert request(route, user="owner")[1]["status"] == "disconnected"
    assert app.oauth.headers("remote", "alice")["Authorization"] == "Bearer synthetic-access"
    with pytest.raises(PermissionError, match="Connect"):
        app.oauth.headers("remote", "owner")
    assert request(route + "/disconnect", {}, user="owner")[0] == 200
    assert app.oauth.status("remote", alice)["connected"]
    manager.revoked.add("alice")
    with pytest.raises(PermissionError, match="revoked"):
        app.oauth.headers("remote", "alice")
    assert request(route, user="alice")[0] == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
@pytest.mark.parametrize("auth_type", ["bearer", "api-key", "basic"])
async def test_real_remote_auth_matrix_and_explicit_reconnect(
    app, authorization_server, monkeypatch, transport, auth_type
):
    monkeypatch.setenv("MCP_FIXTURE_TOKEN", "synthetic-access")
    monkeypatch.setenv("MCP_FIXTURE_USER", "synthetic-user")
    monkeypatch.setenv("MCP_FIXTURE_PASSWORD", "synthetic-password")
    auth = {"type": auth_type, "token_env": "MCP_FIXTURE_TOKEN"}
    if auth_type == "basic":
        auth = {
            "type": "basic",
            "username_env": "MCP_FIXTURE_USER",
            "password_env": "MCP_FIXTURE_PASSWORD",
        }
    elif auth_type == "api-key":
        auth["header"] = "X-Service-Key"
    expected = {
        "bearer": {"Authorization": "Bearer synthetic-access"},
        "basic": {
            "Authorization": "Basic "
            + base64.b64encode(b"synthetic-user:synthetic-password").decode()
        },
        "api-key": {"X-Service-Key": "synthetic-access"},
    }[auth_type]
    record = app.catalog.get("connectors", "remote")
    record["data"] = validate_connection(
        {
            **record["data"],
            "transport": transport,
            "url": authorization_server["origin"] + ("/sse" if transport == "sse" else "/mcp"),
            "auth": auth,
        }
    )
    app._write(app.catalog._path("connectors", "remote"), record)
    authorization_server["expected_headers"] = expected
    assert [
        tool["name"] for tool in await app.connections.execute("remote", {"action": "discover"})
    ] == ["lookup"]
    authorization_server["expected_headers"] = {"Authorization": "rejected-account"}
    with pytest.raises(ValueError, match="Integration action failed"):
        await app.connections.execute("remote", {"action": "call", "tool": "lookup"})
    assert not authorization_server.get("calls")
    authorization_server["expected_headers"] = expected
    assert (
        await app.connections.execute("remote", {"action": "call", "tool": "lookup"})
        == "cited fixture"
    )
    assert len(authorization_server["calls"]) == 1
    assert not [value for method, value in authorization_server["requests"] if method == "POST"]


def test_local_studio_does_not_initialize_bot_auth_configuration(tmp_path):
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nexus.core.studio import StudioService; import sys; app = StudioService(sys.argv[1]); assert 'nexus.core.auth.manager' not in sys.modules; app.close()",
            str(tmp_path / "local"),
        ],
        env={key: value for key, value in os.environ.items() if key != "PROJECT_CONFIG_PATH"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_permission_change_during_refresh_does_not_launch_old_tool(app, authorization_server):
    connect(app, authorization_server)
    expire(app)
    expected = app.catalog.get("connectors", "remote")["data"]
    authorization_server.update(entered=threading.Event(), release=threading.Event())
    with ThreadPoolExecutor() as pool:
        future = pool.submit(app.oauth.headers, "remote", expected=expected)
        assert authorization_server["entered"].wait(3)
        record = app.catalog.get("connectors", "remote")
        record["data"]["enabled"] = False
        app._write(app.catalog._path("connectors", "remote"), record)
        authorization_server["release"].set()
        with pytest.raises(PermissionError, match="permissions changed"):
            future.result(5)
    assert app.oauth.status("remote")["connected"]
    assert not authorization_server.get("calls")


@pytest.mark.asyncio
async def test_independent_stdio_server_uses_selected_tools_and_minimal_credentials(
    app, tmp_path, monkeypatch
):
    import sys

    monkeypatch.setenv("MCP_STDIO_TOKEN", "synthetic-stdio-key")
    monkeypatch.setenv("UNRELATED_HOST_ACCOUNT_SECRET", "not-for-this-server")
    script = tmp_path / "mcp_fixture.py"
    calls = tmp_path / "stdio-calls"
    script.write_text("""import json,os,sys
assert os.environ.get("ACCOUNT_KEY") == "synthetic-stdio-key"
assert "UNRELATED_HOST_ACCOUNT_SECRET" not in os.environ
for line in sys.stdin:
 message=json.loads(line)
 if "id" not in message: continue
 method=message["method"]
 if method=="initialize": result={"protocolVersion":message["params"]["protocolVersion"],"capabilities":{"tools":{}},"serverInfo":{"name":"independent-stdio-fixture","version":"1"}}
 elif method=="tools/list": result={"tools":[{"name":"lookup","description":"Fixture","inputSchema":{"type":"object"}}]}
 elif method=="tools/call":
  with open(sys.argv[1],"a") as output: output.write("called\\n")
  result={"content":[{"type":"text","text":"stdio evidence"}],"isError":False}
 else: raise AssertionError(method)
 print(json.dumps({"jsonrpc":"2.0","id":message["id"],"result":result}),flush=True)
""")
    app.catalog.save(
        "connectors",
        {
            "name": "Stdio contract",
            "data": {
                "type": "mcp",
                "command": sys.executable,
                "args": [str(script), str(calls)],
                "env": {"ACCOUNT_KEY": "MCP_STDIO_TOKEN"},
                "tools": ["lookup"],
                "read_only_tools": ["lookup"],
                "actions": ["discover", "call"],
                "enabled": True,
            },
        },
    )
    assert [
        t["name"] for t in await app.connections.execute("stdio-contract", {"action": "discover"})
    ] == ["lookup"]
    assert (
        await app.connections.execute("stdio-contract", {"action": "call", "tool": "lookup"})
        == "stdio evidence"
    )
    assert calls.read_text() == "called\n"
