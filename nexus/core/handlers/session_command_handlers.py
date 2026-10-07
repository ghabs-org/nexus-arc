"""OpenCode session inspector command handler (`/sessions`)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from nexus.core.utils.log_utils import log_unauthorized_access

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext

_DEFAULT_LIMIT = 8
_MAX_LIMIT = 25


@dataclass
class SessionsHandlerDeps:
    logger: Any
    allowed_user_ids: list[int]
    list_sessions: Callable[[int], list[dict[str, Any]]]
    default_limit: int = _DEFAULT_LIMIT


def _format_sessions(sessions: list[dict[str, Any]]) -> str:
    lines = ["🤖 *Recent OpenCode sessions*", ""]
    total_cost = 0.0
    for entry in sessions:
        title = str(entry.get("title") or "Untitled")
        if len(title) > 42:
            title = title[:41] + "…"
        updated_ms = int(entry.get("updated_ms") or 0)
        when = (
            datetime.fromtimestamp(updated_ms / 1000).strftime("%H:%M")
            if updated_ms > 0
            else "??:??"
        )
        cost = float(entry.get("cost") or 0.0)
        total_cost += cost
        lines.append(
            f"• {when} `{title}`\n"
            f"  {entry.get('model', '?')} — "
            f"${cost:.4f} "
            f"(in {int(entry.get('tokens_in') or 0):,}/"
            f"out {int(entry.get('tokens_out') or 0):,})"
        )
    lines.append("")
    lines.append(f"Showing {len(sessions)}, total cost ${total_cost:.4f}")
    return "\n".join(lines)


async def sessions_handler(ctx: InteractiveContext, deps: SessionsHandlerDeps) -> None:
    """List recent OpenCode sessions with per-session model and cost."""
    deps.logger.info("Sessions requested by user: %s", ctx.user_id)
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    limit = deps.default_limit
    if ctx.args:
        try:
            limit = max(1, min(_MAX_LIMIT, int(str(ctx.args[0]).strip())))
        except (ValueError, TypeError):
            await ctx.reply_text("Usage: `/sessions [limit]` (1-25)")
            return

    try:
        sessions = await asyncio.to_thread(deps.list_sessions, limit)
    except Exception as exc:
        deps.logger.warning("OpenCode session list failed: %s", exc)
        await ctx.reply_text(f"⚠️ Could not list OpenCode sessions: {exc}")
        return

    if not sessions:
        await ctx.reply_text("No OpenCode sessions found.")
        return
    await ctx.reply_text(_format_sessions(sessions))
