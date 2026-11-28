"""Direct TypeSafe Decision API (System One).

Connects directly to TypeSafe's System One API (POST https://api.typesafe.ai/v1/systemone)
for calibrated typed decisions without third-party routing.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any

from nexus.adapters.decisions.errors import DecisionAnswers, DecisionCallError
from nexus.adapters.decisions.http import metadata, request_json

SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13"


def ask_strict_once(
    state: Any,
    questions: dict[str, Any],
    model: str | None = None,
    api_key: str | None = None,
    timeout: int | float = 20,
    endpoint: str | None = None,
    *,
    image_urls: list[str] | None = None,
) -> DecisionAnswers:
    """Single bounded request to TypeSafe System One."""
    key = api_key or os.getenv("TYPESAFE_API_KEY", "").strip()
    if not isinstance(key, str) or not key:
        raise DecisionCallError("config")
    resolved_model = (model or DEFAULT_MODEL).strip()
    if not resolved_model:
        raise DecisionCallError("config")

    # TypeSafe System One is a text/structured state decision model; image inputs are rejected.
    if image_urls:
        raise DecisionCallError("config")

    url = endpoint or SYSTEMONE_URL
    body = request_json(
        url,
        key,
        {"model": resolved_model, "state": state, "questions": questions},
        timeout,
        max_bytes=65536,
    )
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise DecisionCallError("invalid_response")
    return metadata(DecisionAnswers(body["answers"]), body)


def ask_strict(
    state: Any,
    questions: dict[str, Any],
    model: str | None = None,
    api_key: str | None = None,
    timeout: int | float = 20,
    endpoint: str | None = None,
    *,
    image_urls: list[str] | None = None,
) -> DecisionAnswers:
    """TypeSafe System One decision with bounded transient retry budget (max 3 attempts)."""
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise DecisionCallError("config")
    deadline = time.monotonic() + timeout
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DecisionCallError("timeout")
        try:
            return ask_strict_once(
                state,
                questions,
                model=model,
                api_key=api_key,
                timeout=remaining,
                endpoint=endpoint,
                image_urls=image_urls,
            )
        except DecisionCallError as error:
            delay = 0.1 * 2**attempt
            if (
                error.category not in {"rate_limited", "unreachable"}
                or attempt == 2
                or deadline - time.monotonic() <= delay
            ):
                raise
            time.sleep(delay)


def ask(
    state: Any,
    questions: dict[str, Any],
    model: str | None = None,
    api_key: str | None = None,
    timeout: int | float = 20,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Safe fallback ask: returns empty dict on error."""
    try:
        answers = ask_strict_once(
            state, questions, model=model, api_key=api_key, timeout=timeout, endpoint=endpoint
        )
        return dict(answers) if isinstance(answers, dict) else {}
    except Exception:
        return {}
