"""Deployed host-bridge/container-bot feedback regressions; no live services."""

from __future__ import annotations

import ast
import asyncio
import io
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nexus.core.telegram import telegram_router_feedback_service as feedback_service

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "examples" / "nexus-bot"


def _telegram_config():
    return yaml.safe_load((BOT_DIR / "docker-compose.host.yml").read_text())["services"]["telegram"]


def test_host_bot_shares_bridge_state_without_creating_empty_directory():
    mounts = _telegram_config()["volumes"]
    state_mount = next(
        (m for m in mounts if isinstance(m, dict) and m.get("target") == "/var/lib/nexus/state"),
        None,
    )
    assert state_mount is not None, "The host bridge and container bot must share feedback tokens"
    assert state_mount["type"] == "bind"
    assert state_mount["source"] == "/var/lib/nexus/state"
    assert not state_mount.get("read_only", False)
    assert state_mount["bind"]["create_host_path"] is False


def test_bridge_token_resolves_from_persisted_shared_state(tmp_path):
    store = tmp_path / "shared-state" / "router_feedback_tokens.json"
    decision_id = "12345678-1234-4234-8234-123456789abc"
    feedback_service.register_feedback_token(
        user_id="test-user", decision_id=decision_id, store_path=str(store)
    )
    # The callback reader starts with no in-memory state. The persisted map is
    # the contract that a host bind mount must expose inside the bot container.
    assert (
        feedback_service.resolve_feedback_token(
            user_id="test-user",
            decision_ref=feedback_service.decision_token(decision_id),
            store_path=str(store),
        )
        == decision_id
    )
    assert (
        feedback_service.resolve_feedback_token(
            user_id="other-user",
            decision_ref=feedback_service.decision_token(decision_id),
            store_path=str(store),
        )
        is None
    )


def _feedback_action(monkeypatch, *, decision_id, status=200, response=None):
    # Importing the whole example starts unrelated runtime wiring. Compile the
    # real adapter function with explicit dependencies instead; do not mock its
    # token/error/confirmation logic or contact Telegram/the production router.
    tree = ast.parse((BOT_DIR / "src" / "telegram_bot.py").read_text())
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_route_feedback_action"
    )
    calls = []
    body = json.dumps(
        response if response is not None else {"ok": True, "feedback_id": "local-test-feedback"}
    ).encode()

    def urlopen(request, *, timeout):
        calls.append(json.loads(request.data))
        assert timeout == 5
        if status != 200:
            raise urllib.error.HTTPError(
                request.full_url, status, "test failure", {}, io.BytesIO(body)
            )
        result = io.BytesIO(body)
        result.status = status
        return result

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("NEXUS_ROUTER_URL", "http://local-test-router.invalid")
    namespace = {
        "asyncio": asyncio,
        "json": json,
        "logger": logging.getLogger(__name__),
        "os": os,
        "urllib": urllib,
        "resolve_feedback_token": lambda **kwargs: decision_id,
    }
    exec(
        compile(
            ast.Module(body=[node], type_ignores=[]),
            str(BOT_DIR / "src" / "telegram_bot.py"),
            "exec",
        ),
        namespace,
    )
    return namespace["_route_feedback_action"], calls


class _Ctx:
    user_id = "test-user"
    query = SimpleNamespace(message_id="test-message")

    def __init__(self, *, fail_edit=False):
        self.fail_edit = fail_edit
        self.edits = []
        self.replies = []

    async def edit_message_text(self, *, text, message_id):
        if self.fail_edit:
            raise ValueError("message no longer editable")
        self.edits.append(text)

    async def reply_text(self, text):
        self.replies.append(text)


def test_missing_feedback_token_reports_expired_without_posting(monkeypatch):
    action, calls = _feedback_action(monkeypatch, decision_id=None)
    ctx = _Ctx()
    asyncio.run(action(ctx, "missingref", "ok", None))
    assert not calls
    assert "expired or is invalid" in ctx.edits[-1]
    assert "Please try again" not in ctx.edits[-1]


@pytest.mark.parametrize(
    "action_name,task,model", [("ok", None, None), ("wrong", "reasoning", "too_cheap")]
)
def test_feedback_posts_resolved_decision_and_verdict(monkeypatch, action_name, task, model):
    action, calls = _feedback_action(monkeypatch, decision_id="test-decision")
    ctx = _Ctx()
    asyncio.run(action(ctx, "compactref", action_name, task, model))
    assert len(calls) == 1
    assert calls[0]["decision_id"] == "test-decision"
    assert calls[0]["verdict"] == ("correct" if action_name == "ok" else "wrong")
    assert calls[0]["corrected_task"] == task
    assert calls[0]["model_verdict"] == model
    assert calls[0]["source_user_id"] == ctx.user_id
    assert ctx.edits[-1].startswith("Feedback recorded:")


@pytest.mark.parametrize(
    "error,expected",
    [
        ("decision_id_not_found", "expired or is invalid"),
        ("database is locked", "Could not record routing feedback"),
    ],
)
def test_router_errors_never_claim_feedback_recorded(monkeypatch, error, expected):
    action, calls = _feedback_action(
        monkeypatch, decision_id="test-decision", status=500, response={"error": error}
    )
    ctx = _Ctx()
    asyncio.run(action(ctx, "compactref", "ok", None))
    assert len(calls) == 1
    assert expected in ctx.edits[-1]
    assert "Feedback recorded:" not in ctx.edits[-1]


def test_success_confirmation_falls_back_when_edit_fails(monkeypatch):
    action, calls = _feedback_action(monkeypatch, decision_id="test-decision")
    ctx = _Ctx(fail_edit=True)
    asyncio.run(action(ctx, "compactref", "ok", None))
    assert len(calls) == 1
    assert ctx.replies == ["Feedback recorded: correct."]
