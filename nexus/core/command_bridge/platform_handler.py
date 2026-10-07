"""Platform inventory: one pane over providers and tools (GET /api/v1/platform/inventory).

The self-hosted counterpart to a vendor control plane: no accounts, no
tenancy, just live answers from this deployment about what it can do.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _provider_availability() -> list[dict[str, Any]]:
    """Best-effort availability per known provider; failures mean unavailable."""
    from nexus.init_project import PROVIDERS

    entries: dict[str, Any] = {}
    try:
        from nexus.adapters.ai.claude_provider import ClaudeProvider
        entries["claude"] = ClaudeProvider
    except Exception as exc:
        logger.debug("ClaudeProvider init failed: %s", exc)
    try:
        from nexus.adapters.ai.copilot_provider import CopilotCLIProvider
        entries["copilot"] = CopilotCLIProvider
    except Exception as exc:
        logger.debug("CopilotCLIProvider init failed: %s", exc)
    try:
        from nexus.adapters.ai.gemini_provider import GeminiCLIProvider
        entries["gemini"] = GeminiCLIProvider
    except Exception as exc:
        logger.debug("GeminiCLIProvider init failed: %s", exc)
    try:
        from nexus.adapters.ai.codex_provider import CodexCLIProvider
        entries["codex"] = CodexCLIProvider
    except Exception as exc:
        logger.debug("CodexCLIProvider init failed: %s", exc)
    try:
        from nexus.adapters.ai.openai_provider import OpenAIProvider
        entries["openai"] = OpenAIProvider
    except Exception as exc:
        logger.debug("OpenAIProvider init failed: %s", exc)
    try:
        from nexus.adapters.ai.opencode_provider import OpenCodeProvider
        entries["opencode"] = OpenCodeProvider
    except Exception as exc:
        logger.debug("OpenCodeProvider init failed: %s", exc)

    inventory = []
    for name in PROVIDERS:
        cls = entries.get(name)
        available = False
        if cls is not None:
            try:
                available = bool(await cls().check_availability())
            except Exception as exc:
                logger.debug("%s.check_availability() failed: %s", name, exc)
        inventory.append({"name": name, "available": available})
    return inventory


def _tool_inventory() -> list[dict[str, Any]]:
    from nexus.tools import tool_registry

    items = []
    for name in tool_registry.names():
        spec = tool_registry.get(name)
        if spec is None:
            continue
        items.append(
            {"name": spec.name, "description": spec.description, "category": spec.category}
        )
    return items


async def handle_platform_inventory(
    payload: dict[str, Any], config: dict | None = None
) -> dict[str, Any]:
    """Entry point for GET /api/v1/platform/inventory."""
    del payload, config
    return {
        "ok": True,
        "providers": await _provider_availability(),
        "tools": _tool_inventory(),
    }
