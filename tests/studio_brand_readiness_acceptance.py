"""Explicit public-asset and passive-readiness acceptance against actual Studio HTTP."""

import runpy
from pathlib import Path


def test_favicon_and_passive_readiness_through_real_server():
    example = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_brand_readiness.py"
        )
    )
    result = example["exercise"]()
    assert result["favicon_served"] and result["existing_icon_preserved"]
    assert result["runs_started"] == 0 and not result["live_voice_verified"]
    assert any(p["local"] and not p["model_ready"] for p in result["profiles"])
