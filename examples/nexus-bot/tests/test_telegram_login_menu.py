"""`/login` offers a provider menu once Git is linked; its buttons start account sign-ins."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


def _ready_session():
    return {
        "exists": True,
        "session_id": "a" * 32,
        "session_ref": "lsr_ref",
        "status": "completed",
        "expires_at": "2999-01-01T00:00:00+00:00",
    }


def _patch_auth(monkeypatch, telegram_bot, latest):
    monkeypatch.setattr(telegram_bot, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(telegram_bot, "NEXUS_PUBLIC_BASE_URL", "https://nexus.example.test")
    monkeypatch.setattr(telegram_bot, "NEXUS_GITHUB_CLIENT_ID", "id")
    monkeypatch.setattr(telegram_bot, "NEXUS_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setattr(telegram_bot, "_get_or_create_telegram_user", lambda _u: SimpleNamespace(nexus_id="n1"))
    monkeypatch.setattr(telegram_bot, "_svc_get_latest_login_session_status", lambda _nid: latest)


def test_login_without_arguments_shows_provider_menu_when_git_is_linked(monkeypatch):
    import telegram_bot

    _patch_auth(monkeypatch, telegram_bot, _ready_session())
    created = []
    monkeypatch.setattr(telegram_bot, "create_login_session_for_user", lambda **kw: created.append(kw) or "s")
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=1, username="u"))

    asyncio.run(telegram_bot.login_handler(update, SimpleNamespace(args=[])))

    assert created == []
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [b.callback_data for b in buttons if b.callback_data] == [
        "login:claude",
        "login:codex",
        "login:agy",
        "login:gemini",
    ]
    assert "Connect Antigravity" in [b.text for b in buttons]
    assert buttons[-1].url == "https://nexus.example.test/auth/setup?session=lsr_ref"


def test_login_without_arguments_starts_git_oauth_when_not_linked(monkeypatch):
    import telegram_bot

    _patch_auth(monkeypatch, telegram_bot, {"exists": True, "status": "pending", "expires_at": "2999-01-01T00:00:00+00:00"})
    monkeypatch.setattr(telegram_bot, "create_login_session_for_user", lambda **kw: "s" * 32)
    monkeypatch.setattr(telegram_bot, "_svc_format_login_session_ref", lambda sid: "lsr_new")
    monkeypatch.setattr(telegram_bot, "_svc_register_onboarding_message", lambda **kw: None)
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=1, username="u"),
        effective_chat=SimpleNamespace(id=5),
    )

    asyncio.run(telegram_bot.login_handler(update, SimpleNamespace(args=[])))

    markup = message.reply_text.await_args.kwargs["reply_markup"]
    urls = [button.url for row in markup.inline_keyboard for button in row]
    assert urls == ["https://nexus.example.test/auth/start?session=lsr_new&provider=github"]


def test_login_menu_button_starts_provider_and_links_the_account_page(monkeypatch):
    import telegram_bot

    _patch_auth(monkeypatch, telegram_bot, _ready_session())
    started = []

    def _start(*, nexus_id, provider):
        started.append((nexus_id, provider))
        return {"started": True, "state": "starting", "session_ref": "lsr_ref"}

    monkeypatch.setattr(telegram_bot, "_svc_start_provider_account_login_for_nexus", _start)
    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(data="login:claude", answer=AsyncMock(), from_user=SimpleNamespace(id=1), message=message)

    asyncio.run(telegram_bot.login_callback_handler(SimpleNamespace(callback_query=query), None))

    assert started == [("n1", "claude")]
    text = message.reply_text.await_args.args[0]
    assert "Claude sign-in started." in text
    assert "https://nexus.example.test/auth/setup?session=lsr_ref" in text
    assert "claude.com" in text


def test_login_menu_ignores_unknown_buttons(monkeypatch):
    import telegram_bot

    _patch_auth(monkeypatch, telegram_bot, _ready_session())
    monkeypatch.setattr(
        telegram_bot,
        "_svc_start_provider_account_login_for_nexus",
        lambda **kw: (_ for _ in ()).throw(AssertionError("must not start")),
    )
    message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(data="login:evil", answer=AsyncMock(), from_user=SimpleNamespace(id=1), message=message)

    asyncio.run(telegram_bot.login_callback_handler(SimpleNamespace(callback_query=query), None))

    message.reply_text.assert_not_awaited()
