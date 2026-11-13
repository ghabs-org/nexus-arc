"""Acceptance records must distinguish native proof from portable simulations."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests import studio_windows_speech_acceptance as acceptance


def test_wrong_host_is_unavailable_without_starting_consumer(monkeypatch):
    monkeypatch.setattr(acceptance.sys, "platform", "darwin")
    launch = Mock(side_effect=AssertionError("Wrong OS must never launch the consumer"))
    monkeypatch.setattr(acceptance.subprocess, "run", launch)
    report, code = acceptance.run_acceptance()
    assert code == 2 and report["status"] == "unavailable"
    assert report["native_verified"] is False
    assert all(len(value) == 64 for value in report["source_sha256"].values())
    launch.assert_not_called()


@pytest.mark.parametrize(
    "outcome",
    [
        "pass",
        "missing_case",
        "subprocess_only",
        "service_failure",
        "nonzero",
        "invalid_json",
        "timeout",
    ],
)
def test_only_complete_success_promotes_native_acceptance(monkeypatch, outcome):
    monkeypatch.setattr(acceptance.sys, "platform", "win32")
    monkeypatch.setattr(acceptance, "native_abi", lambda: {"fixture_only": 1})
    result = SimpleNamespace(returncode=0, stdout=json.dumps(acceptance.EXPECTED), stderr="")
    if outcome == "missing_case":
        result.stdout = json.dumps({"cancel": acceptance.EXPECTED["cancel"]})
    elif outcome == "subprocess_only":
        result.stdout = json.dumps(
            {
                key: value
                for key, value in acceptance.EXPECTED.items()
                if key in {"cancel", "timeout", "owner_death", "finite_input"}
            }
        )
    elif outcome == "service_failure":
        result.stdout = json.dumps({**acceptance.EXPECTED, "service_cancel": "recording remained"})
    elif outcome == "nonzero":
        result.returncode, result.stderr = 125, "controlled failure"
    elif outcome == "invalid_json":
        result.stdout = "not JSON"
    launch = Mock(return_value=result)
    if outcome == "timeout":
        launch.side_effect = subprocess.TimeoutExpired("controlled consumer", 90)
    monkeypatch.setattr(acceptance.subprocess, "run", launch)
    report, code = acceptance.run_acceptance()
    assert code == (0 if outcome == "pass" else 1)
    assert report["native_verified"] is (outcome == "pass")
    assert report["status"] == ("passed" if outcome == "pass" else "failed")
    assert launch.call_args.kwargs["timeout"] == 90
    assert launch.call_args.kwargs["check"] is False


def test_bad_abi_prevents_native_launch(monkeypatch):
    monkeypatch.setattr(acceptance.sys, "platform", "win32")
    monkeypatch.setattr(acceptance, "native_abi", Mock(side_effect=ValueError("bad ABI")))
    launch = Mock(side_effect=AssertionError("No process on bad ABI"))
    monkeypatch.setattr(acceptance.subprocess, "run", launch)
    report, code = acceptance.run_acceptance()
    assert code == 1 and report["native_verified"] is False
    assert report["error_type"] == "ValueError"
    launch.assert_not_called()


def test_cli_writes_unavailable_report_without_claiming_acceptance(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(acceptance.sys, "platform", "darwin")
    path = tmp_path / "evidence" / "native.json"
    assert acceptance.main(["--report", str(path)]) == 2
    report = json.loads(path.read_text())
    assert report["status"] == "unavailable" and report["native_verified"] is False
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"
