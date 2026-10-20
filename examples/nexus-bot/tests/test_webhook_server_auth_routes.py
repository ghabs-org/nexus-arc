"""Auth guard tests for webhook server web routes."""

from __future__ import annotations

from types import SimpleNamespace


def _ready_session_payload(session_id: str) -> dict:
    return {
        "exists": True,
        "session_id": session_id,
        "status": "completed",
        "expires_at": "2999-01-01T00:00:00+00:00",
        "setup": {"ready": True},
    }


def test_retired_provider_callback_relay_is_removed():
    import webhook_server

    response = webhook_server.app.test_client().post(
        "/auth/provider-connect/relay-callback",
        json={"provider": "gemini", "session_id": "old-session", "callback_url": "http://localhost:1234/"},
    )
    assert response.status_code == 404


def test_retired_provider_cannot_start_account_login(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    from nexus.core.auth import oauth_onboarding_domain as auth
    from datetime import datetime, UTC, timedelta
    monkeypatch.setattr(auth, "get_auth_session", lambda _sid: SimpleNamespace(
        session_id="session", status="completed", expires_at=datetime.now(UTC) + timedelta(minutes=5),
    ))
    response = webhook_server.app.test_client().post(
        "/auth/provider-connect/start", json={"provider": "gemini", "session_id": "session"},
    )
    assert response.status_code == 400
    assert "Unsupported provider" in response.get_json()["message"]


def test_index_renders_dedicated_login_page_when_auth_enabled(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda _session_id: {"exists": False},
    )

    client = webhook_server.app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"<h1>Sign in</h1>" in response.data
    assert b"/auth/start" in response.data


def test_visualizer_redirects_to_login_when_auth_enabled_without_session(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda _session_id: {"exists": False},
    )

    client = webhook_server.app.test_client()
    response = client.get("/visualizer")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/?next=/visualizer")


def test_visualizer_accepts_session_query_and_sets_cookie(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda session_id: _ready_session_payload(str(session_id)),
    )

    client = webhook_server.app.test_client()
    first = client.get("/visualizer?session=sess-123")
    second = client.get("/visualizer")

    assert first.status_code == 302
    assert first.headers["Location"].endswith("/visualizer")
    assert webhook_server._WEB_SESSION_COOKIE_NAME in (first.headers.get("Set-Cookie") or "")
    assert second.status_code == 200
    assert b"Nexus Workflow Visualizer" in second.data


def test_visualizer_trailing_slash_is_served(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda session_id: _ready_session_payload(str(session_id)),
    )

    client = webhook_server.app.test_client()
    response = client.get("/visualizer/?session=sess-123")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/visualizer")


def test_visualizer_snapshot_requires_auth_when_enabled(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda _session_id: {"exists": False},
    )

    client = webhook_server.app.test_client()
    response = client.get("/visualizer/snapshot")

    assert response.status_code == 401
    payload = response.get_json()
    assert payload["status"] == "unauthorized"


def test_visualizer_snapshot_returns_data_for_ready_session(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda session_id: _ready_session_payload(str(session_id)),
    )
    monkeypatch.setattr(
        webhook_server,
        "_collect_visualizer_snapshot",
        lambda: [{"issue": "99", "workflow_id": "wf-99", "status": {"state": "running"}}],
    )

    client = webhook_server.app.test_client()
    response = client.get("/visualizer/snapshot?session=sess-abc")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["workflows"][0]["issue"] == "99"


def test_visualizer_requires_shared_token_when_auth_disabled(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "secret-token")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", False)

    client = webhook_server.app.test_client()
    visualizer_response = client.get("/visualizer")
    index_response = client.get("/")

    assert visualizer_response.status_code == 302
    assert visualizer_response.headers["Location"].endswith("/?next=/visualizer")
    assert index_response.status_code == 200
    assert b"<h1>Visualizer access</h1>" in index_response.data
    assert b"/visualizer/access" in index_response.data


def test_visualizer_access_token_post_sets_cookie(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "secret-token")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", False)

    client = webhook_server.app.test_client()
    invalid = client.post("/visualizer/access", data={"token": "wrong", "next": "/visualizer"})
    assert invalid.status_code == 401

    valid = client.post("/visualizer/access", data={"token": "secret-token", "next": "/visualizer"})
    assert valid.status_code == 302
    assert valid.headers["Location"].endswith("/visualizer")
    assert webhook_server._VISUALIZER_SHARED_TOKEN_COOKIE_NAME in (valid.headers.get("Set-Cookie") or "")

    visualizer_response = client.get("/visualizer")
    assert visualizer_response.status_code == 200
    assert b"Nexus Workflow Visualizer" in visualizer_response.data


def test_provider_connect_start_route_returns_service_payload(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_start_provider_account_login",
        lambda session_id, provider: {
            "started": True,
            "session_id": session_id,
            "provider": provider,
            "state": "starting",
        },
    )

    client = webhook_server.app.test_client()
    response = client.post(
        "/auth/provider-connect/start",
        json={"session_id": "sess-1", "provider": "codex"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["started"] is True
    assert payload["provider"] == "codex"


def test_provider_connect_status_route_returns_service_payload(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_provider_account_login_status",
        lambda session_id, provider: {
            "exists": True,
            "session_id": session_id,
            "provider": provider,
            "state": "pending",
            "verify_url": "https://auth.openai.com/device",
            "user_code": "ABCD-EFGH",
        },
    )

    client = webhook_server.app.test_client()
    response = client.get("/auth/provider-connect/status?session_id=sess-2&provider=codex")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["state"] == "pending"
    assert payload["user_code"] == "ABCD-EFGH"


def test_provider_connect_pending_popup_page_is_gone(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)

    response = webhook_server.app.test_client().get("/auth/provider-connect/pending?session_id=sess-9&provider=claude")

    # Account setup shows the sign-in link inline; the popup page was removed.
    assert response.status_code in {404, 405}




def test_provider_connect_submit_code_route_returns_service_payload(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_submit_provider_account_login_code",
        lambda session_id, provider, authorization_code: {
            "submitted": True,
            "session_id": session_id,
            "provider": provider,
            "state": "pending",
            "message": f"submitted:{authorization_code}",
        },
    )

    client = webhook_server.app.test_client()
    response = client.post(
        "/auth/provider-connect/submit-code",
        json={
            "session_id": "sess-4",
            "provider": "claude",
            "authorization_code": "4/0AX4XfWg-example",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["submitted"] is True
    assert payload["provider"] == "claude"


def test_provider_connect_setup_form_renders_account_rows_with_copy_and_paste():
    import webhook_server

    rendered = webhook_server._render_ai_key_form(
        session_id="lsr_session-123",
        show_api_key_inputs=True,
        copilot_checked=True,
        show_copilot_option=True,
        copilot_token_set=False,
        codex_key_set=False,
        claude_key_set=False,
        codex_account_enabled=False,
        claude_account_enabled=True,
        copilot_account_enabled=False,
        existing_keys_note="note",
        session_ref="lsr_session-123",
        identity_rows=[("GitHub", "<strong>Ghabs95</strong>")],
    )

    for key, name in (("claude", "Claude"), ("codex", "Codex"), ("agy", "Antigravity")):
        assert f'id="account-{key}"' in rendered
        assert f"startProviderConnect('{key}')" in rendered
        assert f'name="use_{key}_account"' in rendered
        assert f">{name}</div>" in rendered
    # Code-entry providers get a code box with a Paste button; Codex shows a device code instead.
    for key in ("claude", "agy"):
        assert f'id="{key}_authorization_code"' in rendered
        assert f'data-paste="{key}_authorization_code"' in rendered
        assert f"submitProviderAuthCode('{key}')" in rendered
    assert 'id="codex_authorization_code"' not in rendered
    assert "data-copy-device" in rendered and "data-copy-link" in rendered
    # Connected accounts show their state and stay switched on.
    assert 'name="use_claude_account" value="1" checked' in rendered
    assert ">Connected</span>" in rendered
    # The session reference can be copied; API key fields can be pasted.
    assert 'data-copy="session_ref_value"' in rendered
    assert 'data-paste="claude_api_key"' in rendered
    # The script talks to the same endpoints and never builds JS from page data.
    for endpoint in ("/auth/provider-connect/start", "/auth/provider-connect/status",
                     "/auth/provider-connect/submit-code"):
        assert endpoint in rendered
    assert '<script type="application/json" id="setup-data">{"session": "lsr_session-123"}</script>' in rendered
    assert "navigator.clipboard.readText" in rendered and "navigator.clipboard.writeText" in rendered


def test_provider_connect_setup_form_hides_api_keys_in_account_mode():
    import webhook_server

    rendered = webhook_server._render_ai_key_form(
        session_id="lsr_session-123",
        show_api_key_inputs=False,
        copilot_checked=True,
        show_copilot_option=True,
        copilot_token_set=False,
        codex_key_set=False,
        claude_key_set=False,
        codex_account_enabled=False,
        claude_account_enabled=False,
        copilot_account_enabled=False,
        existing_keys_note="note",
    )

    assert 'id="keys-h"' not in rendered
    for field in ("codex_api_key", "claude_api_key", "copilot_github_token"):
        assert f'name="{field}"' not in rendered
    assert 'id="accounts-h"' in rendered


def test_auth_start_rejects_unconfigured_provider(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_svc_resolve_login_session_id", lambda _value: "sess-1")
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_ID", "")
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_SECRET", "")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_ID", "")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_SECRET", "")

    client = webhook_server.app.test_client()
    response = client.get("/auth/start?session=sess-1&provider=github")

    assert response.status_code == 400
    assert b"OAuth is not configured" in response.data


def test_auth_start_redirects_when_provider_configured(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_svc_resolve_login_session_id", lambda _value: "sess-1")
    monkeypatch.setattr(
        webhook_server,
        "_svc_start_oauth_flow",
        lambda _session_id, provider="github": (f"https://oauth.test/{provider}", "state-1"),
    )
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_SECRET", "gh-secret")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_ID", "")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_SECRET", "")

    client = webhook_server.app.test_client()
    response = client.get("/auth/start?session=sess-1&provider=github")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://oauth.test/github"


def test_github_callback_handles_provider_error_query(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_auth_session_by_state",
        lambda _state: SimpleNamespace(session_id="sess-1"),
    )
    notified: list[str] = []
    monkeypatch.setattr(
        webhook_server,
        "_notify_onboarding_message",
        lambda session_id, text: notified.append(f"{session_id}:{text}"),
    )

    client = webhook_server.app.test_client()
    response = client.get(
        "/auth/github/callback?error=access_denied&error_description=User%20cancelled&state=state-1"
    )

    assert response.status_code == 400
    assert b"GitHub returned" in response.data
    assert notified and "GitHub OAuth failed" in notified[0]


def test_gitlab_callback_handles_provider_error_query(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_auth_session_by_state",
        lambda _state: SimpleNamespace(session_id="sess-2"),
    )
    notified: list[str] = []
    monkeypatch.setattr(
        webhook_server,
        "_notify_onboarding_message",
        lambda session_id, text: notified.append(f"{session_id}:{text}"),
    )

    client = webhook_server.app.test_client()
    response = client.get(
        "/auth/gitlab/callback?error=access_denied&error_description=User%20cancelled&state=state-2"
    )

    assert response.status_code == 400
    assert b"GitLab returned" in response.data
    assert notified and "GitLab OAuth failed" in notified[0]


def test_visualizer_disabled_flag_returns_404(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", False)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "secret-token")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)

    client = webhook_server.app.test_client()
    root_response = client.get("/")
    visualizer_response = client.get("/visualizer")
    snapshot_response = client.get("/visualizer/snapshot")

    assert root_response.status_code == 404
    assert visualizer_response.status_code == 404
    assert snapshot_response.status_code == 404


def _enable_web_login(monkeypatch, webhook_server, *, ready: bool = True):
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda session_id: (
            _ready_session_payload(str(session_id)) if ready else {"exists": False}
        ),
    )


def test_login_sets_cookie_and_redirects_to_next(monkeypatch):
    import webhook_server

    _enable_web_login(monkeypatch, webhook_server)

    client = webhook_server.app.test_client()
    response = client.get("/login?session=sess-123&next=/workspace")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/workspace")
    cookie = response.headers.get("Set-Cookie") or ""
    assert f"{webhook_server._WEB_SESSION_COOKIE_NAME}=sess-123" in cookie
    assert "HttpOnly" in cookie


def test_login_form_post_sets_cookie(monkeypatch):
    import webhook_server

    _enable_web_login(monkeypatch, webhook_server)

    client = webhook_server.app.test_client()
    response = client.post("/login", data={"session": "sess-123", "next": "/"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert webhook_server._WEB_SESSION_COOKIE_NAME in (response.headers.get("Set-Cookie") or "")


def test_login_renders_sign_in_form_without_ready_session(monkeypatch):
    import webhook_server

    _enable_web_login(monkeypatch, webhook_server, ready=False)

    client = webhook_server.app.test_client()
    response = client.get("/login?next=/")

    assert response.status_code == 200
    assert b"<h1>Sign in</h1>" in response.data
    assert b'data-paste="session"' in response.data
    assert b'action="/login"' in response.data
    assert "Set-Cookie" not in response.headers


def test_login_rejects_unknown_session_and_clears_cookie(monkeypatch):
    import webhook_server

    _enable_web_login(monkeypatch, webhook_server, ready=False)

    client = webhook_server.app.test_client()
    response = client.get("/login?session=bogus")

    assert response.status_code == 200
    assert b"setup is not ready" in response.data
    assert "Max-Age=0" in (response.headers.get("Set-Cookie") or "")


def test_login_ignores_off_origin_next(monkeypatch):
    import webhook_server

    _enable_web_login(monkeypatch, webhook_server)

    client = webhook_server.app.test_client()
    for unsafe in ("//evil.example", "/\\evil.example", "https://evil.example"):
        response = client.get("/login", query_string={"session": "sess-123", "next": unsafe})
        assert response.status_code == 302
        assert response.headers["Location"] in {"/", "http://localhost/"}


def test_login_is_unavailable_when_auth_disabled(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", False)

    client = webhook_server.app.test_client()
    response = client.get("/login")

    assert response.status_code == 404


def test_provider_connect_setup_form_offers_antigravity_code_flow():
    import webhook_server

    rendered = webhook_server._render_ai_key_form(
        session_id="lsr_session-123",
        show_api_key_inputs=False,
        copilot_checked=True,
        show_copilot_option=True,
        copilot_token_set=False,
        codex_key_set=False,
        claude_key_set=False,
        codex_account_enabled=False,
        claude_account_enabled=False,
        copilot_account_enabled=False,
        existing_keys_note="note",
        agy_account_enabled=True,
    )

    assert "startProviderConnect('agy')" in rendered
    assert 'name="use_agy_account" value="1" checked' in rendered
    assert "antigravity.google shows a code" in rendered
    assert "submitProviderAuthCode('agy')" in rendered


def test_ai_keys_form_saves_antigravity_account_toggle(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    captured: dict = {}

    def _store(**kwargs):
        captured.update(kwargs)
        return {"ready": True, "session_id": kwargs["session_id"]}

    monkeypatch.setattr(webhook_server, "_svc_store_ai_provider_keys", _store)

    client = webhook_server.app.test_client()
    client.post("/auth/ai-keys", data={"session_id": "lsr_session-123", "use_agy_account": "1"})

    assert captured["use_agy_account"] is True


def test_auth_setup_reopens_account_page_for_finished_session(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_svc_format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda _sid: {
            "exists": True,
            "session_id": "a" * 32,
            "status": "completed",
            "expires_at": "2999-01-01T00:00:00+00:00",
            "setup": {
                "github_linked": True,
                "github_login": "Ghabs95",
                "projects": ["nexus"],
                "claude_account_enabled": True,
                "agy_account_enabled": False,
            },
        },
    )

    response = webhook_server.app.test_client().get("/auth/setup?session=lsr_ref")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<h1>Account setup</h1>" in body
    assert "Signed in as Ghabs95 · 1 of 3 AI accounts connected" in body
    assert 'id="account-agy"' in body and "nexus" in body
    assert f"lsr_{'a' * 32}" in body
    assert webhook_server._WEB_SESSION_COOKIE_NAME in (response.headers.get("Set-Cookie") or "")


def test_auth_setup_rejects_unknown_or_unfinished_session(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    states = iter([{"exists": False}, {"exists": True, "status": "pending", "expires_at": "2999-01-01T00:00:00+00:00"}])
    monkeypatch.setattr(webhook_server, "_svc_get_session_and_setup_status", lambda _sid: next(states))
    client = webhook_server.app.test_client()

    for _ in range(2):
        response = client.get("/auth/setup?session=lsr_ref")
        assert response.status_code == 404
        assert "Set-Cookie" not in response.headers


def test_ai_keys_save_shows_status_and_what_is_missing(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_svc_format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(webhook_server, "_notify_onboarding_message", lambda **kw: None)
    monkeypatch.setattr(
        webhook_server,
        "_svc_store_ai_provider_keys",
        lambda **kw: {"ready": False, "session_id": "a" * 32, "grants_count": 0},
    )
    monkeypatch.setattr(webhook_server, "_svc_resolve_login_session_id", lambda ref: "a" * 32)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda _sid: {
            "exists": True,
            "setup": {
                "github_login": "Ghabs95",
                "git_provider_linked": True,
                "org_verified": True,
                "project_access_count": 0,
                "ai_provider_ready": True,
                "claude_account_enabled": True,
                "projects": [],
            },
        },
    )

    response = webhook_server.app.test_client().post(
        "/auth/ai-keys", data={"session_id": "lsr_ref", "use_claude_account": "1"}
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<h1>Setup saved</h1>" in body
    assert "Not ready yet" in body
    assert "Access to a Nexus project" in body
    assert "Sign in with GitHub" not in body
    assert f"/auth/setup?session=lsr_{'a' * 32}" in body
    assert "✅" not in body and "⚠️" not in body
    assert f"{webhook_server._WEB_SESSION_COOKIE_NAME}={'a' * 32}" in (response.headers.get("Set-Cookie") or "")
