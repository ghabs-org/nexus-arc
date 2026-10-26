"""Opt-in real frozen backend/MCP cleanup; requires an explicitly built sidecar."""

import os
import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("shutdown", ["stop", "kill"])
def test_frozen_backend_releases_mcp_groups_on_parent_shutdown(persistent, shutdown):
    root = Path(__file__).resolve().parents[1]
    example = runpy.run_path(str(root / "examples/nexus-bot/src/studio_mcp_desktop_cleanup.py"))
    result = example["exercise"](os.environ["NEXUS_ACCEPTANCE_SIDECAR"], persistent, shutdown)
    assert result["server_stopped"] and result["child_stopped"] and result["backend_stopped"]
    assert result["workspace_released"] and result["calls"] == 1 and result["close_seconds"] < 10
    assert not result["live_accounts_used"]


@pytest.mark.parametrize("persistent", [False, True])
def test_source_shutdown_stops_active_stdio_server_and_its_resistant_child(persistent):
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_mcp_lifecycle.py")
    )
    result = example["exercise"](persistent)
    assert result["server_stopped"] and result["child_stopped"] and result["caller_settled"]
    assert result["invocations"] == 1 and result["close_seconds"] < 8
    assert not result["external_accounts_used"]
