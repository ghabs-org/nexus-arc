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


def check_report(report, *, threshold=None):
    """Recompute every variant; incomplete or errored evidence never passes."""
    if not isinstance(report, dict):
        raise ValueError("Choose a Studio evaluation report object")
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
    seen, normalized, errors = set(), [], False
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
        seen.add(slot)
        checks = entry.get("checks")
        if not isinstance(checks, list) or any(
            not isinstance(check, dict) or type(check.get("passed")) is not bool for check in checks
        ):
            raise ValueError("Result checks must have boolean outcomes")
        if any(check.get("error") for check in checks):
            errors = True
        if entry.get("error"):
            errors = True
            if entry["passed"]:
                raise ValueError("An errored result cannot pass")
        elif not checks or entry["passed"] != all(check["passed"] for check in checks):
            raise ValueError("Result outcome must agree with its nonempty checks")
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
    return {
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


def evaluation_command(args):
    try:
        result = check_report(read_report(args.report), threshold=args.threshold)
    except ValueError as exc:
        print(json.dumps({"passed": False, "status": "invalid", "reason": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1
