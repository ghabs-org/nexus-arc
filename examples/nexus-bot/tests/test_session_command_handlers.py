import pytest

from nexus.core.handlers import session_command_handlers as svc


class _Ctx:
    def __init__(self, user_id="7", args=None):
        self.user_id = user_id
        self.args = list(args or [])
        self.calls = []
        self.platform = "telegram"

    async def reply_text(self, text, buttons=None):
        self.calls.append(text)


def _deps(handler=None):
    sessions = [
        {
            "id": "ses_1",
            "title": "Hello world",
            "model": "opencode/spark-free",
            "cost": 0.0,
            "tokens_in": 100,
            "tokens_out": 20,
            "updated_ms": 1791356600000,
        }
    ]
    return svc.SessionsHandlerDeps(
        logger=__import__("logging").getLogger("test"),
        allowed_user_ids=[7],
        list_sessions=(handler or (lambda limit: sessions[:limit])),
    )


@pytest.mark.asyncio
async def test_sessions_handler_lists_with_cost():
    ctx = _Ctx()
    await svc.sessions_handler(ctx, _deps())
    assert len(ctx.calls) == 1
    text = ctx.calls[0]
    assert "Hello world" in text
    assert "opencode/spark-free" in text
    assert "$0.0000" in text


@pytest.mark.asyncio
async def test_sessions_handler_rejects_unauthorized():
    ctx = _Ctx(user_id="9")
    await svc.sessions_handler(ctx, _deps())
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_sessions_handler_reports_empty():
    ctx = _Ctx()
    await svc.sessions_handler(ctx, _deps(handler=lambda limit: []))
    assert ctx.calls == ["No OpenCode sessions found."]


@pytest.mark.asyncio
async def test_sessions_handler_reports_backend_error():
    def _boom(limit):
        raise RuntimeError("not logged in")

    ctx = _Ctx()
    await svc.sessions_handler(ctx, _deps(handler=_boom))
    assert "not logged in" in ctx.calls[0]
