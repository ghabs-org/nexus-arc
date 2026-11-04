"""Browser ownership, stable identity mapping and additive schema coverage."""

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa

from nexus.core.auth import browser_login as login
from nexus.core.auth import credential_store as store
from nexus.core.auth import oauth_onboarding_domain as auth


@pytest.fixture
def browser_auth(monkeypatch):
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("NEXUS_PUBLIC_BASE_URL", "https://nexus.example")
    for provider in login.PROVIDERS:
        monkeypatch.setenv(f"NEXUS_{provider.upper()}_CLIENT_ID", provider + "-app")
        monkeypatch.setenv(f"NEXUS_{provider.upper()}_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv("NEXUS_META_APP_ID", "meta-app")
    monkeypatch.setenv("NEXUS_META_APP_SECRET", "synthetic-secret")
    sessions = {}

    def create(**values):
        sid = f"attempt-{len(sessions)}"
        sessions[sid] = SimpleNamespace(
            session_id=sid, status="pending", expires_at=datetime.now(UTC) + timedelta(minutes=5),
            oauth_provider=None, oauth_state_hash=None, last_error="", **values,
        )
        return sid

    def update(session_id, **values):
        for key, value in values.items():
            setattr(sessions[session_id], key, value)

    monkeypatch.setattr(auth, "create_login_session_for_user", create)
    monkeypatch.setattr(auth, "get_auth_session", sessions.get)
    monkeypatch.setattr(auth, "get_auth_session_by_state", lambda state: next(
        (s for s in sessions.values() if s.oauth_state_hash == store.hash_oauth_state(state)), None,
    ))
    monkeypatch.setattr(auth, "update_auth_session", update)
    return sessions


def attempt(provider):
    result = login.start_browser_login(provider=provider, next_path="/workspace?view=agents")
    state = parse_qs(urlsplit(result["authorization_url"]).query)["state"][0]
    return result["context"], state


@pytest.mark.parametrize("provider", list(login.PROVIDERS))
def test_five_providers_have_browser_bound_attempts(browser_auth, provider):
    context, state = attempt(provider)
    payload = login.validate_browser_callback(context=context, state=state, provider=provider)
    assert payload["next"] == "/workspace?view=agents"
    record = browser_auth[payload["session_id"]]
    assert record.chat_platform == "web-login"
    assert record.discord_user_id == ""
    assert record.oauth_state_hash != state


@pytest.mark.parametrize("failure", ["absent", "tampered", "state", "provider", "expired", "used", "foreign"])
def test_callback_rejects_wrong_browser_state_provider_and_replay(browser_auth, failure):
    context, state = attempt("github")
    provider = "github"
    if failure == "absent":
        context = ""
    elif failure == "tampered":
        context = context[:10] + "!!!" + context[13:]
    elif failure == "state":
        state = "attacker-state"
    elif failure == "provider":
        provider = "gitlab"
    elif failure == "expired":
        browser_auth["attempt-0"].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif failure == "used":
        browser_auth["attempt-0"].status = "oauth_done"
    elif failure == "foreign":
        context, _ = attempt("github")
    with pytest.raises(ValueError, match="another browser"):
        login.validate_browser_callback(context=context, state=state, provider=provider)


def test_encrypted_context_has_its_own_expiry(browser_auth, monkeypatch):
    context, state = attempt("github")
    monkeypatch.setattr(login.time, "time", lambda: datetime.now(UTC).timestamp() + 901)
    with pytest.raises(ValueError):
        login.validate_browser_callback(context=context, state=state, provider="github")


@pytest.mark.parametrize("path", ["https://evil.test", "//evil.test", "/\\evil.test", "/%2fevil.test", "/%255cevil.test", "/a%0d%0aLocation:evil"])
def test_redirect_does_not_leave_origin(path):
    assert login.safe_return_path(path) == "/"


def test_meta_configuration_uses_existing_app_environment_names(browser_auth, monkeypatch):
    assert [p["id"] for p in login.configured_identity_providers()] == list(login.PROVIDERS)
    monkeypatch.delenv("NEXUS_META_APP_SECRET")
    assert "meta" not in [p["id"] for p in login.configured_identity_providers()]


def test_meta_version_is_explicit_and_cannot_change_endpoint_host(browser_auth, monkeypatch):
    monkeypatch.delenv("NEXUS_META_API_VERSION", raising=False)
    assert auth._meta_graph_url("me") == "https://graph.facebook.com/v26.0/me"
    monkeypatch.setenv("NEXUS_META_API_VERSION", "v25.0")
    assert auth._meta_graph_url("me") == "https://graph.facebook.com/v25.0/me"
    monkeypatch.setenv("NEXUS_META_API_VERSION", "//evil.test")
    with pytest.raises(ValueError, match="Graph version"):
        auth._meta_graph_url("me")


def test_start_does_not_create_attempt_for_unconfigured_provider(browser_auth, monkeypatch):
    monkeypatch.delenv("NEXUS_GITHUB_CLIENT_SECRET")
    with pytest.raises(ValueError, match="not configured"):
        login.start_browser_login(provider="github")
    assert not browser_auth


def test_context_encryption_failure_expires_attempt(browser_auth, monkeypatch):
    monkeypatch.delenv("NEXUS_CREDENTIALS_MASTER_KEY")
    with pytest.raises(ValueError):
        login.start_browser_login(provider="github")
    assert browser_auth["attempt-0"].status == "expired"
    assert browser_auth["attempt-0"].oauth_state_hash == ""


def test_social_login_does_not_create_or_reassign_accounts(browser_auth, monkeypatch):
    session = SimpleNamespace(session_id="linked", nexus_id="temporary", chat_platform="web-login")
    monkeypatch.setattr(auth, "get_login_identity", lambda **kw: None)
    with pytest.raises(ValueError, match="GitHub or GitLab first"):
        auth._resolve_social_login_identity(session, "x", "123")
    monkeypatch.setattr(auth, "get_login_identity", lambda **kw: "canonical")
    monkeypatch.setattr(auth, "get_user_credentials", lambda _: SimpleNamespace(github_user_id=123, gitlab_user_id=None))
    updates = []
    monkeypatch.setattr(auth, "update_auth_session", lambda **kw: updates.append(kw))
    assert auth._resolve_social_login_identity(session, "x", "123") == "canonical"
    assert updates[0]["nexus_id"] == "canonical"
    monkeypatch.setattr(auth, "get_user_credentials", lambda _: None)
    with pytest.raises(ValueError, match="no longer"):
        auth._resolve_social_login_identity(session, "x", "123")


def test_additive_identity_schema_preserves_credentials_and_refuses_reassignment(monkeypatch, tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'accounts.db'}")
    # Model an existing installation before the additive table is available.
    store._UserCredentialRow.__table__.create(engine)
    monkeypatch.setattr(store, "_get_engine", lambda: engine)
    store.upsert_github_credentials(nexus_id="owner", github_user_id=123, github_login="original",
                                    github_token_enc="original-secret", org_verified=True)
    store._AuthBase.metadata.create_all(engine)
    store.bind_login_identity(provider="x", client_id="app", subject="123", nexus_id="owner")
    store._AuthBase.metadata.create_all(engine)
    store.bind_login_identity(provider="x", client_id="app", subject="123", nexus_id="owner")
    with pytest.raises(ValueError, match="another Nexus"):
        store.bind_login_identity(provider="x", client_id="app", subject="123", nexus_id="attacker")
    assert store.get_login_identity(provider="x", client_id="app", subject="123") == "owner"
    assert store.get_login_identity(provider="x", client_id="other-app", subject="123") is None
    assert store.get_user_credentials("owner").github_token_enc == "original-secret"
    # Older code can read the unchanged credential table after rollback.
    assert store.get_user_credentials("owner").github_login == "original"
    engine.dispose()


@pytest.mark.parametrize("provider,expected", [("linkedin", "openid profile"), ("x", "tweet.read users.read"), ("meta", "public_profile")])
def test_social_login_requests_identity_scopes_only(browser_auth, provider, expected):
    result = login.start_browser_login(provider=provider)
    scope = parse_qs(urlsplit(result["authorization_url"]).query)["scope"][0]
    assert scope == expected


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_git_login_reuses_immutable_account_id_and_preserves_permissions(browser_auth, monkeypatch, provider):
    context, state = attempt(provider)
    calls = {}
    monkeypatch.setattr(auth, f"_{provider}_exchange_code_for_token", lambda _: {"access_token": "synthetic-token"})
    profile = {"id": 321, "login": "renamed", "username": "renamed"}
    monkeypatch.setattr(auth, f"_fetch_{provider}_profile", lambda _: profile)
    monkeypatch.setattr(auth, "_fetch_github_org_logins" if provider == "github" else "_fetch_gitlab_group_paths", lambda _: set())
    monkeypatch.setattr(auth, "_allowed_github_orgs" if provider == "github" else "_allowed_gitlab_groups", lambda: set())

    def lookup(**kw):
        calls["lookup"] = kw
        return SimpleNamespace(nexus_id="existing-owner")

    monkeypatch.setattr(auth, f"find_user_credentials_by_{provider}_identity", lookup)
    monkeypatch.setattr(auth, f"upsert_{provider}_credentials", lambda **kw: calls.update(credentials=kw))
    monkeypatch.setattr(auth, "sync_user_project_access" if provider == "github" else "sync_user_gitlab_project_access", lambda **kw: calls.update(grants=kw) or 2)
    result = login.complete_browser_login(context=context, state=state, code="authorization-code", provider=provider)
    assert result["nexus_id"] == "existing-owner"
    assert calls["lookup"][f"{provider}_user_id"] == 321
    assert calls["lookup"]["github_login" if provider == "github" else "gitlab_username"] is None
    assert calls["grants"]["nexus_id"] == "existing-owner"
    assert calls["credentials"]["nexus_id"] == "existing-owner"
    assert browser_auth["attempt-0"].chat_platform == "web-login"
    with pytest.raises(ValueError):
        login.complete_browser_login(context=context, state=state, code="authorization-code", provider=provider)
