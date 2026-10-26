"""Explicit actual Docker/Unix-proxy acceptance; no implicit pulls or skips."""

import os
import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["recovery", "failed-remove", "alias", "restart"])
def test_real_filtered_proxy_recovery_and_ownership(mode):
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_proxy_recovery.py")
    )
    assert example["exercise"](
        os.environ["NEXUS_ACCEPTANCE_WORKER_IMAGE"],
        os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"],
        mode,
    ) == {
        "mode": mode,
        "normal_execution": True,
        "ownership_preserved": True,
        "restart_requires_review": mode == "restart",
        "replayed_work": False,
        "images_pulled": False,
        "external_accounts_used": False,
    }
