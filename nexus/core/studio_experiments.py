"""Saved regression plans compiled into the existing evaluation job payload."""

import copy
import math

from nexus.core.studio_catalog import _text

MAX_EXECUTIONS = 400


def validate_variants(variants, *, unique=False):
    from nexus.adapters.ai.base import EFFORT_LEVELS
    from nexus.core.studio import _PROVIDERS

    if not isinstance(variants, list) or not 1 <= len(variants) <= 4:
        raise ValueError("An experiment supports 1 to 4 variants")
    names = set()
    for variant in variants:
        if not isinstance(variant, dict) or set(variant) - {"name", "provider", "model", "effort"}:
            raise ValueError("Invalid experiment variant")
        name = _text(variant.get("name"), "Variant name", 100)
        if unique and name.strip() in names:
            raise ValueError("Experiment variant names must be unique")
        names.add(name.strip())
        if variant.get("provider") and variant["provider"] not in {p[0] for p in _PROVIDERS}:
            raise ValueError("Unknown variant provider")
        if variant.get("model") is not None and (
            not isinstance(variant["model"], str) or unique and len(variant["model"]) > 200
        ):
            raise ValueError(
                "Variant model must be text, at most 200 characters for saved experiments"
            )
        if variant.get("effort") and str(variant["effort"]).strip().lower() not in EFFORT_LEVELS:
            raise ValueError(f"Variant effort must be one of {', '.join(EFFORT_LEVELS)}")
    return copy.deepcopy(variants)


def validate_experiment(data):
    from nexus.core.studio import _identity

    if (
        not isinstance(data, dict)
        or set(data)
        - {
            "kind",
            "version",
            "evaluation_id",
            "evaluation_revision",
            "runsPerExample",
            "passThreshold",
            "variants",
        }
        or data.get("kind", "experiment") != "experiment"
        or type(data.get("version", 1)) is not int
        or data.get("version", 1) != 1
    ):
        raise ValueError("Use a version 1 experiment with supported fields")
    if not isinstance(data.get("evaluation_id"), str):
        raise ValueError("Choose an evaluation suite ID")
    identity = _identity(data["evaluation_id"])
    revision, repeats, threshold = (
        data.get("evaluation_revision"),
        data.get("runsPerExample", 1),
        data.get("passThreshold", 1),
    )
    if type(revision) is not int or revision < 1:
        raise ValueError("Choose a positive evaluation suite revision")
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError("runsPerExample must be an integer from 1 to 20")
    if (
        type(threshold) not in {int, float}
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 1
    ):
        raise ValueError("passThreshold must be a finite number from 0 to 1")
    return {
        "kind": "experiment",
        "version": 1,
        "evaluation_id": identity,
        "evaluation_revision": revision,
        "runsPerExample": repeats,
        "passThreshold": threshold,
        "variants": validate_variants(data.get("variants"), unique=True),
    }


def prepare(studio, payload):
    payload.pop("experiment_snapshot", None)
    payload.pop("resume_graph", None)
    payload.pop("resume_yaml", None)
    payload.pop("workflow_snapshot", None)
    payload.pop("workflow_snapshots", None)
    payload["_experiment_settings"] = {"repeats": 1, "threshold": 1, "enforce": False}
    if payload.get("experiment_id"):
        if "evaluation_id" in payload or "variants" in payload:
            raise ValueError("Named experiments cannot override their suite or variants")
        with studio.lock:
            experiment = studio.catalog.get("experiments", payload["experiment_id"])
            settings = validate_experiment(experiment["data"])
            suite = studio.catalog.get("evaluations", settings["evaluation_id"])
            if suite["revision"] != settings["evaluation_revision"]:
                raise ValueError(
                    "Evaluation suite changed. Review the experiment's pinned revision before running"
                )
        payload.update(
            experiment_snapshot=experiment, evaluation_id=suite["id"], variants=settings["variants"]
        )
        payload["_experiment_settings"] = {
            "repeats": settings["runsPerExample"],
            "threshold": settings["passThreshold"],
            "enforce": True,
        }
    else:
        suite = studio.catalog.get("evaluations", payload.get("evaluation_id"))
        payload["variants"] = validate_variants(
            payload.get("variants", [{"name": "Saved configuration"}])
        )
    if (
        len(suite["data"]["cases"])
        * len(payload["variants"])
        * payload["_experiment_settings"]["repeats"]
        > MAX_EXECUTIONS
    ):
        raise ValueError(
            "Experiment exceeds the 400 case-execution limit; reduce cases, variants or repetitions"
        )
    payload["suite_snapshot"] = copy.deepcopy(suite)
    from nexus.core.studio_evaluation_files import pin_files

    pin_files(studio, payload)
    if suite["data"]["target_kind"] == "workflow":
        with studio.lock:
            payload["workflow_snapshot"] = studio.definition(
                "workflows", suite["data"]["target_id"]
            )
            payload["workflow_snapshots"] = {
                item["id"]: item["document"]
                for item in studio.definitions("workflows")
                if item.get("document") and item["document"].get("graph", {}).get("version") == 2
            }
            payload["_workflow_limits"] = dict(studio.workflow_limits)


def checkpoint_id(studio, run_id):
    """Resolve the current case using server-owned progress, never a caller-supplied ID."""
    if studio.run(run_id).get("kind") != "evaluation":
        return run_id
    path = studio._path("evaluation-progress", run_id)
    if not path.exists():
        return run_id
    progress = studio._read(path)
    return progress.get("checkpoint_id") or run_id


def continuation_payload(studio, run_id, workflow_payload, mode):
    """Continue the parent evaluation through the existing fenced job service."""
    if studio.run(run_id).get("kind") != "evaluation":
        return {**workflow_payload, mode: True}
    progress = studio._read(studio._path("evaluation-progress", run_id))
    if not progress.get("checkpoint_id") or progress.get("complete"):
        raise ValueError("Evaluation has no pending workflow case")
    return {**copy.deepcopy(progress["payload"]), mode: True}


def validate_continuation(studio, run_id, checkpoint):
    """Bind a reviewed decision to one pause, including its experiment repetition."""
    run = studio.run(run_id)
    if run.get("kind") == "evaluation":
        if not checkpoint or checkpoint != run.get("evaluation_checkpoint"):
            raise ValueError(
                "Experiment checkpoint changed. Review the current case before continuing"
            )
        if studio.team:
            studio.team.assert_execution(run.get("requester_nexus_id"))


def summarize(report, *, complete=False):
    variants = []
    expected = report["expected_total"] // len(report["variants"])
    complete = bool(
        complete and expected > 0 and len(report["results"]) == report["expected_total"]
    )
    for index, variant in enumerate(report["variants"], 1):
        results = [r for r in report["results"] if r["variant_index"] == index]
        if len(results) != expected:
            complete = False
        passed = sum(r["passed"] for r in results)
        variants.append(
            {
                "name": variant["name"],
                "index": index,
                "passed": passed,
                "total": len(results),
                "expected_total": expected,
                "pass_rate": passed / expected if expected else 0,
                "accepted": passed / expected >= report["pass_threshold"] if complete else None,
            }
        )
    if not complete:
        for variant in variants:
            variant["accepted"] = None
    report.update(
        variant_results=variants,
        complete=complete,
        acceptance=all(v["accepted"] for v in variants) if complete else None,
    )
    if complete:
        report["evaluation_status"] = "passed" if report["acceptance"] else "failed"
