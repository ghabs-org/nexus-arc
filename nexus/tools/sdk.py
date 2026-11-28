"""Remote Toolsets and Tool SDK for Nexus Studio.

Provides @with_config_schema, ConfigSchemaField, SandboxToolContextFacade,
ToolResult, and standard tool_cli entrypoint for packaging and validating tools.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Union


@dataclass(frozen=True)
class ConfigSchemaField:
    """Typed declaration for a tool configuration setting."""

    key: str
    description: str = ""
    default: Any = ""
    type: str = "string"
    required: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "description": self.description,
            "default": self.default,
            "type": self.type,
            "required": self.required,
        }


def with_config_schema(fields: Sequence[ConfigSchemaField]) -> Callable[[Any], Any]:
    """Decorator attaching configuration schema metadata to a tool callable or class."""

    def decorator(target: Any) -> Any:
        setattr(target, "__config_schema__", list(fields))
        return target

    return decorator


@dataclass
class ToolResult:
    """Standardized output returned by tools built with the Tool SDK."""

    output: Any
    success: bool = True
    metadata: Optional[dict[str, Any]] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "success": self.success,
            "metadata": self.metadata or {},
            "error": self.error,
        }


class SandboxToolContextFacade:
    """Restricted execution context exposed to sandbox tools."""

    def __init__(
        self,
        workspace_root: Optional[Union[str, Path]] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd()
        self.config = config or {}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)


def tool_cli(
    handler: Callable[[Any, Any], Union[ToolResult, Any]],
    args: Optional[Sequence[str]] = None,
) -> int:
    """CLI driver executing a tool function from arguments or standard input."""
    parser = argparse.ArgumentParser(description="Tool SDK CLI invocation")
    parser.add_argument("--config", help="JSON string or path to tool configuration", default="{}")
    parser.add_argument("--input", help="JSON string or path to tool arguments", default="{}")
    parser.add_argument("--schema", action="store_true", help="Print configuration schema and exit")
    parsed = parser.parse_args(args)

    if parsed.schema:
        schema_fields = getattr(handler, "__config_schema__", [])
        print(json.dumps([f.describe() if hasattr(f, "describe") else f for f in schema_fields], indent=2))
        return 0

    try:
        config_data = (
            json.loads(Path(parsed.config).read_text(encoding="utf-8"))
            if Path(parsed.config).is_file()
            else json.loads(parsed.config)
        )
    except Exception:
        config_data = {}

    try:
        input_data = (
            json.loads(Path(parsed.input).read_text(encoding="utf-8"))
            if Path(parsed.input).is_file()
            else json.loads(parsed.input)
        )
    except Exception:
        input_data = parsed.input

    context = SandboxToolContextFacade(config=config_data)
    try:
        result = handler(input_data, context)
        if isinstance(result, ToolResult):
            output = result.to_dict()
        elif isinstance(result, dict) and "output" in result:
            output = result
        else:
            output = ToolResult(output=result).to_dict()
        print(json.dumps(output))
        return 0 if output.get("success", True) else 1
    except Exception as exc:
        print(json.dumps(ToolResult(output=None, success=False, error=str(exc)).to_dict()))
        return 1



