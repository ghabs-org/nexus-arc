"""Studio <-> nexus-router glue: provider ids, Auto candidates, fallback rules.

nexus-router speaks OpenClaw provider ids ("openai-codex/gpt-6.1-sol"); Studio runs
its own providers ("codex" + model "gpt-6.1-sol"). Pure functions, no I/O.
"""

from __future__ import annotations

import re

AUTO = "auto"

# Router provider -> Studio providers that can run its models, preferred first
# (subscription CLI before the metered API).
ROUTER_TO_STUDIO = {
    "openai-codex": ("codex",),
    "anthropic": ("claude-code", "claude"),
    "github-copilot": ("copilot",),
    "google-antigravity": ("agy",),
    "opencode": ("opencode",),
    "openai": ("openai",),
}
STUDIO_TO_ROUTER = {
    studio: router for router, studios in ROUTER_TO_STUDIO.items() for studio in studios
}

# Provider output that means "this account is out of quota / throttled": worth
# trying the next provider. Same wording llm-cli-proxy treats as a 429.
LIMIT = re.compile(
    r"usage limit|rate limit|rate-limited|hit your limit|limit reached|quota|\b429\b|too many requests",
    re.I,
)


def allowed_router_providers(ready: list[str]) -> list[str]:
    """Router provider ids for the Studio providers this user can run."""
    return sorted({STUDIO_TO_ROUTER[name] for name in ready if name in STUDIO_TO_ROUTER})


def candidates(decision: dict, ready: list[str]) -> list[tuple[str, str | None]]:
    """Ordered (studio_provider, model) pairs from a router decision.

    The selected model first, then the router's fallbacks; each mapped to the
    first ready Studio provider for its router provider. Unmappable ids are skipped.
    """
    ready_set = set(ready)
    pairs: list[tuple[str, str | None]] = []
    for model_id in [decision.get("selected_model"), *(decision.get("fallbacks") or [])]:
        if not isinstance(model_id, str) or "/" not in model_id:
            continue
        router_provider, model = model_id.split("/", 1)
        studio = next(
            (s for s in ROUTER_TO_STUDIO.get(router_provider, ()) if s in ready_set), None
        )
        if studio and (studio, model) not in pairs:
            pairs.append((studio, model))
    return pairs


def router_model_id(provider: str, model: str | None) -> str | None:
    """Studio (provider, model) back to the router's provider/model id, for /outcome."""
    router_provider = STUDIO_TO_ROUTER.get(provider)
    return f"{router_provider}/{model}" if router_provider and model else None


def is_limit_error(text: str | None) -> bool:
    return bool(text and LIMIT.search(text))


# Text-only families among the default model suggestions; everything else takes images.
_TEXT_ONLY = ("gpt-oss", "glm-", "/auto")


def caller_catalog(ready: list[str]) -> list[dict]:
    """The models ready providers run today, as router ids, for /route ``available_models``.

    Studio's model lists are newer than any router registry snapshot, so the router
    scores exactly these (unknown ids from family priors) instead of stale entries.
    """
    from nexus.core.studio_models import DEFAULT_MODELS

    seen, catalog = set(), []
    for provider in ready:
        router_provider = STUDIO_TO_ROUTER.get(provider)
        for model, _label in DEFAULT_MODELS.get(provider, ()):
            model_id = f"{router_provider}/{model}" if router_provider else None
            if model_id and model_id not in seen:
                seen.add(model_id)
                catalog.append({"id": model_id, "vision": not any(t in model_id for t in _TEXT_ONLY)})
    return catalog[:200]


def conversation_context(history, turns: int = 8, limit: int = 2000) -> str | None:
    """Recent turns for the router's classifier (used for classification, never stored)."""
    if not history:
        return None
    text = "\n".join(
        f"{entry.get('role')}: {str(entry.get('content') or '')[:600]}" for entry in history[-turns:]
    )
    return text[-limit:] or None


# Studio CLI providers run on flat-rate logins; their marginal per-call cost is 0.
SUBSCRIPTION_CLIS = {"codex", "claude-code", "agy", "copilot", "opencode"}


def estimate_cost(table, provider, model, prompt_tokens, completion_tokens):
    """(usd, source) for a usage row, from the router's /pricing table; (None, None) if unpriced."""
    import fnmatch

    if provider in SUBSCRIPTION_CLIS:
        return 0.0, "subscription"
    model_id = router_model_id(provider, model) if model and model != "default" else None
    models = (table or {}).get("models") or {}
    if not model_id or not models:
        return None, None
    price = models.get(model_id)
    if price is None:
        matches = [pattern for pattern in models if fnmatch.fnmatchcase(model_id, pattern)]
        price = models[max(matches, key=len)] if matches else None
    if price is None:
        return None, None
    usd = (
        int(prompt_tokens or 0) * float(price.get("input_per_mtok", 0))
        + int(completion_tokens or 0) * float(price.get("output_per_mtok", 0))
    ) / 1e6
    return round(usd, 6), "tariff"


def trim_explain(explain) -> dict | None:
    """What Studio keeps of a router explanation: rules, task, top three candidates."""
    if not isinstance(explain, dict):
        return None
    classifier = explain.get("classifier") or {}
    return {
        "rules": list(explain.get("rules_triggered") or [])[:12],
        "task": classifier.get("effective_task_type") or classifier.get("task_type"),
        "confidence": classifier.get("confidence"),
        "classifier": classifier.get("source"),
        "candidates": [
            {"model": item.get("model"), "score": item.get("total")}
            for item in (explain.get("model_scores") or [])[:3]
        ],
        "policy": explain.get("policy_id"),
    }


if __name__ == "__main__":
    decision = {
        "selected_model": "anthropic/claude-opus-5-5",
        "fallbacks": ["openai-codex/gpt-6.1-sol", "nvidia/x", "anthropic/claude-opus-5-5", "bad"],
    }
    assert candidates(decision, ["codex", "claude"]) == [
        ("claude", "claude-opus-5-5"),
        ("codex", "gpt-6.1-sol"),
    ]
    assert candidates(decision, ["claude-code", "claude"])[0] == ("claude-code", "claude-opus-5-5")
    assert allowed_router_providers(["codex", "claude", "claude-code", "x"]) == [
        "anthropic",
        "openai-codex",
    ]
    assert (
        router_model_id("claude-code", "m") == "anthropic/m" and router_model_id("x", "m") is None
    )
    assert is_limit_error("ERROR: You've hit your usage limit.") and not is_limit_error(
        "syntax error"
    )
    catalog = caller_catalog(["claude-code", "copilot", "agy"])
    assert {"id": "anthropic/claude-opus-5-5", "vision": True} in catalog
    assert {"id": "github-copilot/auto", "vision": False} in catalog
    assert {"id": "google-antigravity/gpt-oss-120b-medium", "vision": False} in catalog
    history = [{"role": "user", "content": "x" * 900}] * 20
    assert len(conversation_context(history)) <= 2000 and conversation_context([]) is None
    trimmed = trim_explain(
        {"rules_triggered": ["a"], "classifier": {"task_type": "coding", "confidence": 0.9},
         "model_scores": [{"model": "m", "total": 0.8}] * 5}
    )
    assert trimmed["task"] == "coding" and len(trimmed["candidates"]) == 3
    print("studio_routing ok")
