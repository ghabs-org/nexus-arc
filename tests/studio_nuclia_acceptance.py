"""Explicit local-HTTP Nuclia generation and private citation acceptance."""

import os
import runpy
from pathlib import Path


def test_nuclia_opt_in_generation_and_private_revision_citations():
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_nuclia.py")
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_NUCLIA_URL", "http://127.0.0.1:49873/nuclia")
    )
    assert result == {
        "requests": 9,
        "failure_cases": 6,
        "private_revision_citations": True,
        "explicit_generation": True,
        "live_verified": False,
    }
