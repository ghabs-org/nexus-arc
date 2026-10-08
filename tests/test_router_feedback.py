import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_chat_bridge_callback_emits_feedback(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", MagicMock())
    monkeypatch.setitem(sys.modules, "redis", MagicMock())
    from nexus.core.command_bridge.router import CommandRouter

    router = CommandRouter()
    
    mock_client = MagicMock()
    mock_client.name = "telegram"
    
    class FakeMessage:
        message_id = "12345"
    class FakeRawEvent:
        message = FakeMessage()

    mock_raw_event = FakeRawEvent()
    mock_user_state = {}
    
    router.hands_free_deps = MagicMock()
    router.hands_free_deps.router_feedback_config = {"router_url": "http://test"}

    with patch("nexus.core.command_bridge.router.route_hands_free_text", new_callable=AsyncMock) as mock_route:
        mock_route.return_value = {"message": "Hello", "decision_id": "test_id"}
        
        with patch("nexus.core.command_bridge.router.maybe_send_feedback_prompt", new_callable=AsyncMock) as mock_feedback:
            cb = router._chat_bridge_callback()
            await cb(
                client=mock_client,
                user_id="user1",
                text="hello",
                args=["hello"],
                raw_event=mock_raw_event,
                attachments=None,
                user_state=mock_user_state
            )
            
            mock_route.assert_called_once()
            mock_feedback.assert_called_once()
            args, kwargs = mock_feedback.call_args
            assert kwargs["source_message_id"] == "12345"
            assert kwargs["result"] == {"message": "Hello", "decision_id": "test_id"}
            assert kwargs["feedback_config"] == {"router_url": "http://test"}


@pytest.mark.asyncio
async def test_task_confirmation_callback_emits_feedback_for_routed_result():
    pytest.importorskip("telegram")
    from nexus.core.telegram.telegram_task_capture_service import handle_task_confirmation_callback

    query = SimpleNamespace()
    query.data = "taskconfirm:confirm"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = SimpleNamespace(reply_text=AsyncMock(), chat=SimpleNamespace(id=777))

    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
    )
    context = SimpleNamespace(
        user_data={
            "pending_task_confirmation": {
                "text": "ship it",
                "message_id": "555",
                "attachments": [],
            }
        }
    )

    route_task_with_context = AsyncMock(
        return_value={
            "success": True,
            "message": "done",
            "routing_feedback": {"decision_id": "dec-1", "task_type": "coding"},
        }
    )

    with patch(
        "nexus.core.telegram.telegram_task_capture_service.maybe_send_feedback_prompt",
        new_callable=AsyncMock,
    ) as mock_feedback:
        await handle_task_confirmation_callback(
            update=update,
            context=context,
            allowed_user_ids=None,
            logger=MagicMock(),
            route_task_with_context=route_task_with_context,
            orchestrator=MagicMock(),
            get_chat=MagicMock(return_value={"metadata": {}}),
            process_inbox_task=AsyncMock(),
            requester_context_builder=lambda _user: {"nexus_id": "u-1"},
            authorize_project=None,
            router_feedback_config={"router_url": "http://router", "telegram_enabled": True},
        )

    route_task_with_context.assert_awaited_once()
    query.edit_message_text.assert_awaited_once_with("done")
    mock_feedback.assert_awaited_once()
    kwargs = mock_feedback.await_args.kwargs
    assert kwargs["result"]["routing_feedback"]["decision_id"] == "dec-1"
    assert kwargs["source_message_id"] == "555"
    assert kwargs["feedback_config"]["router_url"] == "http://router"
    assert context.user_data.get("pending_task_confirmation") is None


def test_submit_feedback_queues_then_retries(tmp_path, monkeypatch):
    """Failed POST enqueues; next call drains the outbox first (at-least-once)."""
    import urllib.error

    from nexus.core.telegram import telegram_router_feedback_service as svc

    calls = []
    failures = {"n": 1}

    class _Resp:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if failures["n"]:
            failures["n"] -= 1
            raise urllib.error.URLError("router down")
        return _Resp()

    monkeypatch.setattr(svc.urllib.request, "urlopen", _fake_urlopen)
    outbox = str(tmp_path / "outbox.json")
    payload = {"decision_id": "d1", "metadata": {"feedback_mode": "router"}}

    ok, detail = svc.submit_feedback(router_url="http://router", payload=payload, outbox_path=outbox)
    assert ok is False and "queued for retry" in detail

    ok, _ = svc.submit_feedback(router_url="http://router", payload=payload, outbox_path=outbox)
    assert ok is True
    # first send fails (1 POST), drain resends queued event + current send (2 POSTs)
    assert calls == ["http://router/feedback"] * 3

    from nexus.outbox import FileOutbox

    assert FileOutbox(outbox).pending() == []
