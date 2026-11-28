"""Builtin workflow tools, registered on import.

Kept to dependency-free (stdlib) tools plus one ``requests``-backed fetch;
``http:fetch`` returns a failed result (not an import error) when
``requests`` is unavailable.
"""

from __future__ import annotations

import json as _json
from typing import Any

from nexus.tools.registry import tool_registry

try:
    import requests as _requests_module

    _REQUESTS_AVAILABLE = True
except ImportError:
    _requests_module = None  # type: ignore[assignment]
    _REQUESTS_AVAILABLE = False


@tool_registry.tool(
    name="http:fetch",
    description="GET a URL and return truncated text (max_chars, default 20000)",
    category="http",
)
def http_fetch(url: str, timeout: int = 30, max_chars: int = 20000) -> str:
    if not _REQUESTS_AVAILABLE:
        raise ImportError("requests is required for http:fetch. Install it with: pip install requests")
    response = _requests_module.get(str(url), timeout=int(timeout))
    response.raise_for_status()
    text = response.text or ""
    if len(text) > max_chars:
        return text[:max_chars]
    return text


@tool_registry.tool(
    name="text:wordcount",
    description="Count words in a string",
    category="text",
)
def text_wordcount(text: str) -> int:
    return len(str(text or "").split())


@tool_registry.tool(
    name="text:grep",
    description="Return lines of text matching a substring (case-insensitive)",
    category="text",
)
def text_grep(text: str, pattern: str) -> str:
    needle = str(pattern or "").lower()
    return "\n".join(
        line for line in str(text or "").splitlines() if needle in line.lower()
    )


@tool_registry.tool(
    name="data:query",
    description="Extract dotted path (a.b.0) from a dict or JSON string",
    category="data",
)
def data_query(data: Any, path: str) -> Any:
    current: Any = data
    if isinstance(current, str):
        current = _json.loads(current)
    for part in str(path or "").split("."):
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(f"data:query: missing key '{part}'")
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise KeyError(f"data:query: bad index '{part}'") from exc
        else:
            raise KeyError(f"data:query: cannot descend into {type(current).__name__}")
    return current
