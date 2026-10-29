"""Compile a bounded experiment declaration into reviewed native resources."""

import copy
import json
import uuid

import yaml

from nexus.core.studio_catalog import _text
from nexus.core.studio_datasets import validate_dataset
from nexus.core.studio_experiment_judges import copy_evaluator
from nexus.core.studio_experiments import MAX_EXECUTIONS, validate_experiment, validate_workers
from nexus.core.studio_interop_configuration import PortableConfigurationLoader, identity


def convert_experiment(studio, data):
    if not isinstance(data, dict) or set(data) - {
        "experiment",
        "bindings",
        "pass_threshold",
        "variants",
    }:
        raise ValueError("Supply experiment, explicit resource bindings and pass threshold")
    try:
        if len(json.dumps(data, allow_nan=False).encode()) > 2 * 1024 * 1024:
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Experiment import must fit 2 MiB of JSON-compatible data") from None
    source = data.get("experiment")
    if isinstance(source, str):
        try:
            source = yaml.load(source, Loader=PortableConfigurationLoader)
        except (yaml.YAMLError, ValueError, RecursionError):
            raise ValueError("Use bounded YAML/JSON with unique keys and no aliases") from None
    if (
        not isinstance(source, dict)
        or set(source) - {"kind", "name", "description", "spec"}
        or source.get("kind") != "experiment"
    ):
        raise ValueError("Choose a supported experiment resource")
    name = _text(source.get("name"), "Experiment name", 200)
    description = source.get("description", "")
    if not isinstance(description, str) or len(description.encode()) > 2000:
        raise ValueError("Description must be text up to 2000 bytes")
    spec = source.get("spec")
    required = {"datasetId", "targetAgent", "evaluatorIds", "primaryEvaluatorId"}
    if (
        not isinstance(spec, dict)
        or required - set(spec)
        or set(spec) - required - {"runsPerExample", "maxWorkers"}
    ):
        raise ValueError("Experiment spec has missing or unsupported fields")
    for key in ("datasetId", "targetAgent", "primaryEvaluatorId"):
        _text(spec[key], key, 200)
    aliases = spec["evaluatorIds"]
    if (
        not isinstance(aliases, list)
        or not 1 <= len(aliases) <= 4
        or any(
            not isinstance(alias, str) or not alias.strip() or len(alias) > 200 for alias in aliases
        )
        or len(set(aliases)) != len(aliases)
        or spec["primaryEvaluatorId"] not in aliases
    ):
        raise ValueError(
            "Select one to four distinct evaluator aliases including the primary evaluator"
        )
    workers = validate_workers(spec.get("maxWorkers", 1))
    bindings = data.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("Select explicit dataset, evaluator and target revision bindings")
    if set(bindings) == {"dataset", "evaluator", "target"} and len(aliases) == 1:
        evaluator_bindings = {aliases[0]: bindings["evaluator"]}
    elif (
        set(bindings) == {"dataset", "evaluators", "target"}
        and isinstance(bindings["evaluators"], dict)
        and set(bindings["evaluators"]) == set(aliases)
    ):
        evaluator_bindings = bindings["evaluators"]
    else:
        raise ValueError(
            "Map every evaluator alias to an exact saved revision, without extra bindings"
        )
    for binding in [bindings["dataset"], bindings["target"], *evaluator_bindings.values()]:
        if (
            not isinstance(binding, dict)
            or set(binding) != {"id", "revision"}
            or not isinstance(binding.get("id"), str)
        ):
            raise ValueError("Bindings require an existing ID and exact revision")
    with studio.lock:

        def selected(kind, binding):
            record = studio.catalog.get(kind, binding["id"])
            if (
                type(binding["revision"]) is not int
                or binding["revision"] < 1
                or binding["revision"] != record["revision"]
            ):
                raise ValueError("Selected resource revision changed; review bindings again")
            return copy.deepcopy(record)

        dataset_record = selected("datasets", bindings["dataset"])
        evaluators = [
            copy_evaluator(selected("evaluators", evaluator_bindings[alias])) for alias in aliases
        ]
        target = next(
            (
                item
                for item in studio.definitions("agents")
                if item["id"] == bindings["target"]["id"] and item.get("document")
            ),
            None,
        )
        if target is None:
            raise FileNotFoundError("Selected target agent is unavailable")
        if bindings["target"]["revision"] != target["revision"]:
            raise ValueError("Target revision changed; review its current definition")
        target = copy.deepcopy(target)
    dataset = validate_dataset(dataset_record["data"])
    primary_id = evaluator_bindings[spec["primaryEvaluatorId"]]["id"]
    for evaluator in evaluators:
        validate_dataset(
            {"cases": [{**case, "judge": evaluator["judge"]} for case in dataset["cases"]]}
        )
    primary = next(item for item in evaluators if item["id"] == primary_id)
    cases = validate_dataset(
        {"cases": [{**case, "judge": copy.deepcopy(primary["judge"])} for case in dataset["cases"]]}
    )["cases"]
    suite_id = identity(name)[:48] + "-import-" + uuid.uuid4().hex
    native = validate_experiment(
        {
            "evaluation_id": suite_id,
            "evaluation_revision": 1,
            "runsPerExample": spec.get("runsPerExample", 1),
            "passThreshold": data.get("pass_threshold"),
            "maxWorkers": workers,
            "evaluators": evaluators,
            "primaryEvaluatorId": primary_id,
            "evaluatorPolicy": "all",
            "variants": data.get("variants", [{"name": "Saved agent configuration"}]),
        }
    )
    count = len(cases) * native["runsPerExample"] * len(native["variants"])
    if count > MAX_EXECUTIONS:
        raise ValueError("Experiment exceeds the 400 case-execution limit")
    document = {
        "format": "nexus-studio-config/v1",
        "definitions": [],
        "resources": [
            {
                "kind": "evaluations",
                "id": suite_id,
                "name": name[:175] + " evaluation suite",
                "description": description,
                "data": {"target_kind": "agent", "target_id": target["id"], "cases": cases},
            },
            {
                "kind": "experiments",
                "id": identity(name),
                "name": name,
                "description": description,
                "data": native,
            },
        ],
    }
    return {
        "document": studio.configuration.normalize(document),
        "planned_executions": count,
        "source": {
            "dataset_alias": spec["datasetId"],
            "evaluator_alias": spec["primaryEvaluatorId"],
            "evaluator_aliases": aliases,
            "target_alias": spec["targetAgent"],
            "bindings": copy.deepcopy(bindings),
        },
        "adaptations": [
            "Dataset cases and selected judge settings are copied independently; selected experiment evaluators replace prior case judges.",
            "The experiment pins the new suite at revision 1; source resources are not modified.",
            "All selected evaluators must pass each case; the primary evaluator labels results and cannot hide another evaluator failure.",
            f"Native pass threshold and variants are explicit; at most {workers} agent case/judge pairs run concurrently.",
            "The saved target agent's provider is used unless a reviewed native variant overrides it; targets are pinned when a run is queued.",
        ],
    }
