"""Named tool registry for Nexus ARC workflows.

Tools are plain Python callables (sync or async) registered by name and
invoked by :class:`ToolNode` in workflow graphs::

    from nexus.tools import tool, tool_registry

    @tool(name="text:wordcount", description="Count words in a string")
    def wordcount(text: str) -> int:
        return len(str(text or "").split())

    await tool_registry.call("text:wordcount", "hello world")  # -> 2

Names follow a ``<namespace>:<verb>`` convention (``vcs:read_issue``,
``http:fetch``, ``text:wordcount``) so workflow YAML stays readable.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """A registered tool: name, docs, and the backing callable."""

    name: str
    func: Callable[..., Any]
    description: str = ""
    category: str = ""


@dataclass
class ToolResult:
    """Uniform tool outcome; failures carry ``error`` instead of raising.

    ``disposition`` hints how callers should deliver ``output``: ``inline``
    (embed in the reply), ``artifact`` (persist + reference), or ``auto``
    (decide by size via :func:`resolve_disposition`). Tools may return a
    :class:`ToolResult` directly to control this; plain values default to
    ``auto``/inline-friendly.
    """

    ok: bool
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    disposition: str = "auto"
    mime_type: str = ""


def resolve_disposition(result: ToolResult, inline_limit: int = 4000) -> str:
    """Resolve ``auto`` to ``inline`` or ``artifact`` by payload size."""
    if result.disposition in {"inline", "artifact"}:
        return result.disposition
    output = result.output
    size = len(output) if isinstance(output, (bytes, str)) else len(str(output or ""))
    return "artifact" if size > inline_limit else "inline"


class ToolRegistry:
    """Name -> ToolSpec store with sync/async invocation."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        func: Callable[..., Any],
        description: str = "",
        category: str = "",
    ) -> ToolSpec:
        """Register *func* under *name* (overwrites with a warning)."""
        if not callable(func):
            raise ValueError(f"tool '{name}' func must be callable")
        if name in self._tools:
            logger.warning("Tool '%s' is already registered. Overwriting.", name)
        spec = ToolSpec(name=name, func=func, description=description, category=category)
        self._tools[name] = spec
        return spec

    def tool(
        self,
        name: str,
        description: str = "",
        category: str = "",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of :meth:`register`."""

        def _wrap(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register(name, func, description=description, category=category)
            return func

        return _wrap

    def get(self, name: str) -> ToolSpec | None:
        """Return the spec for *name*, or None when unknown."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """All registered tool names, sorted."""
        return sorted(self._tools)

    def by_category(self, category: str) -> list[ToolSpec]:
        """All tools in *category*."""
        return [spec for spec in self._tools.values() if spec.category == category]

    def clear(self) -> None:
        """Drop all registrations (testing only)."""
        self._tools.clear()

    async def call(self, name: str, *args: Any, **kwargs: Any) -> ToolResult:
        """Invoke tool *name*; unknown names and exceptions become failed results.

        Tools returning :class:`ToolResult` pass through untouched (so they
        control ``disposition``/``mime_type``); plain values are wrapped.
        """
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(ok=False, error=f"unknown tool: {name}")
        try:
            output = spec.func(*args, **kwargs)
            if inspect.isawaitable(output):
                output = await output
            if isinstance(output, ToolResult):
                return output
            return ToolResult(ok=True, output=output)
        except Exception as exc:
            logger.warning("Tool '%s' failed: %s", name, exc)
            return ToolResult(ok=False, error=str(exc))


tool_registry = ToolRegistry()
tool = tool_registry.tool
