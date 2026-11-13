"""Offline release decisions from existing Studio evaluation reports.

This validates recorded results, not report authenticity or model quality. It
never launches a task, contacts Studio, or changes a release.
"""

import json
import math

from nexus.core.studio_experiments import MAX_EXECUTIONS, summarize

MAX_REPORT_BYTES = 8 * 1024 * 1024


def _threshold(value):
    if type(value) not in {int, float} or not 0 <= value <= 1 or not math.isfinite(value):
        raise ValueError("Pass thresholds must be finite numbers from zero to one")
    return value


def _validate_report(report, *, threshold=None):
    """Validate report structure and return summary plus per-slot outcomes and inputs."""
    if not isinstance(report, dict):
        raise ValueError("Choose a Studio evaluation report object")
    from nexus.core.studio_experiment_judges import check_evidence, report_manifest

    manifest = report_manifest(report)
    variants = report.get("variants")
    if (
        not isinstance(variants, list)
        or not 1 <= len(variants) <= 4
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not 1 <= len(item["name"].strip()) <= 100
            for item in variants
        )
    ):
        raise ValueError("Report requires one to four named variants")
    repeats, expected = report.get("runs_per_example"), report.get("expected_total")
    if (
        type(repeats) is not int
        or not 1 <= repeats <= 20
        or type(expected) is not int
        or not 1 <= expected <= MAX_EXECUTIONS
        or expected % (len(variants) * repeats)
    ):
        raise ValueError("Report has invalid expected case or repetition counts")
    cases = expected // (len(variants) * repeats)
    if cases > 100:
        raise ValueError("Report exceeds one hundred cases per variant")
    effective = _threshold(report.get("pass_threshold"))
    if threshold is not None:
        effective = max(effective, _threshold(threshold))
    if type(report.get("complete")) is not bool:
        raise ValueError("Report must declare whether execution is complete")
    results = report.get("results")
    if not isinstance(results, list) or len(results) > expected:
        raise ValueError("Report has invalid result counts")
    seen, normalized, slot_outcomes, case_inputs, errors = {}, [], {}, {}, False
    for entry in results:
        if not isinstance(entry, dict):
            raise ValueError("Each result must be an object")
        slot = tuple(entry.get(key) for key in ("variant_index", "case", "repetition"))
        if (
            any(type(value) is not int for value in slot)
            or not 1 <= slot[0] <= len(variants)
            or not 1 <= slot[1] <= cases
            or not 1 <= slot[2] <= repeats
            or slot in seen
            or type(entry.get("passed")) is not bool
        ):
            raise ValueError("Results need unique, in-range variant/case/repetition slots")
        seen[slot] = True
        if "input" in entry:
            if not isinstance(entry["input"], str):
                raise ValueError("Result input must be a string when present")
            if slot[1] in case_inputs and case_inputs[slot[1]] != entry["input"]:
                raise ValueError("Slots for the same case must share the same input")
            case_inputs[slot[1]] = entry["input"]
        checks = entry.get("checks")
        if not isinstance(checks, list) or any(
            not isinstance(check, dict) or type(check.get("passed")) is not bool for check in checks
        ):
            raise ValueError("Result checks must have boolean outcomes")
        slot_errored = any(bool(check.get("error")) for check in checks)
        slot_errored = check_evidence(manifest, entry) or slot_errored
        if entry.get("error"):
            slot_errored = True
            if entry["passed"]:
                raise ValueError("An errored result cannot pass")
        elif not checks or entry["passed"] != all(check["passed"] for check in checks):
            raise ValueError("Result outcome must agree with its nonempty checks")
        errors = errors or slot_errored
        slot_outcomes[slot] = entry["passed"] and not slot_errored
        normalized.append({"variant_index": slot[0], "passed": entry["passed"]})
    complete = (
        report["complete"]
        and isinstance(report.get("evaluation_status"), str)
        and report["evaluation_status"] in {"passed", "failed"}
        and len(results) == expected
        and not errors
    )
    summary = {
        "variants": [{"name": item["name"]} for item in variants],
        "results": normalized,
        "expected_total": expected,
        "pass_threshold": effective,
    }
    summarize(summary, complete=complete)
    passed = summary["acceptance"] is True
    gate = {
        "format": "nexus-studio-evaluation-gate/v1",
        "passed": passed,
        "status": "passed" if passed else "threshold-not-met" if complete else "blocked",
        "reason": (
            "Every variant meets the effective threshold"
            if passed
            else "At least one variant falls below the effective threshold"
            if complete
            else "Report is incomplete, nonterminal or contains execution errors"
        ),
        "threshold": effective,
        "complete": summary["complete"],
        "total": len(results),
        "expected_total": expected,
        "variants": summary["variant_results"],
    }
    return (
        gate,
        slot_outcomes,
        case_inputs,
        cases,
        repeats,
        [item["name"].strip() for item in variants],
    )


def check_report(report, *, threshold=None):
    """Recompute every variant; incomplete or errored evidence never passes."""
    gate, *_ = _validate_report(report, threshold=threshold)
    return gate


def compare_reports(baseline, candidate, *, threshold=None):
    """Compare baseline and candidate reports offline without lowering thresholds."""
    base_gate, base_slots, base_inputs, base_cases, base_repeats, base_names = _validate_report(
        baseline, threshold=threshold
    )
    cand_gate, cand_slots, cand_inputs, cand_cases, cand_repeats, cand_names = _validate_report(
        candidate, threshold=threshold
    )
    if base_names != cand_names:
        raise ValueError("Reports must have matching variant names in the same order")
    if base_cases != cand_cases or base_repeats != cand_repeats:
        raise ValueError("Reports must have matching case and repetition counts")
    for case_idx in set(base_inputs).intersection(cand_inputs):
        if base_inputs[case_idx] != cand_inputs[case_idx]:
            raise ValueError("Reports must have matching case inputs for each case")
    effective = max(base_gate["threshold"], cand_gate["threshold"])
    if cand_gate["threshold"] != effective:
        cand_gate, cand_slots, *_ = _validate_report(candidate, threshold=effective)
    both_complete = bool(base_gate["complete"] and cand_gate["complete"])
    variant_comparisons = []
    for idx, (base_v, cand_v) in enumerate(
        zip(base_gate["variants"], cand_gate["variants"], strict=True), 1
    ):
        expected_slots = [
            (idx, case_idx, rep_idx)
            for case_idx in range(1, base_cases + 1)
            for rep_idx in range(1, base_repeats + 1)
        ]
        regressed = sum(
            1
            for slot in expected_slots
            if base_slots.get(slot) is True and cand_slots.get(slot) is False
        )
        improved = sum(
            1
            for slot in expected_slots
            if base_slots.get(slot) is False and cand_slots.get(slot) is True
        )
        missing = sum(
            1 for slot in expected_slots if slot not in base_slots or slot not in cand_slots
        )
        accepted = (
            bool(
                cand_v["accepted"] is True
                and regressed == 0
                and cand_v["passed"] >= base_v["passed"]
            )
            if both_complete
            else None
        )
        variant_comparisons.append(
            {
                "name": cand_v["name"],
                "index": idx,
                "baseline_passed": base_v["passed"],
                "candidate_passed": cand_v["passed"],
                "delta_passed": cand_v["passed"] - base_v["passed"],
                "expected_total": cand_v["expected_total"],
                "baseline_pass_rate": base_v["pass_rate"],
                "candidate_pass_rate": cand_v["pass_rate"],
                "regressed_slots": regressed,
                "improved_slots": improved,
                "missing_slots": missing,
                "accepted": accepted,
            }
        )
    total_regressed = sum(item["regressed_slots"] for item in variant_comparisons)
    total_improved = sum(item["improved_slots"] for item in variant_comparisons)
    total_missing = sum(item["missing_slots"] for item in variant_comparisons)
    all_threshold_met = both_complete and all(
        item["candidate_pass_rate"] >= effective for item in variant_comparisons
    )
    passed = bool(both_complete and all_threshold_met and total_regressed == 0)
    status = (
        "blocked"
        if not both_complete
        else "regressed"
        if total_regressed > 0
        else "threshold-not-met"
        if not all_threshold_met
        else "passed"
    )
    reason = (
        "Every variant meets the effective threshold without slot regressions"
        if passed
        else "Baseline or candidate report is incomplete, nonterminal or contains execution errors"
        if not both_complete
        else "Candidate regressed on one or more previously passing slots"
        if total_regressed > 0
        else "At least one candidate variant falls below the effective threshold"
    )
    return {
        "format": "nexus-studio-evaluation-comparison/v1",
        "passed": passed,
        "status": status,
        "reason": reason,
        "threshold": effective,
        "complete": both_complete,
        "baseline_complete": base_gate["complete"],
        "candidate_complete": cand_gate["complete"],
        "expected_total": cand_gate["expected_total"],
        "regressed_slots": total_regressed,
        "improved_slots": total_improved,
        "missing_slots": total_missing,
        "variants": variant_comparisons,
    }


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Report JSON must not contain duplicate fields")
        result[key] = value
    return result


def read_report(path):
    """Read bounded JSON without emitting source content in errors."""
    try:
        with open(path, "rb") as stream:
            content = stream.read(MAX_REPORT_BYTES + 1)
        if len(content) > MAX_REPORT_BYTES:
            raise ValueError("Report exceeds eight MiB")
        return json.loads(content.decode("utf-8-sig"), object_pairs_hook=_unique_fields)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Cannot read a valid UTF-8 JSON evaluation report") from None


def add_parser(subparsers):
    parser = subparsers.add_parser("eval", help="Check exported evaluation evidence offline")
    commands = parser.add_subparsers(dest="eval_operation", required=True)
    check = commands.add_parser("check-report", help="Gate each variant without running tasks")
    check.add_argument("report", help="Exported Studio evaluation report JSON")
    check.add_argument(
        "--threshold", type=float, help="Minimum pass rate; cannot lower the saved threshold"
    )
    compare = commands.add_parser(
        "compare-reports",
        help="Compare baseline and candidate reports without running tasks",
    )
    compare.add_argument("baseline", help="Exported baseline Studio evaluation report JSON")
    compare.add_argument("candidate", help="Exported candidate Studio evaluation report JSON")
    compare.add_argument(
        "--threshold",
        type=float,
        help="Minimum pass rate; cannot lower either saved threshold",
    )


def evaluation_command(args):
    try:
        if args.eval_operation == "compare-reports":
            result = compare_reports(
                read_report(args.baseline), read_report(args.candidate), threshold=args.threshold
            )
        else:
            result = check_report(read_report(args.report), threshold=args.threshold)
    except ValueError as exc:
        print(json.dumps({"passed": False, "status": "invalid", "reason": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1
