"""Default model suggestions for each Studio provider.

These are suggestions, not an allow-list: any model ID the provider accepts can
still be typed or saved as a model profile. Lists come from each provider's own
listing (Codex models cache, ``agy models``, ``opencode models``) or published
model IDs, and should be refreshed when providers ship new models.
"""

from __future__ import annotations

_CLAUDE = (
    ("claude-opus-5-5", "Claude Opus 5.5"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
    ("claude-fable-5-1", "Claude Fable 5.1"),
)
_OPENAI = (
    ("gpt-6.1-sol", "GPT-6.1-Sol"),
    ("gpt-6-astra", "GPT-6-Astra"),
    ("gpt-6-sol", "GPT-6-Sol"),
    ("gpt-6-luna", "GPT-6-Luna"),
    ("gpt-5.6-sol", "GPT-5.6-Sol"),
    ("gpt-5.6-terra", "GPT-5.6-Terra"),
    ("gpt-5.6-luna", "GPT-5.6-Luna"),
)

DEFAULT_MODELS: dict[str, tuple[tuple[str, str], ...]] = {
    "codex": _OPENAI,
    "openai": _OPENAI,
    "claude-code": _CLAUDE,
    "claude": _CLAUDE,
    # Antigravity model IDs carry their effort level.
    "agy": (
        ("gemini-4-argon-high", "Gemini 4 Argon (High)"),
        ("gemini-4-argon-medium", "Gemini 4 Argon (Medium)"),
        ("gemini-4-argon-low", "Gemini 4 Argon (Low)"),
        ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
        ("gemini-3.8-flash-medium", "Gemini 3.8 Flash (Medium)"),
        ("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)"),
        ("claude-opus-5-5-high", "Claude Opus 5.5 (High)"),
        ("claude-opus-5-5-medium", "Claude Opus 5.5 (Medium)"),
        ("claude-opus-5-5-low", "Claude Opus 5.5 (Low)"),
        ("claude-sonnet-5-5-high", "Claude Sonnet 5.5 (High)"),
        ("claude-sonnet-5-5-medium", "Claude Sonnet 5.5 (Medium)"),
        ("claude-sonnet-5-5-low", "Claude Sonnet 5.5 (Low)"),
        ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"),
    ),
    # Copilot has no listing command; "auto" lets it choose per request.
    "copilot": (("auto", "Auto"),),
    "opencode": tuple(
        (f"opencode/{model}", label)
        for model, label in (
            *_CLAUDE,
            ("gpt-6.1-sol", "GPT-6.1-Sol"),
            ("gpt-6-sol", "GPT-6-Sol"),
            ("gpt-5.6-sol", "GPT-5.6-Sol"),
            ("gemini-4-argon", "Gemini 4 Argon"),
            ("gemini-3.8-flash", "Gemini 3.8 Flash"),
        )
    ),
}


def default_models(provider: str) -> list[dict]:
    """Return ``[{"id", "name"}]`` suggestions for *provider*, empty when unknown."""
    return [{"id": model, "name": name} for model, name in DEFAULT_MODELS.get(provider, ())]
