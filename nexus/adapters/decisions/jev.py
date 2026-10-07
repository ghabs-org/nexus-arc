"""Jev decision-model client (TypeSafe System One via OpenRouter).

For choices software must act on — route, gate, classify — where a typed
answer with calibrated confidence beats parsing LLM prose::

    from nexus.adapters.decisions.jev import decide_choice

    route, confidence, sure = decide_choice(
        state="My payouts failed for 3 days",
        options={"billing": "payment and payout issues", "technical": "bugs and errors"},
        instructions="Which team owns this ticket?",
        min_confidence=0.6,
        default="technical",
    )

Requires a key: ``OPENCODE_API_KEY`` (free ``jev-1.13-free`` via OpenCode
System One, preferred) or ``OPENROUTER_API_KEY`` (``typesafe/jev-1.13`` via
OpenRouter Decisions). No key or any failure returns the safe default —
the decision layer must never break the call path.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
SYSTEMONE_URL = "https://opencode.ai/zen/v1/systemone"
DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_FREE_MODEL = "jev-1.13-free"


def resolve_backend(
    model: str | None = None,
    api_key: str | None = None,
    endpoint: str | None = None,
) -> tuple[str, str, str] | None:
    """Resolve (endpoint, key, model) for Jev: explicit args, else env.

    Prefers the free model: ``OPENCODE_API_KEY`` -> OpenCode System One
    (``jev-1.13-free``); otherwise ``OPENROUTER_API_KEY`` -> OpenRouter
    Decisions (``typesafe/jev-1.13``). Returns None when unkeyed.
    """
    if endpoint and api_key and model:
        return endpoint, api_key, model
    backend = (os.getenv("JEV_BACKEND") or "").strip().lower()
    openrouter_key = (api_key if backend == "openrouter" else None) or os.getenv(
        "OPENROUTER_API_KEY", ""
    ).strip()
    opencode_key = (api_key if backend == "opencode" else None) or os.getenv(
        "OPENCODE_API_KEY", ""
    ).strip()
    if backend == "openrouter" or (not backend and openrouter_key and not opencode_key):
        if not openrouter_key:
            return None
        return (
            DECISIONS_URL,
            openrouter_key,
            (model or os.getenv("JEV_MODEL") or DEFAULT_MODEL).strip(),
        )
    if backend == "opencode" or opencode_key:
        if not opencode_key:
            return None
        return (
            SYSTEMONE_URL,
            opencode_key,
            (model or os.getenv("JEV_MODEL") or DEFAULT_FREE_MODEL).strip(),
        )
    if openrouter_key:
        return (
            DECISIONS_URL,
            openrouter_key,
            (model or os.getenv("JEV_MODEL") or DEFAULT_MODEL).strip(),
        )
    return None


@dataclass
class ChoiceResult:
    choice: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class ScoreResult:
    position: float = 0.0
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class NoulResult:
    probability: float = 0.0


def _request(
    state: Any,
    questions: dict[str, Any],
    model: str | None = None,
    api_key: str | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    """POST one System One request; returns the ``answers`` mapping."""
    import requests as _requests

    resolved = resolve_backend(model=model, api_key=api_key)
    if resolved is None:
        raise ValueError("No Jev credentials: set OPENCODE_API_KEY or OPENROUTER_API_KEY")
    endpoint, key, resolved_model = resolved
    response = _requests.post(
        endpoint,
        json={"model": resolved_model, "state": state, "questions": questions},
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Zen sits behind Cloudflare: stdlib/bare UAs get 403/1010.
            "User-Agent": "nexus-arc/1.0 (python-requests)",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and "answers" in payload:
        return payload["answers"]
    return payload if isinstance(payload, dict) else {}


def ask(
    state: Any,
    questions: dict[str, Any],
    model: str | None = None,
    api_key: str | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    """Ask typed questions; empty dict when misconfigured or failing."""
    try:
        answers = _request(state, questions, model=model, api_key=api_key, timeout=timeout)
    except Exception as exc:
        logger.debug("Jev decision failed: %s", exc)
        return {}
    return answers if isinstance(answers, dict) else {}


def choice_question(instructions: str, options: dict[str, str]) -> dict[str, Any]:
    """Build a Choice question from option -> description mapping."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score_question(instructions: str, levels: list[str]) -> dict[str, Any]:
    """Build a Score question over ordered *levels* (low -> high)."""
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def noul_question(instructions: str) -> dict[str, Any]:
    """Build a yes/no (Noul) question."""
    return {"type": "noul", "instructions": instructions}


def parse_choice(answer: Any, default: str = "") -> ChoiceResult:
    """Parse a Choice answer dict; falls back to *default* with 0 confidence."""
    if not isinstance(answer, dict):
        return ChoiceResult(choice=default)
    probs = {
        str(option): float(score)
        for option, score in (answer.get("probabilities") or {}).items()
    }
    choice = str(answer.get("choice") or default)
    return ChoiceResult(
        choice=choice if choice in probs or not probs else default,
        probabilities=probs,
        confidence=float(answer.get("confidence") or 0.0),
    )


def decide_choice(
    state: Any,
    options: dict[str, str],
    instructions: str,
    min_confidence: float = 0.6,
    default: str = "",
    model: str | None = None,
    api_key: str | None = None,
    timeout: int = 20,
) -> tuple[str, float, bool]:
    """One-call Choice with confidence gating.

    Returns ``(choice, confidence, sure)`` where ``sure`` is False (and
    choice is *default*) whenever the call fails or confidence is low —
    route low-confidence cases to the safe default.
    """
    answers = ask(
        state, {"route": choice_question(instructions, options)},
        model=model, api_key=api_key, timeout=timeout,
    )
    result = parse_choice(answers.get("route"), default=default or next(iter(options), ""))
    if result.confidence < min_confidence:
        fallback = default or next(iter(options), "")
        return fallback, result.confidence, False
    return result.choice, result.confidence, True
