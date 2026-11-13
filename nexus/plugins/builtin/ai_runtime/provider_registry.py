"""Compatibility exports for the framework's provider-preference parser."""

from nexus.core.provider_preferences import (
    ToolPreferenceSpec,
    parse_provider,
    parse_tool_preference,
    supports_analysis,
    unique_tools,
)

__all__ = [
    "ToolPreferenceSpec", "parse_provider", "parse_tool_preference",
    "supports_analysis", "unique_tools",
]
