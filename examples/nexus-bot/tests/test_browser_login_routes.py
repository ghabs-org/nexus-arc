"""Real route/cookie/state checks; upstream exchanges are tested independently."""

import base64
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa


@pytest.fixture
def browser_routes(monkeypatch, tmp_path):
    import webhook_server as web

    from nexus.core.auth import browser_login as login
    from nexus.core.auth import credential_store as store
    from nexus.core.auth import oauth_onboarding_domain as auth

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'accounts.db'}")
    store._AuthBase.metadata.create_all(engine)
    monkeypatch.setattr(store, "_get_engine", lambda: engine)
    # SQLite strips time zones; production auth uses PostgreSQL timestamptz.
    monkeypatch.setattr(auth, "_now_utc", lambda: datetime.now(UTC).replace(tzinfo=None))
    monkeypatch.setattr(web, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(web, "NEXUS_PUBLIC_BASE_URL", "http://localhost")
    monkeypatch.setattr(web, "_WEB_SESSION_COOKIE_SECURE", False)
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("NEXUS_PUBLIC_BASE_URL", "http://localhost")
    for provider in login.PROVIDERS:
        monkeypatch.setenv(f"NEXUS_{provider.upper()}_CLIENT_ID", provider + "-app")
        monkeypatch.setenv(f"NEXUS_{provider.upper()}_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv("NEXUS_META_APP_ID", "meta-app")
    monkeypatch.setenv("NEXUS_META_APP_SECRET", "synthetic-secret")
    monkeypatch.setattr(web, "_svc_get_session_and_setup_status", lambda sid: {
        "exists": bool(sid), "session_id": sid, "setup": {"ready": True},
    })
    exchanges = []

    def complete(provider):
        def exchange(*, code, state):
            record = auth._assert_valid_callback_session(state, provider)
            exchanges.append((provider, code))
            auth.update_auth_session(session_id=record.session_id, nexus_id="canonical", status="oauth_done")
            return {"session_id": record.session_id, "nexus_id": "canonical", "provider": provider}
        return exchange

    for provider in login.PROVIDERS:
        monkeypatch.setattr(auth, f"complete_{provider}_oauth", complete(provider))
    monkeypatch.setattr(web, "_notify_onboarding_message", lambda **kw: pytest.fail("Browser login must not send chat messages"))
    yield web, exchanges
    engine.dispose()


def start(client, provider="github", **extra):
    response = client.post("/auth/browser/start", data={"provider": provider, "next": "/studio", **extra})
    assert response.status_code == 303
    assert "no-store" in response.headers["Cache-Control"]
    assert "HttpOnly" in response.headers["Set-Cookie"]
    assert "SameSite=Lax" in response.headers["Set-Cookie"]
    assert "Path=/auth" in response.headers["Set-Cookie"]
    return parse_qs(urlsplit(response.headers["Location"]).query)["state"][0]


def test_login_page_shows_all_configured_identity_providers(browser_routes):
    web, _ = browser_routes
    response = web.app.test_client().get("/login")
    assert response.status_code == 200
    for label in ("GitHub", "GitLab", "LinkedIn", "X", "Meta"):
        assert f"Continue with {label}".encode() in response.data
    assert b'<details class="login-reference">' in response.data
    assert b'action="/login"' in response.data


def test_browser_service_stores_real_attempt(browser_routes):
    web, _ = browser_routes
    result = web._browser_login.start_browser_login(provider="github")
    assert result["context"]


@pytest.mark.parametrize("provider", ["github", "gitlab", "linkedin", "x", "meta"])
def test_callback_sets_authenticated_cookie_and_local_redirect(browser_routes, provider):
    web, exchanges = browser_routes
    client = web.app.test_client()
    state = start(client, provider)
    response = client.get(f"/auth/{provider}/callback", query_string={"state": state, "code": "synthetic-code"})
    assert response.status_code == 303
    assert response.headers["Location"] == "/studio"
    cookies = response.headers.getlist("Set-Cookie")
    assert any(web._WEB_SESSION_COOKIE_NAME + "=" in cookie for cookie in cookies)
    assert any("nexus_browser_login=" in cookie and "Max-Age=0" in cookie for cookie in cookies)
    assert exchanges == [(provider, "synthetic-code")]
    replay = client.get(f"/auth/{provider}/callback", query_string={"state": state, "code": "synthetic-code"})
    assert replay.status_code == 400
    assert len(exchanges) == 1


def test_foreign_browser_cannot_exchange_callback(browser_routes):
    web, exchanges = browser_routes
    state = start(web.app.test_client())
    response = web.app.test_client().get("/auth/github/callback", query_string={"state": state, "code": "stolen-code"})
    assert response.status_code == 400
    assert not exchanges
    assert not any(web._WEB_SESSION_COOKIE_NAME + "=" in c for c in response.headers.getlist("Set-Cookie"))


def test_cross_origin_login_and_unconfigured_provider_rejected(browser_routes, monkeypatch):
    web, exchanges = browser_routes
    client = web.app.test_client()
    assert client.post("/auth/browser/start", data={"provider": "github"}, headers={"Origin": "https://evil.test"}).status_code == 403
    monkeypatch.delenv("NEXUS_GITHUB_CLIENT_SECRET")
    assert client.post("/auth/browser/start", data={"provider": "github"}).status_code == 400
    assert b"Continue with GitHub" not in client.get("/login").data
    assert not exchanges


def test_denial_expires_attempt_without_exchange_or_session_cookie(browser_routes):
    web, exchanges = browser_routes
    client = web.app.test_client()
    state = start(client)
    response = client.get("/auth/github/callback", query_string={"state": state, "error": "access_denied"})
    assert response.status_code == 400
    assert b"cancelled" in response.data
    assert not exchanges
    assert web._svc_get_auth_session_by_state(state) is None


def test_incomplete_setup_stays_on_account_page(browser_routes, monkeypatch):
    web, exchanges = browser_routes
    monkeypatch.setattr(web, "_svc_get_session_and_setup_status", lambda sid: {"setup": {"ready": False}})
    client = web.app.test_client()
    state = start(client, session="must-not-reuse-this")
    response = client.get("/auth/github/callback", query_string={"state": state, "code": "synthetic-code"})
    assert response.status_code == 200
    assert b"Account setup" in response.data
    assert b"Other sign-in accounts" in response.data
    assert "Location" not in response.headers
    assert exchanges == [("github", "synthetic-code")]


def test_login_disabled_blocks_direct_start(browser_routes, monkeypatch):
    web, _ = browser_routes
    monkeypatch.setattr(web, "NEXUS_AUTH_ENABLED", False)
    assert web.app.test_client().post("/auth/browser/start", data={"provider": "github"}).status_code == 404


def test_browser_account_can_connect_social_without_reusing_login_attempt(browser_routes):
    web, _ = browser_routes
    store = web._browser_login.onboarding
    from nexus.core.auth.credential_store import upsert_github_credentials

    upsert_github_credentials(nexus_id="canonical", github_user_id=123, github_login="owner",
                             github_token_enc="synthetic", org_verified=True)
    client = web.app.test_client()
    state = start(client)
    response = client.get("/auth/github/callback", query_string={"state": state, "code": "synthetic"})
    assert response.status_code == 303
    original = store.get_auth_session_by_state(state)
    connection = client.get("/auth/start", query_string={"session": original.session_id, "provider": "linkedin"})
    assert connection.status_code == 303
    new_state = parse_qs(urlsplit(connection.headers["Location"]).query)["state"][0]
    assert new_state != state
    linked = store.get_auth_session_by_state(new_state)
    assert linked.nexus_id == "canonical" and linked.chat_platform == "web-connect"
    done = client.get("/auth/linkedin/callback", query_string={"state": new_state, "code": "synthetic"})
    assert done.status_code == 303
    assert done.headers["Location"].startswith("/auth/setup?session=")
    assert store.get_auth_session(original.session_id).status == "oauth_done"


def test_https_marks_both_context_and_authenticated_cookies_secure(browser_routes, monkeypatch):
    web, _ = browser_routes
    monkeypatch.setattr(web, "_WEB_SESSION_COOKIE_SECURE", True)
    client = web.app.test_client()
    response = client.post("/auth/browser/start", base_url="https://localhost", data={"provider": "github"})
    assert "Secure" in response.headers["Set-Cookie"]
    state = parse_qs(urlsplit(response.headers["Location"]).query)["state"][0]
    done = client.get("/auth/github/callback", base_url="https://localhost", query_string={"state": state, "code": "synthetic"})
    assert done.status_code == 303
    assert all("Secure" in cookie and "HttpOnly" in cookie for cookie in done.headers.getlist("Set-Cookie"))
