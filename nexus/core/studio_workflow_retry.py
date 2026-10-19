"""Bounded, persistable retry policies for generation-only workflow nodes."""

import math


class StudioAgentFailureError(RuntimeError):
    """A provider returned an explicit unsuccessful agent result."""


def validate_retry(policy):
    if not isinstance(policy, dict) or set(policy) != {"limit", "policy", "backoff"}:
        raise ValueError("Retry policy requires limit, policy and backoff")
    if type(policy["limit"]) is not int or not 0 <= policy["limit"] <= 3:
        raise ValueError("Retry limit must be between zero and three")
    if policy["policy"] not in ("OnError", "OnFailure", "Always"):
        raise ValueError("Unsupported retry policy")
    backoff = policy["backoff"]
    if not isinstance(backoff, dict) or set(backoff) != {"seconds", "factor", "max_seconds"}:
        raise ValueError("Retry backoff requires seconds, factor and max_seconds")
    for key, low, high in [("seconds", 0, 300), ("factor", 1, 10), ("max_seconds", 0, 300)]:
        value = backoff[key]
        if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
            raise ValueError("Retry backoff exceeds supported bounds")
    if backoff["seconds"] > backoff["max_seconds"]:
        raise ValueError("Retry initial delay exceeds maximum delay")
    return policy


def retry_delay(policy, attempt):
    backoff = policy["backoff"]
    return min(backoff["seconds"] * backoff["factor"] ** attempt, backoff["max_seconds"])


def retry_matches(policy, error):
    return (
        policy["policy"] == "Always"
        or error is not None
        and (
            policy["policy"] == "OnError"
            or policy["policy"] == "OnFailure"
            and isinstance(error, StudioAgentFailureError)
        )
    )


def generation_retry_only(agent, provider=None):
    if (provider or agent.get("provider", "codex")) not in {"openai", "claude"} or agent.get(
        "tool_ids"
    ):
        raise ValueError(
            "Automatic agent retries require an API provider without tools; coding CLIs cannot be automatically retried"
        )
