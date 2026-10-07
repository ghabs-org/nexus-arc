"""Tool registry and by-name ToolNode resolution."""

import asyncio


def _fresh_registry():
    from nexus.tools import ToolRegistry

    return ToolRegistry()


def test_register_and_call_sync():
    registry = _fresh_registry()
    registry.register("text:upper", lambda s: s.upper(), description="Uppercase")
    result = asyncio.run(registry.call("text:upper", "hi"))
    assert result.ok is True
    assert result.output == "HI"


def test_call_async_func():
    async def _double(n):
        return n * 2

    registry = _fresh_registry()
    registry.register("math:double", _double)
    assert asyncio.run(registry.call("math:double", 21)).output == 42


def test_unknown_tool_is_failed_result():
    registry = _fresh_registry()
    result = asyncio.run(registry.call("nope:missing"))
    assert result.ok is False
    assert "unknown tool" in (result.error or "")


def test_exception_becomes_failed_result():
    def _boom():
        raise ValueError("kaput")

    registry = _fresh_registry()
    registry.register("bad:boom", _boom)
    result = asyncio.run(registry.call("bad:boom"))
    assert result.ok is False
    assert result.error == "kaput"


def test_decorator_and_categories():
    from nexus.tools import ToolRegistry

    registry = ToolRegistry()

    @registry.tool(name="text:words", description="Count words", category="text")
    def _words(text):
        return len(str(text).split())

    assert registry.names() == ["text:words"]
    assert [s.name for s in registry.by_category("text")] == ["text:words"]
    assert registry.get("text:words").description == "Count words"


def test_tool_node_resolves_by_name():
    from nexus.tools import ToolRegistry
    from nexus.workflows.nodes import ToolNode

    registry = ToolRegistry()
    registry.register("text:shout", lambda s: f"{s}!")
    node = ToolNode("text:shout", registry=registry)
    assert node.kind == "tool"
    assert asyncio.run(node.run(None, "hey")) == "hey!"


def test_tool_node_unknown_name_raises():
    import pytest

    from nexus.tools import ToolRegistry
    from nexus.workflows.nodes import ToolNode

    node = ToolNode("nope:missing", registry=ToolRegistry())
    with pytest.raises(RuntimeError, match="unknown tool"):
        asyncio.run(node.run(None, "hey"))


def test_tool_node_callable_still_works():
    from nexus.workflows.nodes import ToolNode

    node = ToolNode(lambda: "direct")
    assert asyncio.run(node.run(None)) == "direct"


def test_builtin_tools_registered_and_working():
    import nexus.tools  # noqa: F401  (registers builtins)
    from nexus.tools import tool_registry

    for name in ("http:fetch", "text:wordcount", "text:grep", "data:query"):
        assert tool_registry.get(name) is not None, name
    assert asyncio.run(tool_registry.call("text:wordcount", "a b c")).output == 3
    assert asyncio.run(tool_registry.call("text:grep", "a\nb\na2", "a")).output == "a\na2"
    assert (
        asyncio.run(tool_registry.call("data:query", {"a": {"b": [1, 2]}}, "a.b.1")).output
        == 2
    )


def test_http_fetch_uses_requests():
    from unittest.mock import MagicMock

    import nexus.tools.builtin as _builtin

    fake_response = MagicMock()
    fake_response.text = "x" * 10
    fake_response.raise_for_status = lambda: None
    fake_requests = MagicMock()
    fake_requests.get = MagicMock(return_value=fake_response)
    original_module, original_available = _builtin._requests_module, _builtin._REQUESTS_AVAILABLE
    _builtin._requests_module, _builtin._REQUESTS_AVAILABLE = fake_requests, True
    try:
        assert asyncio.run(_builtin.tool_registry.call("http:fetch", "http://x", 5, 4)).output == "xxxx"
    finally:
        _builtin._requests_module, _builtin._REQUESTS_AVAILABLE = original_module, original_available


def test_http_fetch_missing_dep_fails_cleanly():
    import nexus.tools.builtin as _builtin

    original_module, original_available = _builtin._requests_module, _builtin._REQUESTS_AVAILABLE
    _builtin._requests_module, _builtin._REQUESTS_AVAILABLE = None, False
    try:
        result = asyncio.run(_builtin.tool_registry.call("http:fetch", "http://x"))
    finally:
        _builtin._requests_module, _builtin._REQUESTS_AVAILABLE = original_module, original_available
    assert result.ok is False
    assert "requests" in (result.error or "")


def test_disposition_resolves_by_size():
    from nexus.tools.registry import ToolResult, resolve_disposition

    assert resolve_disposition(ToolResult(ok=True, output="short")) == "inline"
    assert resolve_disposition(ToolResult(ok=True, output="x" * 4001)) == "artifact"
    assert (
        resolve_disposition(ToolResult(ok=True, output="x" * 5000, disposition="inline"))
        == "inline"
    )


def test_tool_result_passthrough():
    from nexus.tools import ToolRegistry
    from nexus.tools.registry import ToolResult

    registry = ToolRegistry()
    registry.register(
        "big:report",
        lambda: ToolResult(ok=True, output="x", disposition="artifact", mime_type="text/csv"),
    )
    result = asyncio.run(registry.call("big:report"))
    assert (result.disposition, result.mime_type) == ("artifact", "text/csv")


def test_embeds_resolve_callables_and_keep_unknowns():
    from nexus.tools.embeds import render_text, resolve_embeds

    assert resolve_embeds("at «now»", {"now": lambda: "noon"}) == "at noon"
    assert resolve_embeds("a «missing» b", {}) == "a «missing» b"
    assert resolve_embeds("x «bad»", {"bad": lambda: 1 / 0}) == "x «bad»"
    assert render_text("Hi {name}, «when»", {"name": "Al"}, {"when": "now"}) == "Hi Al, now"
    assert render_text("Hi {unknown}", {}) == "Hi {unknown}"
