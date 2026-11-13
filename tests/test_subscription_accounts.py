"""Subscription discovery and Nexus identity boundaries, without real logins."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nexus.core.auth import oauth_onboarding_domain as auth
from nexus.core.auth import subscription_accounts as accounts


def test_discovery_checks_commands_without_starting_or_reading_credentials(monkeypatch):
    monkeypatch.setenv("NEXUS_CLI_AUTH_MODE", "account")
    monkeypatch.setenv("CODEX_CLI_PATH", "/server/codex")
    monkeypatch.setenv("CLAUDE_CLI_PATH", "/server/claude")
    monkeypatch.setenv("AGY_CLI_PATH", "/server/agy")
    monkeypatch.setattr(accounts.shutil, "which", lambda cmd: cmd if cmd.endswith("codex") else None)
    monkeypatch.setattr(auth.subprocess, "Popen", lambda *a, **kw: pytest.fail("Discovery must not start authentication"))
    monkeypatch.setattr(auth, "get_user_credentials", lambda *a: pytest.fail("Discovery must not read credentials"))
    options = accounts.subscription_accounts()
    assert [item["id"] for item in options] == ["codex", "claude", "agy"]
    assert options[0]["available"] and options[0]["billing"] == "subscription"
    assert not options[1]["available"] and "Install the Claude CLI" in options[1]["reason"]
    assert not any("/server" in str(item) for item in options)


def test_bad_command_and_api_key_mode_report_unavailable(monkeypatch):
    monkeypatch.setenv("NEXUS_CLI_AUTH_MODE", "account")
    # Use the command argument setting consumed by the existing service.
    monkeypatch.setenv(auth._PROVIDER_ACCOUNT_CONNECT_ARGS_ENV["codex"], "'unterminated")
    assert "needs configuration" in accounts.subscription_accounts()[0]["reason"]
    monkeypatch.setenv("NEXUS_CLI_AUTH_MODE", "api-key")
    assert all(not p["available"] and "authentication mode" in p["reason"] for p in accounts.subscription_accounts())


@pytest.mark.parametrize("provider", ["codex", "claude", "agy"])
def test_subscription_environment_does_not_inherit_server_api_credentials(monkeypatch, provider):
    import os

    keys = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_USE_VERTEX", "GOOGLE_APPLICATION_CREDENTIALS", "XDG_CONFIG_HOME"]
    for key in keys:
        monkeypatch.setenv(key, "server-private-setting")
    result = auth._provider_account_environment(provider=provider, user_home="/private/user", provider_home="/private/provider")
    assert not any(key in result for key in keys)
    assert all(os.environ[key] == "server-private-setting" for key in keys)
    assert result[auth._PROVIDER_ACCOUNT_CONNECT_HOME_ENV[provider]] == "/private/provider"
    assert result["HOME"] == ("/private/provider" if provider == "agy" else "/private/user")


def test_antigravity_status_probe_also_excludes_server_api_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "server-only-key")
    captured = {}

    def run(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="gemini-fixture\tGemini Fixture\n", stderr="")

    monkeypatch.setattr(auth.subprocess, "run", run)
    assert auth._agy_signed_in(cli_path="synthetic-agy", home="/private/account")
    assert "GEMINI_API_KEY" not in captured["env"]
    assert captured["env"]["HOME"] == "/private/account"


@pytest.mark.parametrize("provider,expected", [("codex", "codex"), ("claude-code", "claude"), ("Antigravity", "agy")])
def test_destinations_are_explicit_and_local(provider, expected):
    destination = accounts.subscription_destination(provider, "https://evil.test")
    assert f"provider={expected}" in destination
    assert "next=%2F" in destination
    assert accounts.is_subscription_destination(destination)
    assert not accounts.is_subscription_destination("//evil.test/auth/subscription")
    assert not accounts.is_subscription_destination("/unrelated")


def test_unknown_provider_cannot_launch_a_command():
    with pytest.raises(ValueError):
        accounts.subscription_destination("shell")


def test_changing_subscription_keeps_workspace_return_path():
    first = accounts.subscription_destination("codex", "/workspace")
    assert accounts.subscription_return_path(first) == "/workspace"
    assert accounts.subscription_return_path(accounts.subscription_destination("claude", first)) == "/workspace"
    assert accounts.subscription_return_path("//evil.test") == "/"


@pytest.mark.parametrize("provider,anchor,billing", [("copilot", "copilot-h", "subscription"), ("opencode", "opencode-h", "api-key")])
def test_existing_form_shortcuts_preserve_selection_without_exposing_credentials(provider, anchor, billing):
    option = next(item for item in accounts.account_setup_options() if item["id"] == provider)
    assert option["billing"] == billing
    destination = accounts.account_setup_destination(provider, "/workspace")
    assert destination.endswith("#" + anchor)
    assert accounts.is_account_connection_destination(destination)
    assert accounts.subscription_return_path(destination) == "/workspace"
    assert accounts.subscription_return_path(accounts.subscription_destination("claude", destination)) == "/workspace"
    assert not accounts.is_account_connection_destination("/auth/setup?connection=unrecognized")
    assert not accounts.is_account_connection_destination("//evil.test/auth/setup?connection=copilot")


def test_existing_form_shortcut_context_uses_only_verified_nexus_user(monkeypatch):
    monkeypatch.setattr(auth, "get_auth_session", lambda _: SimpleNamespace(
        session_id="current-user", nexus_id="owner", status="oauth_done",
        expires_at=datetime.now(UTC) + timedelta(minutes=5)))
    monkeypatch.setattr(auth, "get_user_credentials", lambda _: SimpleNamespace(
        github_user_id=123, gitlab_user_id=None, github_token_enc="must-not-return"))
    result = accounts.account_setup_context(session_id="current-user", provider="copilot")
    assert result["github_linked"] and result["session_id"] == "current-user"
    assert "must-not-return" not in str(result)
    with pytest.raises(PermissionError):
        accounts.account_setup_context(session_id="", provider="opencode")
    with pytest.raises(ValueError):
        accounts.account_setup_destination("shell")


@pytest.mark.parametrize("case", ["missing", "expired", "pending", "no-identity"])
def test_subscription_page_requires_current_existing_identity(monkeypatch, case):
    record = SimpleNamespace(session_id="session", nexus_id="owner", status="oauth_done",
                             expires_at=datetime.now(UTC) + timedelta(minutes=5))
    monkeypatch.setattr(auth, "get_auth_session", lambda _: record)
    monkeypatch.setattr(auth, "get_user_credentials", lambda _: SimpleNamespace(github_user_id=123, gitlab_user_id=None))
    if case == "expired":
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif case == "pending":
        record.status = "pending"
    elif case == "no-identity":
        monkeypatch.setattr(auth, "get_user_credentials", lambda _: None)
    else:
        monkeypatch.setattr(auth, "get_auth_session", lambda _: pytest.fail("Anonymous page must not touch account storage"))
    with pytest.raises(PermissionError):
        accounts.subscription_account_context(session_id="" if case == "missing" else "session", provider="codex")


def test_existing_account_connection_is_private_and_does_not_require_ai_readiness(monkeypatch):
    monkeypatch.setattr(auth, "get_auth_session", lambda _: SimpleNamespace(
        session_id="existing-session", nexus_id="owner", status="oauth_done",
        expires_at=datetime.now(UTC) + timedelta(minutes=5)))
    monkeypatch.setattr(auth, "get_user_credentials", lambda _: SimpleNamespace(
        github_user_id=123, gitlab_user_id=None, claude_account_enabled=True,
        github_token_enc="must-not-return", claude_api_key_enc="must-not-return"))
    monkeypatch.setattr(auth, "get_setup_status", lambda _: pytest.fail("Do not require AI readiness to connect AI"))
    result = accounts.subscription_account_context(session_id="existing-session", provider="claude")
    assert result["session_id"] == "existing-session" and result["connected"]
    assert "must-not-return" not in str(result)


@pytest.mark.parametrize("operation", ["status", "submit"])
@pytest.mark.parametrize("status,expired", [("pending", False), ("oauth_done", True)])
def test_expired_or_pending_sessions_cannot_poll_or_submit_provider_codes(monkeypatch, operation, status, expired):
    record = SimpleNamespace(session_id="session", status=status,
        expires_at=datetime.now(UTC) + timedelta(seconds=-1 if expired else 600))
    monkeypatch.setattr(auth, "get_auth_session", lambda _: record)
    monkeypatch.setattr(auth, "get_setup_status", lambda _: pytest.fail("Do not inspect provider state"))
    monkeypatch.setattr(auth, "_normalize_provider_auth_code", lambda _: pytest.fail("Do not forward codes"))
    result = (auth.get_provider_account_login_status(session_id="session", provider="claude") if operation == "status"
              else auth.submit_provider_account_login_code(session_id="session", provider="claude", authorization_code="must-not-forward"))
    assert result["state"] == "invalid_session"
