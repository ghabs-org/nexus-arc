"""Rich Slack Block Kit formatting, artifact blocks, and interactive controls."""

from __future__ import annotations

import json
from typing import Any

SECTION_CHAR_LIMIT = 3000


def split_text_sections(text: str, limit: int = SECTION_CHAR_LIMIT) -> list[dict[str, Any]]:
    """Split long text into Slack section blocks without exceeding Block Kit limits."""
    if not text:
        return []
    blocks = []
    for offset in range(0, max(1, len(text)), limit):
        chunk = text[offset : offset + limit]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk},
        })
    return blocks


def build_status_context_block(
    status: str,
    run_id: str | None = None,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a Slack context block showing status, run ID, and elapsed time."""
    status_emojis = {
        "completed": "✅",
        "running": "⏳",
        "waiting": "⏸️",
        "failed": "❌",
        "cancelled": "🛑",
        "interrupted": "⚠️",
    }
    emoji = status_emojis.get(status, "ℹ️")
    elements = [
        {"type": "mrkdwn", "text": f"*Status:* {emoji} {status.title()}"}
    ]
    if run_id:
        elements.append({"type": "mrkdwn", "text": f"*Run ID:* `{run_id[:16]}`"})
    if elapsed_seconds is not None:
        elements.append({"type": "mrkdwn", "text": f"*Duration:* {elapsed_seconds:.2f}s"})
    return {"type": "context", "elements": elements}


def build_artifact_blocks(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format outbound artifacts as rich Block Kit sections."""
    if not artifacts:
        return []
    blocks = [{"type": "divider"}]
    lines = ["*Generated Artifacts:*"]
    for art in artifacts:
        name = art.get("name") or art.get("id") or "artifact"
        size = art.get("size")
        size_str = f" ({size} bytes)" if size is not None else ""
        mime = art.get("mime_type") or art.get("type") or "file"
        download_url = art.get("download_url") or art.get("url")
        if download_url:
            lines.append(f"• <{download_url}|{name}>{size_str} - `{mime}`")
        else:
            lines.append(f"• *{name}*{size_str} - `{mime}`")
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)},
    })
    return blocks


def build_feedback_action_block(
    token_positive: str,
    token_negative: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build interactive feedback buttons (Thumbs Up / Thumbs Down)."""
    return {
        "type": "actions",
        "block_id": "nexus_feedback_block",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "👍 Helpful"},
                "action_id": "slack_feedback_positive",
                "value": token_positive,
                "style": "primary",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "👎 Needs Improvement"},
                "action_id": "slack_feedback_negative",
                "value": token_negative,
            },
        ],
    }


def build_cancellation_action_block(token_cancel: str, run_id: str | None = None) -> dict[str, Any]:
    """Build task cancellation action button."""
    return {
        "type": "actions",
        "block_id": "nexus_cancel_block",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🛑 Cancel Task"},
                "action_id": "slack_cancel_task",
                "value": token_cancel,
                "style": "danger",
            }
        ],
    }


def build_rich_reply_blocks(
    text: str,
    status: str = "completed",
    run_id: str | None = None,
    elapsed_seconds: float | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    feedback_tokens: tuple[str, str] | None = None,
    cancel_token: str | None = None,
    buttons: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compose full rich Block Kit message structure."""
    blocks: list[dict[str, Any]] = []

    # 1. Main text sections
    blocks.extend(split_text_sections(text))

    # 2. Artifact summary cards
    if artifacts:
        blocks.extend(build_artifact_blocks(artifacts))

    # 3. Context footer with execution status
    blocks.append(build_status_context_block(status, run_id=run_id, elapsed_seconds=elapsed_seconds))

    # 4. Action buttons (Approval / Feedback / Cancellation)
    if buttons:
        elements = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": b.get("label", "Action")},
                "action_id": b.get("action_id", f"btn_{i}"),
                "value": b.get("callback_data", ""),
            }
            for i, b in enumerate(buttons)
        ]
        blocks.append({"type": "actions", "elements": elements})

    if cancel_token and status in {"running", "waiting"}:
        blocks.append(build_cancellation_action_block(cancel_token, run_id=run_id))
    elif feedback_tokens and status == "completed":
        blocks.append(build_feedback_action_block(feedback_tokens[0], feedback_tokens[1], run_id=run_id))

    return blocks
