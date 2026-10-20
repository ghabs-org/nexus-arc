from __future__ import annotations

from nexus.core.auth import access_domain as access_svc
from nexus.core.auth import execution_env_resolver as resolver


def test_prepare_execution_env_allows_git_only_for_issue_write(monkeypatch):
    monkeypatch.setattr(access_svc, "_ensure_private_runtime_dir", lambda _path: None)
    monkeypatch.setattr(access_svc, "NEXUS_RUNTIME_DIR", "/tmp/nexus-test-runtime")

    env, error = access_svc.prepare_execution_env(
        "openclaw:user:alice",
        {"GITHUB_TOKEN": "ghu_test"},
        purpose="issue_write",
    )

    assert error is None
    assert env["GITHUB_TOKEN"] == "ghu_test"
    assert env["GITLAB_TOKEN"] == "ghu_test"
    assert env["HOME"].endswith("/openclaw:user:alice")


def test_resolve_execution_env_uses_credential_store(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "build_execution_env",
        lambda requester_id, *, purpose="agent_run": (
            {"NEXUS_ID": requester_id, "GITHUB_TOKEN": "ghu_store", "PURPOSE": purpose},
            None,
        ),
    )

    env, error = resolver.resolve_execution_env(
        "openclaw:user:alice",
        project_name="demo",
        repo_name="demo/repo",
        issue_url="https://github.com/demo/repo/issues/42",
        purpose="agent_run",
    )

    assert error is None
    assert env["NEXUS_ID"] == "openclaw:user:alice"
    assert env["GITHUB_TOKEN"] == "ghu_store"
    assert env["PURPOSE"] == "agent_run"


def test_prepare_execution_env_gives_agy_its_own_home(monkeypatch):
    created: list[str] = []
    monkeypatch.setattr(access_svc, "_ensure_private_runtime_dir", lambda path: created.append(path))
    monkeypatch.setattr(access_svc, "NEXUS_RUNTIME_DIR", "/tmp/nexus-test-runtime")

    env, error = access_svc.prepare_execution_env(
        "nexus-alice",
        {"GITHUB_TOKEN": "ghu_test", "NEXUS_ACCOUNT_AUTH_PROVIDERS": "agy"},
        purpose="agent_run",
    )

    assert error is None
    # Antigravity keeps login/state in $HOME/.gemini: separate from Gemini CLI's home.
    assert env["AGY_HOME"] == "/tmp/nexus-test-runtime/auth/agy/nexus-alice"
    assert "GEMINI_HOME" not in env
    assert env["AGY_HOME"] != env["HOME"]
    assert env["AGY_HOME"] in created


def test_setup_status_counts_agy_account_as_ai_provider(monkeypatch):
    from types import SimpleNamespace

    record = SimpleNamespace(
        github_token_enc="enc",
        github_login="alice",
        gitlab_token_enc=None,
        gitlab_username=None,
        linkedin_token_enc=None,
        linkedin_author_urn=None,
        x_bearer_token_enc=None,
        x_oauth_token_enc=None,
        meta_page_token_enc=None,
        meta_page_id=None,
        meta_ig_account_id=None,
        codex_api_key_enc=None,
        gemini_api_key_enc=None,
        claude_api_key_enc=None,
        copilot_github_token_enc=None,
        codex_account_enabled=False,
        gemini_account_enabled=False,
        claude_account_enabled=False,
        copilot_account_enabled=False,
        agy_account_enabled=True,
        org_verified=True,
    )
    monkeypatch.setattr(access_svc, "auth_enabled", lambda: True)
    monkeypatch.setattr(access_svc, "maybe_sync_user_project_access", lambda _nid: True)
    monkeypatch.setattr(access_svc, "get_user_credentials", lambda _nid: record)
    monkeypatch.setattr(
        access_svc, "get_user_project_access", lambda _nid: [SimpleNamespace(project_key="nexus")]
    )
    monkeypatch.setenv("NEXUS_CLI_AUTH_MODE", "account")

    status = access_svc.get_setup_status("nexus-alice")

    assert status["agy_account_enabled"] is True
    assert status["account_provider_ready"] is True
