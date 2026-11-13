"""Run explicitly with the local repository Power BI simulation available."""

import os
import runpy
from pathlib import Path


def test_powerbi_http_semantics_and_natural_language_agent():
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_powerbi.py")
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_POWERBI_URL", "http://127.0.0.1:49872/powerbi")
    )
    assert result == {
        "dataset_scoped": True,
        "partial_results_rejected": 9,
        "agent_synthesis": True,
        "requests": 16,
        "live_verified": False,
    }
