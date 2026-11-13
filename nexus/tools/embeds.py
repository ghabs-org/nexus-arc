"""Dynamic embeds: late-bound ``«name»`` placeholders in prompts and replies.

Unlike static ``{variable}`` substitution (see ``WorkflowEngine.render_prompt``),
embed values may be zero-argument callables evaluated at render time — handy
for timestamps, artifact contents, or per-render counters. Unknown names and
failing callables are left intact, never raised.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

_EMBED_RE = re.compile(r"«([^»<>]+)»")


def resolve_embeds(text: str, values: Mapping[str, Any]) -> str:
    """Replace ``«name»`` with ``values[name]`` (calling it when callable)."""

    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1).strip()
        if name not in values:
            return match.group(0)
        value = values[name]
        try:
            resolved = value() if callable(value) else value
        except Exception:
            return match.group(0)
        return str(resolved)

    return _EMBED_RE.sub(_replace, str(text or ""))


def render_text(
    template: str,
    variables: Mapping[str, Any] | None = None,
    embeds: Mapping[str, Any] | None = None,
) -> str:
    """Static ``{variable}`` substitution (safe-missing) plus ``«embed»`` pass."""

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    rendered = str(template or "").format_map(_SafeDict(dict(variables or {})))
    return resolve_embeds(rendered, embeds or {})
