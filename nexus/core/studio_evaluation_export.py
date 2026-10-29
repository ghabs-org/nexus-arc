"""Inverse of the bounded CSV/template import profile, without lossy conversion."""

import copy
import csv
import io
import json

import yaml

from nexus.core.studio_catalog import _text
from nexus.core.studio_configuration import digest
from nexus.core.studio_configuration_handoff import LIMIT, read_handoff
from nexus.core.studio_datasets import DATASET_LIMIT
from nexus.core.studio_evaluation_import import IMPORTED_RUBRIC, convert_package
from nexus.core.studio_experiment_judges import copy_evaluator
from nexus.core.studio_interop_configuration import identity
from nexus.core.studio_judge_templates import VARIABLE

HANDOFF_FORMAT = "nexus-studio-evaluation-handoff/v1"


def reference(value):
    from nexus.core.studio import _identity

    if (
        not isinstance(value, dict)
        or set(value) != {"id", "revision"}
        or not isinstance(value["id"], str)
    ):
        raise ValueError("Select an exact dataset/evaluator ID and revision")
    _identity(value["id"])
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ValueError("Select a positive integer resource revision")
    return dict(value)


def export_package(studio, data):
    if not isinstance(data, dict) or set(data) != {"dataset", "evaluator", "model_alias"}:
        raise ValueError("Select dataset/evaluator revisions and an explicit external model alias")
    refs = {key: reference(data[key]) for key in ("dataset", "evaluator")}
    alias = _text(data["model_alias"], "External model alias", 200)
    diagnostics = []

    def issue(path, code, message):
        diagnostics.append({"path": path, "code": code, "message": message})

    selected = {}
    with studio.lock:
        for key, ref in refs.items():
            try:
                record = studio.catalog.get(key + "s", ref["id"])
            except (ValueError, FileNotFoundError):
                issue("/" + key, "unavailable", "Selected resource is unavailable or archived")
                continue
            selected[key] = copy.deepcopy(record)
            if ref["revision"] != record["revision"]:
                issue(
                    "/" + key + "/revision",
                    "stale_revision",
                    "Selected resource changed; review its current revision",
                )
            try:
                if identity(record["name"]) != record["id"]:
                    raise ValueError
            except ValueError:
                issue(
                    "/" + key + "/id",
                    "identity_loss",
                    "Declaration import derives IDs from names; use native JSON for custom IDs",
                )
    if len(selected) < 2:
        return {"valid": False, "diagnostics": diagnostics, "files": []}
    dataset, evaluator = selected["dataset"], selected["evaluator"]
    judge = copy_evaluator(evaluator)["judge"]
    if judge.get("rubric") != IMPORTED_RUBRIC:
        issue(
            "/evaluator/rubric",
            "unsupported_rubric",
            "Arbitrary native rubrics are not represented by the template declaration; use native JSON export",
        )
    if "choices" not in judge:
        issue(
            "/evaluator/choices",
            "unsupported_numeric_judge",
            "Declaration export requires configured choice scores",
        )
    if set(VARIABLE.findall(judge.get("prompt_template", ""))) != {
        "Prompt",
        "Expected Response",
        "Response",
    }:
        issue(
            "/evaluator/prompt_template",
            "unsupported_template",
            "Declaration export requires all three fixed template variables",
        )
    for index, case in enumerate(dataset["data"]["cases"]):
        path = f"/dataset/cases/{index}"
        if "expected_response" not in case:
            issue(
                path + "/expected_response",
                "missing_reference",
                "CSV export requires an expected response for every case",
            )
        if case.get("contains") or case.get("excludes"):
            issue(
                path,
                "unsupported_phrase_checks",
                "Three-column CSV cannot retain phrase checks; use native JSON export",
            )
        for key in sorted(
            set(case) - {"input", "expected_response", "contains", "excludes", "judge"}
        ):
            issue(
                path + "/" + key,
                "unsupported_case_field",
                "CSV declarations cannot retain this native case field; use native JSON export",
            )
        if case.get("judge") != judge:
            issue(
                path + "/judge",
                "judge_mismatch",
                "The case must contain the exact selected evaluator settings; no judge is replaced during export",
            )
    if diagnostics:
        return {"valid": False, "diagnostics": diagnostics, "files": []}
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(["sequence_number", "prompt", "expected_response"])
    for index, case in enumerate(dataset["data"]["cases"], 1):
        writer.writerow([index, case["input"], case["expected_response"]])
    csv_text = stream.getvalue()
    request = {
        "dataset": {
            "kind": "dataset",
            "name": dataset["name"],
            "description": dataset["description"],
            "spec": {"examples_file": "cases.csv"},
        },
        "evaluator": {
            "kind": "evaluator",
            "name": evaluator["name"],
            "description": evaluator["description"],
            "spec": {
                "type": "llm_judge",
                "model": alias,
                "promptTemplate": judge["prompt_template"],
                "choiceScores": copy.deepcopy(judge["choices"]),
            },
        },
        "files": {"cases.csv": csv_text},
        "binding": {"alias": alias, "provider": judge["provider"], "model": judge["model"]},
        "threshold": judge["threshold"],
    }
    try:
        converted = convert_package(request)
        for resource in converted["document"]["resources"]:
            original = selected["dataset" if resource["kind"] == "datasets" else "evaluator"]
            if any(resource[key] != original[key] for key in ("id", "name", "description", "data")):
                issue(
                    "/" + resource["kind"],
                    "roundtrip_mismatch",
                    "The declaration profile would change saved metadata or data; use native JSON export",
                )
    except (ValueError, UnicodeError, RecursionError):
        issue(
            "/package",
            "conversion_preflight",
            "The declaration package cannot round-trip within the supported import limits; use native JSON export",
        )
    if diagnostics:
        return {"valid": False, "diagnostics": diagnostics, "files": []}
    handoff = {"format": HANDOFF_FORMAT, "source": refs, "request": request}
    handoff["digest"] = digest(handoff)
    files = [
        {
            "filename": "dataset.yaml",
            "media_type": "application/yaml",
            "content": yaml.safe_dump(request["dataset"], allow_unicode=True, sort_keys=False),
        },
        {
            "filename": "evaluator.yaml",
            "media_type": "application/yaml",
            "content": yaml.safe_dump(request["evaluator"], allow_unicode=True, sort_keys=False),
        },
        {"filename": "cases.csv", "media_type": "text/csv", "content": csv_text},
        {
            "filename": "evaluation-handoff.json",
            "media_type": "application/json",
            "content": json.dumps(handoff, ensure_ascii=False, indent=2, allow_nan=False),
        },
    ]
    try:
        if (
            any(len(item["content"].encode()) > DATASET_LIMIT for item in files[:3])
            or sum(len(item["content"].encode()) for item in files) > LIMIT
        ):
            issue(
                "/files",
                "size_limit",
                "Prepared files exceed the package's two MiB or per-input 900 KiB limit",
            )
    except UnicodeError:
        issue("/files", "invalid_text", "Export requires valid UTF-8 text")
    if diagnostics:
        return {"valid": False, "diagnostics": diagnostics, "files": []}
    return {
        "valid": True,
        "diagnostics": [],
        "source": refs,
        "files": files,
        "binding": request["binding"],
        "threshold": request["threshold"],
        "notes": [
            "Files embed full prompts, expected answers and judge instructions. Review before downloading and keep the package private.",
            "Row order is retained; sequence numbers are regenerated as 1..N because original source sequence IDs are not stored in native cases.",
            "The three declarations/CSV preserve this bounded template profile. Keep the native companion for provider, model and threshold settings; external model aliases alone do not carry those settings.",
            "Native grading uses validated JSON choice/reason and one-pass untrusted JSON template data. Other runtimes may use different rendering or acceptance behavior.",
            "Reimport creates only reviewed dataset/evaluator drafts; it neither configures credentials nor launches AI. Source identity and the corruption-check digest grant no authority.",
        ],
    }


def convert_handoff(data):
    handoff = read_handoff(data, HANDOFF_FORMAT)
    source = handoff["source"]
    if not isinstance(source, dict) or set(source) != {"dataset", "evaluator"}:
        raise ValueError(
            "Evaluation handoff requires descriptive dataset/evaluator source identities"
        )
    source = {key: reference(source[key]) for key in source}
    report = convert_package(handoff["request"])
    report["handoff_source"] = source
    return report
