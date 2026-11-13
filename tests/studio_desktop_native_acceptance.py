"""Explicit native handshake acceptance. Requires the locally built probe; no skips."""

import json
import os
import re
import runpy
import subprocess
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "apps/studio-desktop/src-tauri/target/debug/examples/backend_probe"


def test_launched_sidecar_proves_its_credential_and_gates_api():
    example = runpy.run_path(str(ROOT / "examples/nexus-bot/src/studio_desktop_readiness.py"))
    assert example["exercise"](PROBE) == {
        "launched_sidecar_verified": True,
        "wrong_workspace_rejected": True,
        "other_launch_credential_rejected": True,
        "unauthenticated_studio_rejected": True,
        "anonymous_api_denied": True,
        "session_api_allowed": True,
        "second_launch_rejected": True,
        "stop_closes_and_releases_workspace": True,
        "native_webview_tested": False,
        "standalone_distribution_ready": False,
    }


@pytest.mark.parametrize("mode", ["redirect", "html", "oversized", "forged", "bootstrap"])
def test_native_readiness_rejects_impostors_without_sending_secrets(mode):
    port = int(os.environ["NEXUS_NATIVE_SIMULATION_PORT"])
    with requests.Session() as client:
        client.trust_env = False
        base = f"http://127.0.0.1:{port}/api"
        response = client.post(base + "/fixture/mode", json={"mode": mode}, timeout=5)
        response.raise_for_status()
        result = subprocess.run(
            [str(PROBE), str(port), "/expected"],
            input=os.urandom(32).hex() + "\n",
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 1 and json.loads(result.stdout) == {"ready": False}
        calls = client.get(base + "/fixture/calls", timeout=5).json()["calls"]
        assert calls and {call["path"] for call in calls} == {"/desktop/handshake"}
        challenges = [call["challenge"] for call in calls]
        assert all(re.fullmatch("[0-9a-f]{64}", c) for c in challenges)
        assert len(set(challenges)) == len(challenges)  # fresh per attempt, never replayed
        assert not any(call["cookie"] or call["authorization"] for call in calls)
