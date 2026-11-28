"""Tests for Remote Toolsets and Tool SDK."""

import json
from pathlib import Path
import sys

import pytest

from nexus.core.studio_toolset_cli import (
    build_toolset,
    toolset_command,
    validate_toolset,
)
from nexus.tools.sdk import (
    ConfigSchemaField,
    SandboxToolContextFacade,
    ToolResult,
    tool_cli,
    with_config_schema,
)


def test_config_schema_field_and_decorator():
    fields = [
        ConfigSchemaField(key="api_key", description="API Key", required=True),
        ConfigSchemaField(key="timeout", description="Timeout in seconds", default=30, type="integer"),
    ]

    @with_config_schema(fields)
    def my_handler(input_data, context):
        return ToolResult(output={"received": input_data})

    assert hasattr(my_handler, "__config_schema__")
    assert len(my_handler.__config_schema__) == 2
    assert my_handler.__config_schema__[0].key == "api_key"
    assert my_handler.__config_schema__[0].required is True
    assert my_handler.__config_schema__[1].default == 30
    assert my_handler.__config_schema__[1].describe() == {
        "key": "timeout",
        "description": "Timeout in seconds",
        "default": 30,
        "type": "integer",
        "required": False,
    }


def test_tool_result_and_sandbox_context(tmp_path):
    ctx = SandboxToolContextFacade(workspace_root=tmp_path, config={"env": "test", "limit": 10})
    assert ctx.workspace_root == tmp_path.resolve()
    assert ctx.get_config("env") == "test"
    assert ctx.get_config("limit") == 10
    assert ctx.get_config("missing", "default_val") == "default_val"

    res = ToolResult(output={"count": 5}, success=True, metadata={"cached": False})
    d = res.to_dict()
    assert d["success"] is True
    assert d["output"] == {"count": 5}
    assert d["metadata"] == {"cached": False}
    assert d["error"] is None

    err_res = ToolResult(output=None, success=False, error="Invalid input")
    assert err_res.to_dict()["success"] is False
    assert err_res.to_dict()["error"] == "Invalid input"


def test_tool_cli_schema_and_invocation(capsys, tmp_path):
    fields = [ConfigSchemaField(key="multiplier", default=2, type="integer")]

    @with_config_schema(fields)
    def multiply_handler(input_data, context):
        mult = context.get_config("multiplier", 2)
        val = input_data.get("val", 0) if isinstance(input_data, dict) else int(input_data)
        return ToolResult(output={"result": val * mult})

    # Schema mode
    exit_code = tool_cli(multiply_handler, ["--schema"])
    assert exit_code == 0
    captured = capsys.readouterr()
    schema_info = json.loads(captured.out)
    assert schema_info[0]["key"] == "multiplier"

    # Execution with inline JSON
    exit_code = tool_cli(
        multiply_handler,
        ['--config', '{"multiplier": 5}', '--input', '{"val": 10}'],
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["success"] is True
    assert result["output"]["result"] == 50

    # Execution with config and input files
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({"multiplier": 3}), encoding="utf-8")
    inp_file = tmp_path / "inp.json"
    inp_file.write_text(json.dumps({"val": 7}), encoding="utf-8")

    exit_code = tool_cli(multiply_handler, ["--config", str(cfg_file), "--input", str(inp_file)])
    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["output"]["result"] == 21


def test_tool_cli_exception_handling(capsys):
    def failing_handler(input_data, context):
        raise RuntimeError("Something broke")

    exit_code = tool_cli(failing_handler, ["--input", "{}"])
    assert exit_code == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["success"] is False
    assert "Something broke" in result["error"]



def test_toolset_validation_and_build(tmp_path):
    pkg_dir = tmp_path / "sample_toolset"
    pkg_dir.mkdir()

    # Create module file
    tool_file = pkg_dir / "my_tools.py"
    tool_file.write_text(
        """from nexus.tools.sdk import with_config_schema, ConfigSchemaField, ToolResult

@with_config_schema([
    ConfigSchemaField(key="api_token", description="Service token", required=True),
    ConfigSchemaField(key="mode", default="fast", type="string")
])
def compute(input_data, context):
    return ToolResult(output={"status": "computed"})
""",
        encoding="utf-8",
    )

    manifest_file = pkg_dir / "manifest.yaml"
    manifest_file.write_text(
        """id: sample-toolset
name: Sample Toolset
version: 1.0.0
description: A sample test toolset
tools:
  - id: sample_compute
    name: Sample Compute
    description: Computes something useful
    entrypoint: my_tools:compute
""",
        encoding="utf-8",
    )

    pyproject_file = pkg_dir / "pyproject.toml"
    pyproject_file.write_text(
        """[project]
name = "sample-toolset"
version = "1.0.0"
""",
        encoding="utf-8",
    )

    summary = validate_toolset(pkg_dir)
    assert summary["valid"] is True
    assert summary["toolset_id"] == "sample-toolset"
    assert summary["version"] == "1.0.0"
    assert len(summary["tools"]) == 1
    tool_meta = summary["tools"][0]
    assert tool_meta["id"] == "sample_compute"
    assert len(tool_meta["config_schema"]) == 2
    assert tool_meta["config_schema"][0]["key"] == "api_token"
    assert tool_meta["config_schema"][0]["required"] is True

    # Test wheel build
    out_dir = tmp_path / "dist"
    wheel_path = build_toolset(pkg_dir, output_dir=out_dir)
    assert wheel_path.is_file()
    assert wheel_path.name == "sample_toolset-1.0.0-py3-none-any.whl"


def test_toolset_validation_errors(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    res = validate_toolset(empty_dir)
    assert res["valid"] is False
    assert any("No toolset manifest found" in e for e in res["errors"])

    bad_manifest = tmp_path / "bad_manifest"
    bad_manifest.mkdir()
    (bad_manifest / "manifest.yaml").write_text("tools: []", encoding="utf-8")
    res = validate_toolset(bad_manifest)
    assert res["valid"] is False
    assert any("missing required 'id'" in e for e in res["errors"])
    assert any("non-empty 'tools' list" in e for e in res["errors"])


def test_toolset_cli_command(tmp_path, capsys):
    pkg_dir = tmp_path / "cli_toolset"
    pkg_dir.mkdir()
    (pkg_dir / "manifest.yaml").write_text(
        """id: cli-toolset
version: 0.1.0
tools:
  - id: echo
    entrypoint: builtins:str
""",
        encoding="utf-8",
    )

    class Args:
        toolset_subcommand = "validate"
        path = str(pkg_dir)
        json_output = True

    code = toolset_command(Args())
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["valid"] is True
    assert data["toolset_id"] == "cli-toolset"
