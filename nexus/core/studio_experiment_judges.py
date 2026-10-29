"""Copied experiment judges and complete, independently inspectable grading evidence."""

import copy
import math

from nexus.core.studio_catalog import _text
from nexus.core.studio_evaluations import additional_checks, validate_judge


def validate_selection(data):
    fields = {"evaluators", "primaryEvaluatorId", "evaluatorPolicy"}
    if not fields.intersection(data):
        return {}
    if not fields.issubset(data) or data["evaluatorPolicy"] != "all":
        raise ValueError(
            "Select copied evaluators, a primary evaluator and explicit all-evaluator policy"
        )
    items = data["evaluators"]
    if not isinstance(items, list) or not 1 <= len(items) <= 4:
        raise ValueError("Choose one to four distinct experiment evaluators")
    seen, normalized = set(), []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "revision", "name", "judge"}:
            raise ValueError("Evaluator copies require ID, revision, name and judge settings")
        identity = _text(item["id"], "Evaluator ID", 96)
        if (
            identity != item["id"]
            or identity in seen
            or type(item["revision"]) is not int
            or item["revision"] < 1
        ):
            raise ValueError("Evaluator copies require unique IDs and positive integer revisions")
        seen.add(identity)
        normalized.append(
            {
                "id": identity,
                "revision": item["revision"],
                "name": _text(item["name"], "Evaluator name", 200),
                "judge": validate_judge(item["judge"]),
            }
        )
    if not isinstance(data["primaryEvaluatorId"], str) or data["primaryEvaluatorId"] not in seen:
        raise ValueError("The primary evaluator must be one of the selected evaluators")
    return {
        "evaluators": normalized,
        "primaryEvaluatorId": data["primaryEvaluatorId"],
        "evaluatorPolicy": "all",
    }


def copy_evaluator(record):
    return {
        "id": record["id"],
        "revision": record["revision"],
        "name": record["name"],
        "judge": {
            key: copy.deepcopy(value)
            for key, value in record["data"].items()
            if key not in {"kind", "version", "type"}
        },
    }


async def grade(studio, case, output, run_id, requester, evaluators, on_progress=None):
    """Run each judge on one answer; retain completed evidence if a later judge fails."""
    base = {key: value for key, value in case.items() if key != "judge"}
    checks = await additional_checks(studio, base, output, run_id, requester)
    results = []
    if on_progress:
        on_progress(checks, results)
    for evaluator in evaluators:
        result = {key: evaluator[key] for key in ("id", "revision", "name")}
        try:
            graded = await additional_checks(
                studio,
                {**base, "checks": [], "judge": evaluator["judge"]},
                output,
                run_id,
                requester,
            )
            graded = [{**check, "evaluator_id": evaluator["id"]} for check in graded]
            result.update(
                checks=graded, passed=bool(graded) and all(check["passed"] for check in graded)
            )
            if any(check.get("error") for check in graded):
                result["error"] = "Evaluator returned invalid grading evidence"
        except Exception as exc:
            result.update(checks=[], passed=False, error=str(exc))
        checks.extend(result["checks"])
        results.append(result)
        if on_progress:
            on_progress(checks, results)
        if result.get("error"):
            break
    return checks, results


def summarize_evaluators(report):
    if not report.get("evaluators"):
        return
    summaries = []
    for evaluator in report["evaluators"]:
        entries = [*report["results"], *report.get("pending_grading", {}).values()]
        items = [
            item
            for entry in entries
            for item in entry.get("evaluators", [])
            if item["id"] == evaluator["id"]
        ]
        scores = [check["score"] for item in items for check in item["checks"] if "score" in check]
        summaries.append(
            {
                **evaluator,
                "primary": evaluator["id"] == report["primaryEvaluatorId"],
                "total": len(items),
                "expected_total": report["expected_total"],
                "passed": sum(item["passed"] for item in items),
                "mean_score": sum(scores) / len(scores) if scores else None,
            }
        )
    report["evaluator_summaries"] = summaries


def report_manifest(report):
    fields = {"evaluators", "primaryEvaluatorId", "evaluatorPolicy"}
    if not fields.intersection(report):
        return []
    if not fields.issubset(report) or report["evaluatorPolicy"] != "all":
        raise ValueError("Report requires its complete all-evaluator manifest")
    items = report["evaluators"]
    if not isinstance(items, list) or not 1 <= len(items) <= 4:
        raise ValueError("Report requires one to four evaluators")
    seen = set()
    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "revision", "name"}
            or not isinstance(item["id"], str)
            or not 1 <= len(item["id"]) <= 96
            or item["id"] in seen
            or type(item["revision"]) is not int
            or item["revision"] < 1
            or not isinstance(item["name"], str)
            or not 1 <= len(item["name"]) <= 200
        ):
            raise ValueError("Report has an invalid or duplicate evaluator identity")
        seen.add(item["id"])
    if (
        not isinstance(report["primaryEvaluatorId"], str)
        or report["primaryEvaluatorId"] not in seen
    ):
        raise ValueError("Report primary evaluator is missing")
    return items


def check_evidence(manifest, entry):
    """Validate nested grading evidence against the exact flat checks used for acceptance."""
    flat = [check for check in entry.get("checks", []) if "evaluator_id" in check]
    if not manifest:
        if "evaluators" in entry or flat:
            raise ValueError("Evaluator evidence requires its report manifest")
        return False
    items = entry.get("evaluators", [] if entry.get("error") else None)
    if (
        not isinstance(items, list)
        or len(items) > len(manifest)
        or (not entry.get("error") and len(items) != len(manifest))
    ):
        raise ValueError("Case is missing complete evaluator evidence")
    collected, errors = [], False
    for expected, item in zip(manifest, items, strict=False):
        if (
            not isinstance(item, dict)
            or any(item.get(key) != expected[key] for key in ("id", "revision", "name"))
            or type(item.get("revision")) is not int
            or type(item.get("passed")) is not bool
            or not isinstance(item.get("checks"), list)
        ):
            raise ValueError("Case evaluator identities or outcomes disagree with the manifest")
        checks = item["checks"]
        if len(checks) > 1 or (not checks and not item.get("error")):
            raise ValueError("Each evaluator requires one grading check or an explicit error")
        for check in checks:
            if (
                not isinstance(check, dict)
                or check.get("evaluator_id") != item["id"]
                or type(check.get("passed")) is not bool
                or check.get("type") not in {"choice_judge", "rubric_judge"}
            ):
                raise ValueError("Invalid evaluator grading check")
            if check.get("error"):
                errors = True
                if check["passed"]:
                    raise ValueError("Errored evaluator checks cannot pass")
            else:
                score, threshold = check.get("score"), check.get("threshold")
                if any(
                    type(value) not in {int, float}
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                    for value in (score, threshold)
                ) or check["passed"] != (score >= threshold):
                    raise ValueError("Evaluator scores and outcomes disagree")
        if item.get("error"):
            errors = True
            if item["passed"]:
                raise ValueError("Errored evaluator outcomes cannot pass")
        elif item["passed"] != all(check["passed"] for check in checks):
            raise ValueError("Evaluator outcome disagrees with its checks")
        collected.extend(checks)
    if flat != collected:
        raise ValueError("Nested evaluator evidence disagrees with flat case checks")
    return errors
