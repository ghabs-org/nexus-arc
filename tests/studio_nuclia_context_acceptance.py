"""Actual connector HTTP request with server context; requires local dependency simulation."""

import os
import runpy
from pathlib import Path


def test_server_context_never_accepts_a_tool_argument_override():
    consumer = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_nuclia_context.py")
    )
    assert consumer["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia")
    ) == {
        "server_context_used": True,
        "override_rejected_before_network": True,
        "augmented_citation_pinned": True,
    }
