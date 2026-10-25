"""Real pinned filesystem server acceptance; prerequisites fail explicitly.

Set NEXUS_FILESYSTEM_MCP_PACKAGE to the installed server-filesystem package folder
and NEXUS_FILESYSTEM_NODE to an absolute Node 24+ executable. Install separately;
this suite never downloads a package, runs an installer or uses live accounts.
"""

import asyncio
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("persistent", [False, True])
def test_pinned_filesystem_server_invocation_and_folder_boundaries(persistent):
    package_path = os.environ.get("NEXUS_FILESYSTEM_MCP_PACKAGE")
    runtime = os.environ.get("NEXUS_FILESYSTEM_NODE")
    assert package_path and runtime, "Select the installed package and Node runtime; no skips"
    package = Path(package_path)
    metadata = json.loads((package / "package.json").read_text())
    assert metadata["name"] == "@modelcontextprotocol/server-filesystem"
    assert metadata["version"] == "2.0.0", "Use the frozen installation selection"
    assert Path(runtime).is_absolute() and Path(runtime).is_file()
    version = subprocess.run(
        [runtime, "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()
    assert int(version.removeprefix("v").split(".")[0]) >= 24
    path = Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_filesystem.py"
    spec = importlib.util.spec_from_file_location("studio_filesystem_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = asyncio.run(module.exercise(runtime, package / "dist/index.js", persistent=persistent))
    assert result["persistent"] is persistent
    assert result["discovered_tools"] >= len(result["selected_tools"])
    assert result["reviewed_write"] and result["post_failure_read"]
