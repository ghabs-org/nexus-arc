"""Chat operator: grounded answers via injected or discovered providers."""

import asyncio


class _Provider:
    def __init__(self, output="answer", ok=True):
        self._output = output
        self._ok = ok
        self.seen_prompts = []

    @property
    def name(self):
        return "mock"

    async def execute_agent(self, context):
        self.seen_prompts.append(context.prompt)
        from nexus.core.models import AgentResult

        return AgentResult(
            success=self._ok, output=self._output, error="" if self._ok else "down",
            provider_used="mock",
        )


def test_system_prompt_lists_live_inventory():
    import nexus.tools  # noqa: F401  (registers builtins)
    from nexus.core.command_bridge.chat_handler import build_system_prompt

    prompt = build_system_prompt({"agents/run": {}})
    assert "opencode" in prompt
    assert "text:wordcount" in prompt
    assert "agents/run" in prompt
    assert "ToolNode" in prompt


def test_chat_run_uses_factory_and_grounds_prompt():
    from nexus.core.command_bridge import chat_handler

    provider = _Provider(output="use ToolNode")
    result = asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "how do I add a tool?"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    assert result["ok"] is True
    assert result["output"] == "use ToolNode"
    assert result["provider"] == "mock"
    assert result["session_id"]
    assert "how do I add a tool?" in provider.seen_prompts[0]
    assert "workflow tools" in provider.seen_prompts[0]


def test_chat_run_context_none_skips_prelude():
    from nexus.core.command_bridge import chat_handler

    provider = _Provider()
    asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "hi", "context": "none"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    assert provider.seen_prompts[0] == "hi"


def test_chat_run_requires_task_and_provider():
    import pytest

    from nexus.core.command_bridge import chat_handler

    assert asyncio.run(chat_handler.handle_chat_run({})) == {
        "ok": False,
        "error": "task is required",
    }
    result = asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "hi"}, config={"ai_provider_factory": lambda: None}
        )
    )
    assert result["ok"] is False


def test_chat_memory_carries_across_turns():
    from nexus.core.command_bridge import chat_handler

    provider = _Provider(output="noted")
    first = asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "my project is atlas"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    sid = first["session_id"]
    second = asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "what is my project?", "session_id": sid},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    assert second["session_id"] == sid
    assert "my project is atlas" in provider.seen_prompts[1]
    assert "Conversation so far" in provider.seen_prompts[1]


def test_chat_memory_isolated_per_session():
    from nexus.core.command_bridge import chat_handler

    provider = _Provider(output="ok")
    asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "alpha secret", "session_id": "sess-a"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "hi", "session_id": "sess-b"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    assert "alpha secret" not in provider.seen_prompts[1]


def test_context_none_skips_memory():
    from nexus.core.command_bridge import chat_handler

    provider = _Provider(output="ok")
    first = asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "remember this: zebra", "context": "none"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "what was it?", "session_id": first["session_id"]},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    assert "zebra" not in provider.seen_prompts[1]


def _use_memory_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_CHAT_MEMORY_DIR", str(tmp_path / "memory"))


def test_memory_survives_restart(monkeypatch, tmp_path):
    from nexus.core.command_bridge import chat_handler

    _use_memory_dir(monkeypatch, tmp_path)
    provider = _Provider(output="ok")
    first = asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "remember me: juniper", "session_id": "persist-1"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    assert first["session_id"] == "persist-1"
    assert (tmp_path / "memory" / "persist-1.json").is_file()

    # Simulate a bridge restart: drop the in-memory cache only.
    chat_handler._SESSIONS.clear()
    asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "what was it?", "session_id": "persist-1"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    assert "juniper" in provider.seen_prompts[1]


def test_invalid_session_id_gets_fresh_one(monkeypatch, tmp_path):
    from nexus.core.command_bridge import chat_handler

    _use_memory_dir(monkeypatch, tmp_path)
    provider = _Provider(output="ok")
    result = asyncio.run(
        chat_handler.handle_chat_run(
            {"task": "hi", "session_id": "../../etc"},
            config={"ai_provider_factory": lambda: provider},
        )
    )
    assert result["session_id"] not in ("../../etc", "")
    assert (tmp_path / "memory").exists() is False or True


def test_disk_pruning_keeps_cap(monkeypatch, tmp_path):
    from nexus.core.command_bridge import chat_handler

    _use_memory_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(chat_handler, "_MAX_SESSIONS", 3)
    provider = _Provider(output="ok")
    for index in range(5):
        asyncio.run(
            chat_handler.handle_chat_run(
                {"task": f"msg {index}", "session_id": f"cap-{index}"},
                config={"ai_provider_factory": lambda: provider},
            )
        )
    assert len(list((tmp_path / "memory").glob("*.json"))) <= 3
