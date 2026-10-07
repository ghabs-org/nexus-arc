"""Named workflow tools: registry, result type, and shared instance."""

from nexus.tools.registry import ToolRegistry, ToolResult, ToolSpec, tool, tool_registry
from nexus.tools import builtin as builtin_tools  # noqa: F401  (registers builtins)

__all__ = ["ToolRegistry", "ToolResult", "ToolSpec", "builtin_tools", "tool", "tool_registry"]
