"""Opt-in real native client against the local Postman contract simulation."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "apps/studio-desktop/src-tauri/target/debug/examples/accounts_probe"


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "wrong-state-first",
        "deny",
        "redirect",
        "html",
        "oversized",
        "foreign-destination",
        "exchange-error",
    ],
)
def test_native_account_http_contract(mode):
    port = int(os.environ["NEXUS_ACCOUNT_SIMULATION_PORT"])
    with requests.Session() as http:
        http.trust_env = False
        base = f"http://127.0.0.1:{port}/auth"
        http.post(base + "/fixture/mode", json={"mode": mode}, timeout=5).raise_for_status()
        try:
            result = subprocess.run([str(PROBE), str(port)], capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            trace = http.get(base + "/fixture/calls", timeout=5).json()["calls"]
            pytest.fail(f"Native fixture stalled: {trace}")
        success = mode in {"success", "wrong-state-first"}
        assert result.returncode == (0 if success else 1)
        assert json.loads(result.stdout) == {"connected_and_revoked": success}
        calls = http.get(base + "/fixture/calls", timeout=5).json()["calls"]
        exchanges = [c for c in calls if c["path"] == "/desktop/exchange"]
        assert len(exchanges) == (1 if success or mode == "exchange-error" else 0)
        assert not any(c["path"] == "/must-not-follow" for c in calls)
        assert all(not c.get("bearer", False) for c in calls if c["path"] != "/desktop/session")
        if success:
            assert any(
                c.get("method") == "DELETE" and c["path"] == "/desktop/session" and c["bearer"]
                for c in calls
            )
