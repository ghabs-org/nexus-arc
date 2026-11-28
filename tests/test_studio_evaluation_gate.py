"""Offline release gates distrust aggregates and require complete case evidence."""

import copy
import json
import subprocess
import sys

import pytest

from nexus.core.studio_evaluation_gate import check_report, compare_reports, read_report
from tests.test_studio_experiments import experiment, suite
from tests.test_studio_resources import app as app
from tests.test_studio_resources import done


def report():
    return {
        "variants": [{"name": "A"}, {"name": "B"}],
        "runs_per_example": 1,
        "pass_threshold": 0.5,
        "expected_total": 4,
        "complete": True,
        "evaluation_status": "passed",
        "results": [
            {
                "variant_index": variant,
                "case": case,
                "repetition": 1,
                "passed": True,
                "checks": [{"passed": True}],
                "input": "PRIVATE INPUT",
                "output": "PRIVATE OUTPUT",
            }
            for variant in (1, 2)
            for case in (1, 2)
        ],
    }


def test_recomputes_each_variant_without_trusting_aggregates_or_mutating_report():
    source = report()
    source.update(total=999, passed=0, acceptance=False, variant_results=[])
    before = copy.deepcopy(source)
    result = check_report(source)
    assert result["passed"] and result["total"] == 4
    assert [v["passed"] for v in result["variants"]] == [2, 2]
    assert source == before
    assert "PRIVATE" not in json.dumps(result)


def test_weak_variant_cannot_hide_behind_aggregate_or_lower_override():
    source = report()
    source["pass_threshold"] = 0.75
    source["results"][-1].update(passed=False, checks=[{"passed": False}])
    result = check_report(source, threshold=0)
    assert result["status"] == "threshold-not-met" and result["threshold"] == 0.75
    assert [v["accepted"] for v in result["variants"]] == [True, False]
    source["pass_threshold"] = 0.5
    assert check_report(source)["passed"]
    assert not check_report(source, threshold=1)["passed"]


@pytest.mark.parametrize("state", ["running", "waiting", "error", "cancelled", [], None])
def test_nonterminal_or_unknown_state_never_passes_even_at_zero_threshold(state):
    source = report()
    source.update(evaluation_status=state, pass_threshold=0)
    result = check_report(source)
    assert result["status"] == "blocked" and not result["complete"]


def test_missing_or_errored_evidence_never_passes_at_zero_threshold():
    source = report()
    source["pass_threshold"] = 0
    source["results"].pop()
    assert check_report(source)["status"] == "blocked"
    source = report()
    source["pass_threshold"] = 0
    source["results"][0].update(passed=False, checks=[], error="PRIVATE FAILURE")
    result = check_report(source)
    assert result["status"] == "blocked"
    assert "PRIVATE" not in json.dumps(result)
    source = report()
    source["pass_threshold"] = 0
    source["results"][0].update(
        passed=False, checks=[{"passed": False, "error": "PRIVATE JUDGE FAILURE"}]
    )
    assert check_report(source)["status"] == "blocked"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(complete=1),
        lambda d: d.update(expected_total=True),
        lambda d: d.update(expected_total=3),
        lambda d: d.update(runs_per_example=0),
        lambda d: d.update(pass_threshold=float("nan")),
        lambda d: d.update(pass_threshold=True),
        lambda d: d.update(variants=[]),
        lambda d: d["results"][0].update(variant_index=True),
        lambda d: d["results"][0].update(repetition=2),
        lambda d: d["results"][0].update(passed=1),
        lambda d: d["results"][0].update(checks=[]),
        lambda d: d["results"][0].update(checks=[{"passed": False}]),
        lambda d: d["results"].__setitem__(1, copy.deepcopy(d["results"][0])),
    ],
)
def test_malformed_evidence_cannot_be_promoted(change):
    source = report()
    change(source)
    with pytest.raises(ValueError):
        check_report(source)


def test_duplicate_json_fields_and_oversized_reports_reject(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_text('{"complete":true,"complete":false}')
    with pytest.raises(ValueError, match="duplicate"):
        read_report(path)
    monkeypatch.setattr("nexus.core.studio_evaluation_gate.MAX_REPORT_BYTES", 20)
    path.write_text(" " * 21)
    with pytest.raises(ValueError, match="eight MiB"):
        read_report(path)
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        read_report(path)


def test_actual_evaluation_report_cli_and_thin_example(app, tmp_path):
    studio, calls = app
    saved = experiment(studio, suite(studio))
    run = done(studio, studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]}))
    source = studio._read(studio._path("evaluation-reports", run["id"]))
    path = tmp_path / "report.json"
    path.write_text(json.dumps(source))
    before = path.read_bytes()
    for command in (
        [sys.executable, "-m", "nexus.cli", "eval", "check-report"],
        [sys.executable, "examples/nexus-bot/src/studio_evaluation_gate.py"],
    ):
        result = subprocess.run([*command, str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["passed"] is True
    assert path.read_bytes() == before and len(calls) == 3


def test_cli_exit_codes_and_private_errors(tmp_path):
    path = tmp_path / "report.json"
    source = report()
    source.update(complete=False, evaluation_status="waiting")
    path.write_text(json.dumps(source))
    command = [sys.executable, "-m", "nexus.cli", "eval", "check-report", str(path)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1 and json.loads(result.stdout)["status"] == "blocked"
    assert "PRIVATE" not in result.stdout + result.stderr
    path.write_text("PRIVATE INVALID JSON")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2 and json.loads(result.stdout)["status"] == "invalid"
    assert "PRIVATE" not in result.stdout + result.stderr


def test_compare_reports_tracks_improvements_regressions_and_preserves_thresholds():
    baseline = report()
    baseline["results"][1].update(passed=False, checks=[{"passed": False}])
    candidate = report()
    candidate["pass_threshold"] = 0.25
    baseline["pass_threshold"] = 0.75
    diff = compare_reports(baseline, candidate)
    assert diff["passed"] is True
    assert diff["status"] == "passed"
    assert diff["threshold"] == 0.75
    assert diff["improved_slots"] == 1 and diff["regressed_slots"] == 0
    assert diff["variants"][0]["delta_passed"] == 1
    assert "PRIVATE" not in json.dumps(diff)

    regressed = report()
    regressed["results"][0].update(passed=False, checks=[{"passed": False}])
    reg_diff = compare_reports(baseline, regressed, threshold=0.25)
    assert reg_diff["passed"] is False
    assert reg_diff["status"] == "regressed"
    assert reg_diff["regressed_slots"] == 1 and reg_diff["improved_slots"] == 1


def test_compare_reports_blocks_incomplete_or_errored_and_rejects_incompatible_inputs(tmp_path):
    baseline = report()
    incomplete = report()
    incomplete.update(complete=False, evaluation_status="running")
    blocked = compare_reports(baseline, incomplete)
    assert blocked["passed"] is False and blocked["status"] == "blocked"

    mismatch_variants = report()
    mismatch_variants["variants"] = [{"name": "B"}, {"name": "A"}]
    with pytest.raises(ValueError, match="variant names"):
        compare_reports(baseline, mismatch_variants)

    mismatch_inputs = report()
    mismatch_inputs["results"][0]["input"] = "CHANGED PRIVATE INPUT"
    mismatch_inputs["results"][2]["input"] = "CHANGED PRIVATE INPUT"
    with pytest.raises(ValueError, match="case inputs") as exc_info:
        compare_reports(baseline, mismatch_inputs)
    assert "PRIVATE" not in str(exc_info.value)

    base_path = tmp_path / "baseline.json"
    cand_path = tmp_path / "candidate.json"
    base_path.write_text(json.dumps(baseline))
    cand_path.write_text(json.dumps(report()))
    for cmd in (
        [
            sys.executable,
            "-m",
            "nexus.cli",
            "eval",
            "compare-reports",
            str(base_path),
            str(cand_path),
        ],
        [
            sys.executable,
            "examples/nexus-bot/src/studio_evaluation_gate.py",
            str(base_path),
            "--compare",
            str(cand_path),
        ],
    ):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["passed"] is True
