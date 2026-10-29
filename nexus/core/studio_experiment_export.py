"""Reference-only declarations with explicit native settings and revision handoff."""

import copy
import json

import yaml

from nexus.core.studio_configuration import digest
from nexus.core.studio_configuration_handoff import LIMIT, read_handoff
from nexus.core.studio_experiment_import import convert_experiment
from nexus.core.studio_experiment_judges import copy_evaluator
from nexus.core.studio_experiments import validate_experiment
from nexus.core.studio_interop_configuration import identity

HANDOFF_FORMAT = "nexus-studio-experiment-handoff/v1"


def revision(value):
    if type(value) is not int or value < 1:
        raise ValueError("Select a positive integer resource revision")
    return value


def export_experiment(studio, data):
    from nexus.core.studio import _identity

    if not isinstance(data, dict) or set(data) != {"experiment_id", "revision", "dataset"}:
        raise ValueError("Select an experiment revision and matching dataset revision")
    if (
        not isinstance(data["experiment_id"], str)
        or not isinstance(data["dataset"], dict)
        or set(data["dataset"]) != {"id", "revision"}
        or not isinstance(data["dataset"]["id"], str)
    ):
        raise ValueError("Use exact native experiment and dataset identities")
    _identity(data["experiment_id"])
    _identity(data["dataset"]["id"])
    revision(data["revision"])
    revision(data["dataset"]["revision"])
    diagnostics = []

    def issue(path, code, message):
        diagnostics.append({"path": path, "code": code, "message": message})

    def selected(kind, ref, path):
        try:
            record = studio.catalog.get(kind, ref["id"])
        except (FileNotFoundError, ValueError):
            issue(
                path,
                "unavailable",
                "Selected resource is unavailable or archived; choose a current revision",
            )
            return None
        if record["revision"] != ref["revision"]:
            issue(path, "stale_revision", "Selected resource changed; review the current revision")
        return copy.deepcopy(record)

    with studio.lock:
        experiment = selected(
            "experiments",
            {"id": data["experiment_id"], "revision": data["revision"]},
            "/experiment",
        )
        dataset = selected("datasets", data["dataset"], "/dataset")
        if experiment is None:
            return {"valid": False, "diagnostics": diagnostics, "files": []}
        settings = validate_experiment(experiment["data"])
        suite = selected(
            "evaluations",
            {"id": settings["evaluation_id"], "revision": settings["evaluation_revision"]},
            "/experiment/evaluation_revision",
        )
        try:
            if identity(experiment["name"]) != experiment["id"]:
                raise ValueError
        except ValueError:
            issue(
                "/experiment/id",
                "identity_loss",
                "Name-derived declaration import cannot retain this custom experiment ID; use native configuration export",
            )
        copies = settings.get("evaluators", [])
        if not copies:
            issue(
                "/experiment/evaluators",
                "unsupported_inline_judges",
                "Select copied experiment evaluators before declaration export; per-case inline judges have no declaration equivalent",
            )
        for index, item in enumerate(copies):
            record = selected("evaluators", item, f"/experiment/evaluators/{index}")
            if record is not None and copy_evaluator(record) != item:
                issue(
                    f"/experiment/evaluators/{index}",
                    "changed_evaluator",
                    "The saved evaluator copy differs from the current catalog; use native configuration export to retain the copy",
                )
        target = None
        if suite:
            if suite["data"]["target_kind"] != "agent":
                issue(
                    "/suite/target_kind",
                    "unsupported_workflow",
                    "Declaration export supports agent targets; use native configuration export for workflows",
                )
            else:
                target = next(
                    (
                        copy.deepcopy(item)
                        for item in studio.definitions("agents")
                        if item["id"] == suite["data"]["target_id"] and item.get("document")
                    ),
                    None,
                )
                if target is None:
                    issue(
                        "/suite/target_id",
                        "unavailable_agent",
                        "The selected target agent is unavailable",
                    )
            if dataset:

                def effective(cases):
                    return [
                        {key: value for key, value in case.items() if key != "judge"}
                        for case in cases
                    ]

                if effective(dataset["data"]["cases"]) != effective(suite["data"]["cases"]):
                    issue(
                        "/dataset/cases",
                        "case_mismatch",
                        "Choose a dataset with the same ordered effective cases, checks, references and file roles as the pinned suite",
                    )
        if diagnostics:
            return {"valid": False, "diagnostics": diagnostics, "files": []}
        declaration = {
            "kind": "experiment",
            "name": experiment["name"],
            "description": experiment["description"],
            "spec": {
                "datasetId": dataset["id"],
                "targetAgent": target["id"],
                "evaluatorIds": [item["id"] for item in copies],
                "primaryEvaluatorId": settings["primaryEvaluatorId"],
                "runsPerExample": settings["runsPerExample"],
                "maxWorkers": settings["maxWorkers"],
            },
        }
        request = {
            "experiment": declaration,
            "bindings": {
                "dataset": {"id": dataset["id"], "revision": dataset["revision"]},
                "target": {"id": target["id"], "revision": target["revision"]},
                "evaluators": {
                    item["id"]: {"id": item["id"], "revision": item["revision"]} for item in copies
                },
            },
            "pass_threshold": settings["passThreshold"],
            "variants": copy.deepcopy(settings["variants"]),
        }
        # Reuse import preflight before exposing either file; conversion saves nothing.
        try:
            convert_experiment(studio, request)
        except (ValueError, FileNotFoundError):
            issue(
                "/handoff",
                "conversion_preflight",
                "The selected dependencies cannot round-trip through the supported import profile; use native configuration export",
            )
            return {"valid": False, "diagnostics": diagnostics, "files": []}
    source = {
        "experiment_id": experiment["id"],
        "revision": experiment["revision"],
        "suite_id": suite["id"],
        "suite_revision": suite["revision"],
    }
    handoff = {"format": HANDOFF_FORMAT, "source": source, "request": request}
    handoff["digest"] = digest(handoff)
    files = [
        {
            "filename": "experiment.yaml",
            "media_type": "application/yaml",
            "content": yaml.safe_dump(declaration, allow_unicode=True, sort_keys=False),
        },
        {
            "filename": "experiment-handoff.json",
            "media_type": "application/json",
            "content": json.dumps(handoff, ensure_ascii=False, indent=2, allow_nan=False),
        },
    ]
    try:
        size = sum(len(item["content"].encode()) for item in files)
    except UnicodeError:
        return {
            "valid": False,
            "diagnostics": [
                {
                    "path": "/files",
                    "code": "invalid_text",
                    "message": "Export requires valid UTF-8 text; review the selected configuration",
                }
            ],
            "files": [],
        }
    if size > LIMIT:
        return {
            "valid": False,
            "diagnostics": [
                {
                    "path": "/files",
                    "code": "size_limit",
                    "message": "Export exceeds two MiB; use native configuration export",
                }
            ],
            "files": [],
        }
    return {
        "valid": True,
        "diagnostics": [],
        "files": files,
        "source": source,
        "notes": [
            "The declaration alone omits native thresholds, variants, revision pins and all-evaluator acceptance. Keep the companion handoff for native reimport.",
            "Dependencies are references, not embedded resources. Reimport requires the same current dataset, evaluator copies and target revision; no folders, URLs, files or secrets are fetched.",
            "Reimport creates a fresh independent suite with the primary inline judge, then reviews an experiment update. Original standalone suite judges are not exported.",
            "The digest detects corruption; it does not authenticate the author or grant access. Review before saving or running.",
        ],
    }


def convert_handoff(studio, data):
    from nexus.core.studio import _identity

    handoff = read_handoff(data, HANDOFF_FORMAT)
    source = handoff["source"]
    if not isinstance(source, dict) or set(source) != {
        "experiment_id",
        "revision",
        "suite_id",
        "suite_revision",
    }:
        raise ValueError("Handoff source identity is invalid")
    for key in ("experiment_id", "suite_id"):
        if not isinstance(source[key], str):
            raise ValueError("Handoff source identities must be native IDs")
        _identity(source[key])
    revision(source["revision"])
    revision(source["suite_revision"])
    report = convert_experiment(studio, handoff["request"])
    report["handoff_source"] = copy.deepcopy(source)
    return report
