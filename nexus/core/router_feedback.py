"""Router feedback vocabulary and payload shape, shared by Telegram, Discord and Studio.

No configuration or transport imports: Studio runs without the bot's project config.
"""

from __future__ import annotations

import time
from typing import Any

TASK_LABELS = [
    "coding",
    "code_review",
    "reasoning",
    "summarization",
    "fast_utility",
    "long_context",
    "vision",
    "general_chat",
]

# Model-verdict options surfaced in step 2 of the "Wrong" flow
MODEL_VERDICT_LABELS: dict[str, str] = {
    "too_cheap": "🔼 Too cheap/fast",
    "ok": "✅ Model OK",
    "too_powerful": "🔽 Too powerful/slow",
}
MODEL_VERDICTS = list(MODEL_VERDICT_LABELS.keys())


def build_feedback_payload(
    *,
    meta: dict[str, Any],
    verdict: str,
    corrected_task: str | None,
    source_message_id: str | None,
    source_user_id: str | None,
    model_verdict: str | None = None,
    source_surface: str = "telegram",
) -> dict[str, Any]:
    metadata = dict(meta.get("metadata") or {}) if isinstance(meta.get("metadata"), dict) else {}
    metadata.update(
        {
            "feedback_mode": meta.get("feedback_mode") or "router",
            "source_chat_id": str(meta.get("source_chat_id") or "") or None,
            "feedback_message_id": str(meta.get("feedback_message_id") or "") or None,
            "task_type": str(meta.get("task_type") or "") or None,
            "selected_model": str(meta.get("selected_model") or "") or None,
            "confidence": meta.get("confidence"),
            "submitted_at": int(time.time()),
        }
    )
    return {
        "decision_id": meta.get("decision_id"),
        "verdict": verdict,
        "corrected_task": corrected_task,
        "model_verdict": model_verdict or None,
        "source_surface": source_surface,
        "source_channel": meta.get("source_channel") or source_surface,
        "source_message_id": str(source_message_id or meta.get("source_message_id") or "") or None,
        "source_user_id": str(source_user_id or meta.get("source_user_id") or "") or None,
        "metadata": metadata,
    }
