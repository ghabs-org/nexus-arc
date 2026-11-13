from types import SimpleNamespace

import pytest

from nexus.core.auth import access_domain as access_svc


def test_get_auth_onboarding_disabled_message_includes_key_requirements(monkeypatch):
    monkeypatch.setenv("NEXUS_AUTH_ENABLED", "false")
    monkeypatch.setenv("NEXUS_STORAGE_BACKEND", "filesystem")
    monkeypatch.delenv("NEXUS_STORAGE_DSN", raising=False)
    monkeypatch.delenv("NEXUS_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("NEXUS_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("NEXUS_GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("NEXUS_GITLAB_CLIENT_ID", raising=False)
    monkeypatch.delenv("NEXUS_GITLAB_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("NEXUS_CREDENTIALS_MASTER_KEY", raising=False)

    message = access_svc.get_auth_onboarding_disabled_message()

    assert "Auth onboarding is disabled in this environment" in message
    assert "NEXUS_AUTH_ENABLED=true" in message
    assert "NEXUS_STORAGE_BACKEND=postgres" in message
    assert "NEXUS_STORAGE_DSN" in message
    assert "NEXUS_PUBLIC_BASE_URL" in message
    assert "NEXUS_CREDENTIALS_MASTER_KEY" in message


def test_get_auth_onboarding_disabled_message_is_empty_when_enabled(monkeypatch):
    monkeypatch.setenv("NEXUS_AUTH_ENABLED", "true")

    message = access_svc.get_auth_onboarding_disabled_message()

    assert message == ""


@pytest.mark.parametrize("legacy_key,legacy_account", [("old-key", False), (None, True)])
def test_retired_credentials_do_not_make_setup_or_polling_ready(monkeypatch, legacy_key, legacy_account):
    record = SimpleNamespace(
        nexus_id="legacy-user", github_token_enc=None, github_login=None,
        gitlab_token_enc="git-token", gitlab_username="alice",
        linkedin_token_enc=None, x_bearer_token_enc=None, x_oauth_token_enc=None,
        meta_page_token_enc=None, meta_ig_account_id=None, codex_api_key_enc=None,
        claude_api_key_enc=None, copilot_github_token_enc=None,
        gemini_api_key_enc=legacy_key, gemini_account_enabled=legacy_account,
        org_verified=True, linkedin_author_urn=None, meta_page_id=None,
    )
    monkeypatch.setenv("NEXUS_CLI_AUTH_MODE", "account")
    monkeypatch.setattr(access_svc, "auth_enabled", lambda: True)
    monkeypatch.setattr(access_svc, "maybe_sync_user_project_access", lambda _nid: None)
    monkeypatch.setattr(access_svc, "get_user_credentials", lambda _nid: record)
    monkeypatch.setattr(access_svc, "get_user_project_access", lambda _nid: [SimpleNamespace(project_key="demo")])
    monkeypatch.setattr(access_svc, "list_credentials_for_sync", lambda **kw: [record])
    monkeypatch.setattr(access_svc, "has_user_project_access", lambda *_args: True)

    status = access_svc.get_setup_status("legacy-user")

    assert status["git_provider_linked"] is True
    assert status["ready"] is False
    assert status["ai_provider_ready"] is False
    assert not any("gemini" in key for key in status)
    assert access_svc.has_setup_ready_user_for_project("demo") is False


@pytest.mark.parametrize("credentials", [{"GEMINI_API_KEY": "retired"}, {"NEXUS_ACCOUNT_AUTH_PROVIDERS": "gemini"}])
def test_prepare_execution_env_rejects_retired_credentials(credentials):
    env, error = access_svc.prepare_execution_env("legacy-user", {"GITLAB_TOKEN": "git-token", **credentials})
    assert env == {}
    assert "Missing AI provider credentials" in error


def test_build_execution_env_ignores_legacy_secrets_and_keeps_supported_accounts(monkeypatch, tmp_path):
    record = SimpleNamespace(
        github_token_enc="git-token", gitlab_token_enc=None,
        copilot_github_token_enc=None, codex_api_key_enc=None,
        claude_api_key_enc=None, claude_account_enabled=True,
        gemini_api_key_enc="broken-legacy-key", gemini_account_enabled=True,
    )
    decrypted = []
    monkeypatch.setenv("NEXUS_CLI_AUTH_MODE", "account")
    monkeypatch.setattr(access_svc, "get_user_credentials", lambda _nid: record)
    monkeypatch.setattr(access_svc, "decrypt_secret", lambda secret: decrypted.append(secret) or secret)
    monkeypatch.setattr(access_svc, "NEXUS_RUNTIME_DIR", str(tmp_path))

    env, error = access_svc.build_execution_env("alice")

    assert error is None
    assert decrypted == ["git-token"]
    assert env["NEXUS_ACCOUNT_AUTH_PROVIDERS"] == "claude"
    assert not any("GEMINI" in key for key in env)
    assert not (tmp_path / "auth" / "gemini").exists()


def test_prepare_execution_env_strips_retired_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(access_svc, "NEXUS_RUNTIME_DIR", str(tmp_path))
    env, error = access_svc.prepare_execution_env("alice", {
        "GITLAB_TOKEN": "git-token", "GEMINI_API_KEY": "legacy-key",
        "GEMINI_HOME": "/legacy", "GEMINI_CLI_HOME": "/legacy",
        "NEXUS_ACCOUNT_AUTH_PROVIDERS": "gemini,claude",
    })
    assert error is None
    assert env["NEXUS_ACCOUNT_AUTH_PROVIDERS"] == "claude"
    assert not any("GEMINI" in key for key in env)
