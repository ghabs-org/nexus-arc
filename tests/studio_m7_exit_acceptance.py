"""Milestone 7 configuration, Tool SDK and packaging helper checks.

Run from repository root:
PYTHONPATH=. .venv/bin/python3 tests/studio_m7_exit_acceptance.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile

from nexus.core.studio import StudioService
from nexus.core.studio_connections import validate_connection
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


def run_catalog_exit_scenario(studio: StudioService) -> None:
    print("[1/5] Verifying all 31 catalog entries (4 excluded, 27 supported)...")
    catalog = studio.recipes.list()
    items = catalog["items"]
    official = catalog["official_items"]
    excluded = catalog["excluded"]

    assert len(items) == 12, f"Expected 12 community items, got {len(items)}"
    assert len(official) == 15, f"Expected 15 official items, got {len(official)}"
    assert len(excluded) == 4, f"Expected 4 excluded items, got {len(excluded)}"
    assert len(items) + len(official) + len(excluded) == 31

    for row in items + official:
        meta = row.get("setup_metadata")
        assert meta is not None, f"Missing setup_metadata for {row['id']}"
        assert meta["billing"], f"Missing billing for {row['id']}"
        assert row.get("readiness") is not None, f"Missing readiness for {row['id']}"
        assert row["readiness"]["live_verified"] is False


def run_direct_configuration_scenario(studio: StudioService) -> None:
    print("[2/5] Testing direct configuration paths for community and official tools...")
    # Community tools
    fin = studio.recipes.configure("finance", {}, None)
    assert fin["tool_id"] and "setup" in fin

    web = studio.recipes.configure("web-agent", {}, None)
    assert web["tool_id"] and "setup" in web

    ip_tool = studio.recipes.configure("find-my-ip", {}, None)
    assert ip_tool["tool_id"] and "setup" in ip_tool

    im = studio.recipes.configure("imagemagick", {}, None)
    assert im["tool_id"] and "setup" in im

    od = studio.recipes.configure("object-detection", {}, None)
    assert od["tool_id"] and "setup" in od

    cli_ep = studio.recipes.configure("cli-entrypoint", {}, None)
    assert "nexus chat" in cli_ep["command"]

    # Official connectors validation
    sql_val = validate_connection({
        "type": "sql",
        "dsn_env": "DB_URL",
        "dialect": "postgresql",
        "actions": ["query"],
        "enabled": False,
    })
    assert sql_val["type"] == "sql"

    mongo_val = validate_connection({
        "type": "mongodb",
        "dsn_env": "MONGO_URL",
        "database": "test",
        "collections": ["logs"],
        "actions": ["query"],
        "allow_writes": False,
        "enabled": False,
    })
    assert mongo_val["type"] == "mongodb"


def run_tool_sdk_scenario(workspace: Path) -> None:
    print("[3/5] Verifying Tool SDK @with_config_schema and tool_cli driver...")
    schema = [
        ConfigSchemaField(key="token", required=True),
        ConfigSchemaField(key="threshold", default=10, type="integer"),
    ]

    @with_config_schema(schema)
    def sample_tool(input_data, context):
        thresh = context.get_config("threshold", 10)
        return ToolResult(output={"accepted": input_data.get("val", 0) >= thresh})

    fields = getattr(sample_tool, "__config_schema__")
    assert len(fields) == 2
    assert fields[0].key == "token"

    ctx = SandboxToolContextFacade(workspace_root=workspace, config={"token": "t-1", "threshold": 5})
    res = sample_tool({"val": 8}, ctx)
    assert res.success is True
    assert res.output["accepted"] is True


def run_toolset_packaging_scenario(workspace: Path) -> None:
    print("[4/5] Verifying Toolset CLI validation and PEP 427 wheel building...")
    pkg = workspace / "test_toolset"
    pkg.mkdir(parents=True, exist_ok=True)

    (pkg / "calc.py").write_text(
        """from nexus.tools.sdk import with_config_schema, ConfigSchemaField, ToolResult

@with_config_schema([ConfigSchemaField(key="factor", default=2, type="integer")])
def scale(input_data, context):
    factor = context.get_config("factor", 2)
    return ToolResult(output={"scaled": input_data.get("val", 0) * factor})
""",
        encoding="utf-8",
    )

    (pkg / "manifest.yaml").write_text(
        """id: test-scale-toolset
name: Test Scale Toolset
version: 0.2.0
tools:
  - id: scale
    entrypoint: calc:scale
""",
        encoding="utf-8",
    )

    summary = validate_toolset(pkg)
    assert summary["valid"] is True
    assert summary["toolset_id"] == "test-scale-toolset"

    wheel = build_toolset(pkg, output_dir=workspace / "dist")
    assert wheel.is_file()
    assert wheel.name == "test_scale_toolset-0.2.0-py3-none-any.whl"


def run_report_template_scenario(workspace: Path) -> None:
    print("[5/5] Verifying Report Template read, verify, instantiate, package, and unpack...")
    template = {
        "id": "compliance-audit",
        "name": "Compliance Audit Report",
        "version": 1,
        "parameters": [
            {"name": "auditor", "type": "string", "required": True},
            {"name": "score", "type": "number", "required": True},
            {"name": "passed", "type": "boolean", "default": True},
        ],
        "sections": [
            {
                "id": "summary",
                "title": "Audit Summary",
                "format": "markdown",
                "content_template": "Auditor: {{auditor}}\nScore: {{score}}%\nPassed: {{passed}}",
            }
        ],
    }

    tpl = read_report_template(template)
    v = verify_report_template(tpl)
    assert v["valid"] is True

    doc = instantiate_report_template(tpl, {"auditor": "Alice", "score": 98.5})
    assert "Auditor: Alice" in doc["rendered_document"]
    assert "Score: 98.5%" in doc["rendered_document"]

    pkg_zip = package_report_template(tpl, workspace / "compliance.zip")
    assert pkg_zip.is_file()

    unpacked = unpack_report_template(pkg_zip)
    assert unpacked["id"] == "compliance-audit"
    assert unpacked["version"] == 1


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        workspace = Path(td)
        studio = StudioService(workspace / "studio")
        try:
            run_catalog_exit_scenario(studio)
            run_direct_configuration_scenario(studio)
            run_tool_sdk_scenario(workspace)
            run_toolset_packaging_scenario(workspace)
            run_report_template_scenario(workspace)
        finally:
            studio.close()

    print("Catalog configuration and packaging checks passed; per-entry invocation acceptance remains separate.")


if __name__ == "__main__":
    main()
