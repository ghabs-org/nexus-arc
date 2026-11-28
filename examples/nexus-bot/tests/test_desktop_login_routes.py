"""Real route, browser cookie and PKCE exchange contract; no provider account needed."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from test_browser_login_routes import browser_routes as _browser_routes
from test_browser_login_routes import start

from nexus.core.auth import credential_store as store
from nexus.core.auth import desktop_login as login

# Reuse the existing real auth-route fixture in this module.
browser_routes = _browser_routes


@pytest.fixture
def desktop_routes(browser_routes, monkeypatch):
    web, _ = browser_routes
    monkeypatch.setenv("NEXUS_GITHUB_CALLBACK_URL", "https://studio.example/auth/github/callback")
    store.upsert_github_credentials(
        nexus_id="canonical",
        github_user_id=123,
        github_login="test-owner",
        github_token_enc="fixture-token",
        org_verified=True,
    )
    client = web.app.test_client()
    response = client.post(
        "/auth/browser/start", base_url="https://studio.example", data={"provider": "github"}
    )
    state = parse_qs(urlsplit(response.location).query)["state"][0]
    client.get(
        "/auth/github/callback",
        base_url="https://studio.example",
        query_string={"state": state, "code": "fixture"},
    )
    return web, client


def begin(client):
    request = {
        "redirect_uri": "http://127.0.0.1:19234/nexus/desktop/callback",
        "state": "s" * 43,
        "code_challenge": login.pkce_challenge("v" * 43),
        "device_name": "Test Mac",
    }
    response = client.post("/auth/desktop/start", json=request)
    assert response.status_code == 200
    return response.json["attempt_id"], request


def test_routes_confirm_exchange_inspect_and_revoke(desktop_routes):
    web, client = desktop_routes
    identity, request = begin(client)
    # A public attempt ID alone cannot authorize or read a device session.
    foreign = web.app.test_client()
    response = foreign.get("/auth/desktop/authorize", query_string={"attempt_id": identity})
    assert response.status_code == 302 and response.location.startswith("/login?")
    page = client.get(
        "/auth/desktop/authorize",
        base_url="https://studio.example",
        query_string={"attempt_id": identity},
    )
    assert page.status_code == 200
    assert b"Test Mac" in page.data and b"test-owner" in page.data
    assert page.headers["Referrer-Policy"] == "same-origin"
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
    reply = client.post(
        "/auth/desktop/authorize",
        base_url="https://studio.example",
        data={"attempt_id": identity, "csrf": csrf, "decision": "allow"},
        headers={"Origin": "https://studio.example"},
    )
    assert reply.status_code == 303 and reply.headers["Referrer-Policy"] == "no-referrer"
    code = parse_qs(urlsplit(reply.location).query)["code"][0]
    exchange = {
        "attempt_id": identity,
        "code": code,
        "code_verifier": "v" * 43,
        "redirect_uri": request["redirect_uri"],
    }
    response = foreign.post("/auth/desktop/exchange", json=exchange)
    assert response.status_code == 200
    token = response.json["token"]
    assert foreign.post("/auth/desktop/exchange", json=exchange).status_code == 400
    headers = {"Authorization": "Bearer " + token}
    assert foreign.get("/auth/desktop/session", headers=headers).json["account"] == "test-owner"
    assert foreign.delete("/auth/desktop/session", headers=headers).status_code == 204
    assert foreign.get("/auth/desktop/session", headers=headers).status_code == 401


def test_cross_origin_native_and_browser_requests_rejected(desktop_routes):
    _, client = desktop_routes
    identity, _ = begin(client)
    assert (
        client.post(
            "/auth/desktop/start", json={}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post("/auth/desktop/exchange", json={}, headers={"Origin": "null"}).status_code
        == 403
    )
    response = client.post(
        "/auth/desktop/authorize",
        data={"attempt_id": identity, "decision": "allow"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_auth_disabled_and_bounded_json(desktop_routes, monkeypatch):
    web, client = desktop_routes
    assert client.post("/auth/desktop/start", data="not json").status_code == 400
    assert client.post("/auth/desktop/start", json={"data": "x" * 5000}).status_code == 400
    monkeypatch.setattr(web, "NEXUS_AUTH_ENABLED", False)
    assert client.post("/auth/desktop/start", json={}).status_code == 404


def test_login_can_return_to_desktop_before_ai_setup(browser_routes, monkeypatch):
    web, _ = browser_routes
    monkeypatch.setattr(
        web, "_svc_get_session_and_setup_status", lambda sid: {"setup": {"ready": False}}
    )
    client = web.app.test_client()
    state = start(client, next="/auth/desktop/authorize?attempt_id=fixture")
    response = client.get("/auth/github/callback", query_string={"state": state, "code": "fixture"})
    assert response.status_code == 303
    assert response.location == "/auth/desktop/authorize?attempt_id=fixture"
