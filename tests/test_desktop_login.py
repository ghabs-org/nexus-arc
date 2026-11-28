"""Desktop grants use real auth storage and atomic one-time exchanges."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import sqlalchemy as sa

from nexus.core.auth import credential_store as store
from nexus.core.auth import desktop_login as login
from nexus.core.auth import oauth_onboarding_domain as auth


@pytest.fixture
def desktop_auth(monkeypatch, tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'accounts.db'}")
    store._AuthBase.metadata.create_all(engine)
    monkeypatch.setattr(store, "_get_engine", lambda: engine)
    monkeypatch.setattr(auth, "_now_utc", lambda: datetime.now(UTC).replace(tzinfo=None))
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("NEXUS_GITHUB_CALLBACK_URL", "https://studio.example/auth/github/callback")
    store.upsert_github_credentials(
        nexus_id="owner",
        github_user_id=123,
        github_login="test-owner",
        github_token_enc="encrypted-upstream-token",
        org_verified=True,
    )
    sid = auth.create_login_session_for_user(
        nexus_id="owner", discord_user_id="", discord_username=None, chat_platform="web-login"
    )
    store.update_auth_session(session_id=sid, status="oauth_done")
    yield SimpleNamespace(engine=engine, session_id=sid)
    engine.dispose()


def start_attempt():
    verifier = "v" * 43
    redirect = "http://127.0.0.1:19234/nexus/desktop/callback"
    result = login.start(
        redirect_uri=redirect,
        code_challenge=login.pkce_challenge(verifier),
        state="s" * 43,
        device_name="Test Mac",
    )
    return result["attempt_id"], verifier, redirect


def approve_attempt(sid, identity):
    context = login.confirmation(attempt_id=identity, session_id=sid)
    url = login.approve(
        attempt_id=identity,
        session_id=sid,
        context=context["context"],
        csrf=context["csrf"],
        allow=True,
    )
    return parse_qs(urlsplit(url).query)["code"][0]


def grant(sid):
    identity, verifier, redirect = start_attempt()
    code = approve_attempt(sid, identity)
    return login.exchange(
        attempt_id=identity, code=code, code_verifier=verifier, redirect_uri=redirect
    )


def test_identity_revocation_and_hashed_storage(desktop_auth):
    result = grant(desktop_auth.session_id)
    identity = login.device_session(result["token"])
    assert identity["account"] == "test-owner"
    assert identity["nexus_id"] == "owner" and identity["projects"] == []
    assert "token" not in identity
    with store.Session(desktop_auth.engine) as db:
        row = db.get(login.DesktopDevice, result["device_id"])
        assert row.token_hash != result["token"] and len(row.token_hash) == 64
    login.device_session(result["token"], revoke=True)
    with pytest.raises(PermissionError, match="revoked"):
        login.device_session(result["token"])


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.test/callback",
        "http://localhost:19234/nexus/desktop/callback",
        "http://127.0.0.1:80/nexus/desktop/callback",
        "http://127.0.0.1:19234/wrong",
        "http://127.0.0.1:19234/nexus/desktop/callback?next=https://evil.test",
        "http://user@127.0.0.1:19234/nexus/desktop/callback",
        "http://127.0.0.1:19234/nexus/desktop/callback#fragment",
        "file:///tmp/callback",
        "http://127.0.0.1:99999/nexus/desktop/callback",
    ],
)
def test_callback_allowlist(desktop_auth, uri):
    with pytest.raises(ValueError):
        login.validate_redirect(uri)


@pytest.mark.parametrize("fault", ["verifier", "code", "redirect", "expired", "replay"])
def test_exchange_binds_code_verifier_redirect_and_expiry(desktop_auth, monkeypatch, fault):
    identity, verifier, redirect = start_attempt()
    code = approve_attempt(desktop_auth.session_id, identity)
    values = {
        "attempt_id": identity,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect,
    }
    if fault == "verifier":
        values["code_verifier"] = "w" * 43
    if fault == "code":
        values["code"] = "x" * 43
    if fault == "redirect":
        values["redirect_uri"] = redirect.replace("19234", "19235")
    if fault == "expired":
        monkeypatch.setattr(login.time, "time", lambda: 10**12)
    if fault == "replay":
        login.exchange(**values)
    with pytest.raises(ValueError):
        login.exchange(**values)


def test_concurrent_exchange_mints_one_device(desktop_auth):
    identity, verifier, redirect = start_attempt()
    code = approve_attempt(desktop_auth.session_id, identity)

    def exchange(_):
        try:
            return login.exchange(
                attempt_id=identity, code=code, code_verifier=verifier, redirect_uri=redirect
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(exchange, range(2)))
    assert sum(value is not None for value in results) == 1
    with store.Session(desktop_auth.engine) as db:
        assert db.query(login.DesktopDevice).count() == 1


@pytest.mark.parametrize("fault", ["csrf", "context", "session", "expired"])
def test_approval_requires_current_browser_and_account(desktop_auth, monkeypatch, fault):
    identity, _, _ = start_attempt()
    c = login.confirmation(attempt_id=identity, session_id=desktop_auth.session_id)
    values = {
        "attempt_id": identity,
        "session_id": desktop_auth.session_id,
        "context": c["context"],
        "csrf": c["csrf"],
        "allow": True,
    }
    if fault == "csrf":
        values["csrf"] = "forged"
    if fault == "context":
        values["context"] = "forged"
    if fault == "session":
        values["session_id"] = "unknown"
    if fault == "expired":
        monkeypatch.setattr(login.time, "time", lambda: 10**12)
    with pytest.raises(PermissionError):
        login.approve(**values)


def test_cancel_does_not_mint_device(desktop_auth):
    identity, _, _ = start_attempt()
    c = login.confirmation(attempt_id=identity, session_id=desktop_auth.session_id)
    url = login.approve(
        attempt_id=identity,
        session_id=desktop_auth.session_id,
        context=c["context"],
        csrf=c["csrf"],
        allow=False,
    )
    assert parse_qs(urlsplit(url).query) == {"state": ["s" * 43], "error": ["access_denied"]}
    with pytest.raises(ValueError):
        login.confirmation(attempt_id=identity, session_id=desktop_auth.session_id)
    with store.Session(desktop_auth.engine) as db:
        assert db.query(login.DesktopDevice).count() == 0


def test_credentials_expire_and_do_not_modify_browser_sessions(desktop_auth, monkeypatch):
    result = grant(desktop_auth.session_id)
    monkeypatch.setattr(login.time, "time", lambda: result["expires_at"] + 1)
    with pytest.raises(PermissionError):
        login.device_session(result["token"])
    assert store.get_auth_session(desktop_auth.session_id).status == "oauth_done"


def test_schema_upgrade_preserves_existing_credentials(desktop_auth):
    login.DesktopAttempt.__table__.drop(desktop_auth.engine)
    login.DesktopDevice.__table__.drop(desktop_auth.engine)
    assert "nexus_desktop_devices" not in sa.inspect(desktop_auth.engine).get_table_names()
    login._engine()
    login._engine()
    assert "nexus_desktop_devices" in sa.inspect(desktop_auth.engine).get_table_names()
    assert store.get_user_credentials("owner").github_login == "test-owner"


def test_approval_cannot_switch_to_another_valid_account(desktop_auth):
    identity, _, _ = start_attempt()
    context = login.confirmation(attempt_id=identity, session_id=desktop_auth.session_id)
    store.upsert_github_credentials(
        nexus_id="other",
        github_user_id=456,
        github_login="other-owner",
        github_token_enc="synthetic",
        org_verified=True,
    )
    other = auth.create_login_session_for_user(
        nexus_id="other", discord_user_id="", discord_username=None, chat_platform="web-login"
    )
    store.update_auth_session(session_id=other, status="oauth_done")
    with pytest.raises(PermissionError):
        login.approve(
            attempt_id=identity,
            session_id=other,
            context=context["context"],
            csrf=context["csrf"],
            allow=True,
        )


def test_device_rechecks_existing_identity(desktop_auth, monkeypatch):
    credential = grant(desktop_auth.session_id)
    monkeypatch.setattr(store, "get_user_credentials", lambda nexus_id: None)
    with pytest.raises(PermissionError):
        login.device_session(credential["token"])


def test_grants_survive_new_engine(desktop_auth, monkeypatch):
    result = grant(desktop_auth.session_id)
    desktop_auth.engine.dispose()
    reopened = sa.create_engine(desktop_auth.engine.url)
    monkeypatch.setattr(store, "_get_engine", lambda: reopened)
    assert login.device_session(result["token"])["nexus_id"] == "owner"
    reopened.dispose()
