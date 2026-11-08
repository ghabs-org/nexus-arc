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
    print("studio_routing ok")
