"""Explicit real HTTP attachment acceptance; missing prerequisites fail, never skip."""

import os
import runpy
from pathlib import Path


def test_signed_slack_files_use_real_bounded_http_and_private_pinned_artifacts():
    consumer = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_slack_files.py")
    )
    assert consumer["exercise"](os.environ["NEXUS_ACCEPTANCE_SLACK_URL"]) == {
        "http_requests": 11,
        "private_artifacts": 2,
        "pinned_runs": 1,
        "duplicates_suppressed": True,
        "redirects_blocked": True,
        "provider_errors_sanitized": True,
        "live_verified": False,
    }
