"""Reusable logging filters and setup helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any


class SecretRedactingFilter(logging.Filter):
    """Redact sensitive values from log messages and args."""

    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self._secrets = [str(secret) for secret in secrets if str(secret)]

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            redacted = value
            for secret in self._secrets:
                redacted = redacted.replace(secret, "[REDACTED_SECRET]")
            return redacted
        if isinstance(value, tuple):
            return tuple(self._redact(item) for item in value)
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.msg)
        record.args = self._redact(record.args)
        return True


def install_secret_redaction(
    secrets: Iterable[str], target_logger: logging.Logger | None = None
) -> None:
    """Attach secret redaction filter to all handlers of target logger."""
    logger = target_logger or logging.getLogger()
    redaction_filter = SecretRedactingFilter(secrets)
    for handler in logger.handlers:
        handler.addFilter(redaction_filter)


# Key names whose values must never appear in logs, even masked: masked
# values still leak length/shape, so these keys are omitted entirely.
SECRET_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GITHUB_TOKEN",
        "GITLAB_TOKEN",
        "COPILOT_GITHUB_TOKEN",
        "SLACK_TOKEN",
        "DISCORD_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "NEXUS_COMMAND_BRIDGE_AUTH_TOKEN",
        "NEXUS_OPENCLAW_BRIDGE_TOKEN",
    }
)


def omit_secret_keys(mapping: Any, extra_keys: Iterable[str] = ()) -> dict[str, Any]:
    """Return a copy of *mapping* with secret keys removed (not masked).

    Non-mapping input yields an empty dict. Use for anything crossing a log
    or API boundary: auth configs, resolved env summaries, error contexts.
    """
    if not isinstance(mapping, dict):
        return {}
    denied = set(SECRET_KEYS) | {str(key) for key in extra_keys}
    return {key: value for key, value in mapping.items() if str(key) not in denied}


# Key names whose values must never appear in logs, even masked: masked
# values still leak length/shape, so these keys are omitted entirely.
SECRET_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GITHUB_TOKEN",
        "GITLAB_TOKEN",
        "COPILOT_GITHUB_TOKEN",
        "SLACK_TOKEN",
        "DISCORD_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "NEXUS_COMMAND_BRIDGE_AUTH_TOKEN",
        "NEXUS_OPENCLAW_BRIDGE_TOKEN",
    }
)


def omit_secret_keys(mapping: Any, extra_keys: Iterable[str] = ()) -> dict[str, Any]:
    """Return a copy of *mapping* with secret keys removed (not masked).

    Non-mapping input yields an empty dict. Use for anything crossing a log
    or API boundary: auth configs, resolved env summaries, error contexts.
    """
    if not isinstance(mapping, dict):
        return {}
    denied = set(SECRET_KEYS) | {str(key) for key in extra_keys}
    return {key: value for key, value in mapping.items() if str(key) not in denied}
