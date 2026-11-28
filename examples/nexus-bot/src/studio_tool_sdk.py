"""Exercise Remote Toolsets, Tool SDK, and Report-Template lifecycle.

Run from repository root:
PYTHONPATH=. .venv/bin/python3 examples/nexus-bot/src/studio_tool_sdk.py
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from nexus.core.studio_report_templates import (
    instantiate_report_template,
    package_report_template,
    read_report_template,
    unpack_report_template,
    verify_report_template,
)
from nexus.core.studio_toolset_cli import (
    build_toolset,
    validate_toolset,
)
from nexus.tools.sdk import (
    ConfigSchemaField,
    SandboxToolContextFacade,
    ToolResult,
    tool_cli,
    with_config_schema,
)


def run_tool_sdk_demo(workspace: Path) -> None:
    print("=== Step 1: Author tool with @with_config_schema and execute ===")

    schema = [
        ConfigSchemaField(
            key="api_key",
            description="Scoped API Key",
            type="string",
            required=True,
        ),
        ConfigSchemaField(
            key="precision",
            description="Rounding precision",
            type="integer",
            default=2,
        ),
    ]

    @with_config_schema(schema)
    def calculate_metrics(input_data: dict, context: SandboxToolContextFacade) -> ToolResult:
        precision = context.get_config("precision", 2)
        values = input_data.get("values", [])
        avg = round(sum(values) / len(values), precision) if values else 0.0
        return ToolResult(
            output={"count": len(values), "average": avg},
            metadata={"precision_used": precision},
        )

    # Test schema inspection
    inspect_result = [f.describe() for f in getattr(calculate_metrics, "__config_schema__", [])]
    print(f"Tool declared schema: {json.dumps(inspect_result, indent=2)}")

    # Execute directly via Tool SDK context
    ctx = SandboxToolContextFacade(workspace_root=workspace, config={"api_key": "k-12345", "precision": 3})
    res = calculate_metrics({"values": [10.1234, 20.5678, 30.9999]}, ctx)
    print(f"Execution result: {json.dumps(res.to_dict())}")
    assert res.success is True
    assert res.output["count"] == 3
    assert res.output["average"] == 20.564


def run_toolset_packaging_demo(workspace: Path) -> None:
    print("\n=== Step 2: Validate and build toolset wheel package ===")
    toolset_dir = workspace / "custom_analytics"
    toolset_dir.mkdir(parents=True, exist_ok=True)

    # Write tool source module
    module_code = """from nexus.tools.sdk import with_config_schema, ConfigSchemaField, ToolResult

@with_config_schema([
    ConfigSchemaField(key="threshold", description="Alert threshold", default=80, type="integer")
])
def analyze_logs(input_data, context):
    threshold = context.get_config("threshold", 80)
    score = input_data.get("score", 0)
    return ToolResult(output={"alert": score >= threshold, "score": score})
"""
    (toolset_dir / "analytics.py").write_text(module_code, encoding="utf-8")

    # Write manifest
    manifest_yaml = """id: custom-analytics
name: Custom Analytics Toolset
version: 1.2.0
description: Packaged custom analysis tools for Studio
tools:
  - id: analyze_logs
    name: Log Analysis
    description: Scan logs and evaluate severity threshold
    entrypoint: analytics:analyze_logs
"""
    (toolset_dir / "manifest.yaml").write_text(manifest_yaml, encoding="utf-8")

    # Validate
    validation = validate_toolset(toolset_dir)
    print(f"Toolset validation valid={validation['valid']}, tools={len(validation['tools'])}")
    assert validation["valid"] is True
    assert validation["toolset_id"] == "custom-analytics"

    # Build wheel
    dist_dir = workspace / "dist"
    wheel = build_toolset(toolset_dir, output_dir=dist_dir)
    print(f"Built toolset wheel: {wheel.name} ({wheel.stat().st_size} bytes)")
    assert wheel.is_file()
    assert "custom_analytics-1.2.0" in wheel.name


def run_report_template_lifecycle_demo(workspace: Path) -> None:
    print("\n=== Step 3: Report-template read, verify, instantiate, package ===")

    template_dict = {
        "id": "incident-postmortem",
        "name": "Incident Postmortem Report",
        "version": 1,
        "description": "Standardized postmortem incident report format",
        "parameters": [
            {"name": "incident_id", "type": "string", "required": True},
            {"name": "severity", "type": "string", "required": True},
            {"name": "impact_duration_minutes", "type": "integer", "required": True},
            {"name": "root_cause", "type": "string", "required": True},
            {"name": "resolved", "type": "boolean", "default": True},
        ],
        "sections": [
            {
                "id": "overview",
                "title": "Incident Overview",
                "format": "markdown",
                "content_template": "Incident **{{incident_id}}** (Severity: {{severity}})\nResolved: {{resolved}}",
            },
            {
                "id": "impact",
                "title": "Impact & Root Cause",
                "format": "markdown",
                "content_template": "Total impact duration: {{impact_duration_minutes}} minutes.\n\nRoot Cause Analysis:\n{{root_cause}}",
            },
        ],
    }

    # 1. Read
    tpl = read_report_template(template_dict)
    print(f"Read report template: {tpl['id']} v{tpl['version']}")

    # 2. Verify
    verification = verify_report_template(tpl)
    print(f"Template verification valid={verification['valid']}, vars={verification['referenced_variables']}")
    assert verification["valid"] is True

    # 3. Instantiate
    inputs = {
        "incident_id": "INC-8821",
        "severity": "SEV-2",
        "impact_duration_minutes": 42,
        "root_cause": "Database connection pool exhaustion due to slow replica queries.",
    }
    rendered = instantiate_report_template(tpl, inputs)
    print(f"Instantiated report document:\n{rendered['rendered_document']}")
    assert "Incident **INC-8821** (Severity: SEV-2)" in rendered["rendered_document"]
    assert "Total impact duration: 42 minutes." in rendered["rendered_document"]

    # 4. Package
    pkg_path = workspace / "incident-postmortem.zip"
    pkg = package_report_template(tpl, pkg_path)
    print(f"Packaged template bundle: {pkg.name} ({pkg.stat().st_size} bytes)")
    assert pkg.is_file()

    # 5. Unpack
    unpacked = unpack_report_template(pkg)
    print(f"Unpacked and verified template: {unpacked['id']} v{unpacked['version']}")
    assert unpacked["id"] == "incident-postmortem"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        workspace = Path(td)
        run_tool_sdk_demo(workspace)
        run_toolset_packaging_demo(workspace)
        run_report_template_lifecycle_demo(workspace)
    print("\nTool SDK, Toolset CLI, and Report-Template lifecycle demonstration passed successfully.")


if __name__ == "__main__":
    main()
