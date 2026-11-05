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


def _existing_subscription_user(web, client):
    from nexus.core.auth import oauth_onboarding_domain as auth
    from nexus.core.auth.credential_store import upsert_github_credentials

    upsert_github_credentials(nexus_id="subscription-owner", github_user_id=456, github_login="existing",
                             github_token_enc="synthetic-private-token", org_verified=True)
    sid = auth.create_login_session_for_user(nexus_id="subscription-owner", discord_user_id="", discord_username=None)
    auth.update_auth_session(session_id=sid, status="oauth_done")
    client.set_cookie(web._WEB_SESSION_COOKIE_NAME, sid)
    return sid


def test_login_offers_three_subscription_connections(browser_routes):
    web, _ = browser_routes
    page = web.app.test_client().get("/login?next=/workspace")
    for label in ("Codex", "Claude", "Antigravity"):
        assert f"Connect {label}".encode() in page.data
    assert b"Sign in to Nexus first" in page.data
    assert b"/auth/subscription?" in page.data


@pytest.mark.parametrize("provider", ["codex", "claude", "agy"])
def test_subscription_choice_survives_identity_login_before_ai_setup(browser_routes, monkeypatch, provider):
    web, _ = browser_routes
    client = web.app.test_client()
    first = client.get("/auth/subscription", query_string={"provider": provider, "next": "/workspace"})
    assert first.status_code == 302
    destination = parse_qs(urlsplit(first.headers["Location"]).query)["next"][0]
    assert parse_qs(urlsplit(destination).query)["provider"] == [provider]
    monkeypatch.setattr(web, "_svc_get_session_and_setup_status", lambda _: {"setup": {"ready": False}})
    state = start(client, "gitlab", next=destination)
    done = client.get("/auth/gitlab/callback", query_string={"state": state, "code": "synthetic"})
    assert done.status_code == 303
    assert done.headers["Location"] == destination


@pytest.mark.parametrize("provider,label", [("codex", "Codex"), ("claude", "Claude"), ("agy", "Antigravity")])
def test_connection_page_uses_cookie_owner_and_never_starts_cli(browser_routes, monkeypatch, provider, label):
    web, _ = browser_routes
    client = web.app.test_client()
    sid = _existing_subscription_user(web, client)
    monkeypatch.setattr(web._subscription_accounts.shutil, "which", lambda _: "/synthetic/cli")
    monkeypatch.setattr(web._browser_login.onboarding.subprocess, "Popen", lambda *a, **kw: pytest.fail("Viewing must not start CLI"))
    response = client.get("/auth/subscription", query_string={"provider": provider, "session": "someone-else", "next": "//evil.test"})
    assert response.status_code == 200
    assert f"Connect {label}".encode() in response.data
    assert b"someone-else" not in response.data
    assert web._svc_format_login_session_ref(sid).encode() in response.data
    assert b"synthetic-private-token" not in response.data
    assert response.headers["Cache-Control"] == "no-store"
    assert b"use_codex_account" not in response.data  # No unsaved Use switch on the standalone page.
    assert b"next=%2F" in response.data
    assert b"No API key is needed" in response.data


def test_missing_cli_disables_connection_with_explanation(browser_routes, monkeypatch):
    web, _ = browser_routes
    client = web.app.test_client()
    _existing_subscription_user(web, client)
    monkeypatch.setattr(web._subscription_accounts.shutil, "which", lambda _: None)
    response = client.get("/auth/subscription?provider=claude")
    assert response.status_code == 200
    assert b"Install the Claude CLI on the Studio server" in response.data
    assert b"startProviderConnect('claude')\" disabled" in response.data


def test_subscription_page_rejects_expired_session_and_bad_provider(browser_routes):
    web, _ = browser_routes
    client = web.app.test_client()
    sid = _existing_subscription_user(web, client)
    web._browser_login.onboarding.update_auth_session(session_id=sid, status="expired")
    assert client.get("/auth/subscription?provider=codex").status_code == 302
    assert client.get("/auth/subscription?provider=shell").status_code == 400


def test_subscription_page_respects_disabled_auth(browser_routes, monkeypatch):
    web, _ = browser_routes
    monkeypatch.setattr(web, "NEXUS_AUTH_ENABLED", False)
    assert web.app.test_client().get("/auth/subscription?provider=codex").status_code == 404


def test_subscription_service_failure_is_sanitized(browser_routes, monkeypatch):
    web, _ = browser_routes

    def unavailable(**kwargs):
        raise RuntimeError("private-account-store-detail")

    monkeypatch.setattr(web._subscription_accounts, "subscription_account_context", unavailable)
    response = web.app.test_client().get("/auth/subscription?provider=codex")
    assert response.status_code == 503
    assert b"Account service unavailable" in response.data
    assert b"private-account-store-detail" not in response.data
    assert response.headers["Cache-Control"] == "no-store"
