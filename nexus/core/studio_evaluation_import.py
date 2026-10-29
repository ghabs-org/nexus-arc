"""Convert explicitly supplied evaluation documents/files into native drafts."""

import csv
import hashlib
import io
import json
import re

import yaml

from nexus.core.studio_catalog import _text
from nexus.core.studio_datasets import DATASET_LIMIT, validate_dataset
from nexus.core.studio_evaluators import validate_evaluator
from nexus.core.studio_interop_configuration import PortableConfigurationLoader, identity
from nexus.core.studio_judge_templates import VARIABLE, validate_template

IMPORTED_RUBRIC = "Grade using the imported template and configured choice scores."


def resource(value, kind, fields):
    if isinstance(value, str):
        try:
            value = yaml.load(value, Loader=PortableConfigurationLoader)
        except (yaml.YAMLError, ValueError, RecursionError):
            raise ValueError(
                "Use bounded resource YAML/JSON with unique keys and no aliases"
            ) from None
    if (
        not isinstance(value, dict)
        or set(value) - {"kind", "name", "description", "spec"}
        or value.get("kind") != kind
    ):
        raise ValueError("Choose supported dataset/evaluator resource documents")
    spec = value.get("spec")
    if not isinstance(spec, dict) or set(spec) != fields:
        raise ValueError("Resource spec has missing or unsupported fields")
    description = value.get("description", "Imported evaluation resource")
    if not isinstance(description, str) or len(description.encode()) > 2000:
        raise ValueError("Description must be text up to 2000 bytes")
    return {
        "name": _text(value.get("name"), "Resource name", 200),
        "description": description,
        "spec": spec,
    }


def convert_package(data):
    if not isinstance(data, dict) or set(data) - {
        "dataset",
        "evaluator",
        "files",
        "binding",
        "threshold",
    }:
        raise ValueError("Supply dataset, evaluator, files and explicit judge binding")
    try:
        if len(json.dumps(data, allow_nan=False).encode()) > 2 * 1024 * 1024:
            raise ValueError
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Evaluation package must fit 2 MiB of JSON-compatible data") from None
    dataset = resource(data.get("dataset"), "dataset", {"examples_file"})
    evaluator = resource(
        data.get("evaluator"), "evaluator", {"type", "model", "promptTemplate", "choiceScores"}
    )
    filename = dataset["spec"]["examples_file"]
    if not isinstance(filename, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_. -]{0,195}\.csv", filename
    ):
        raise ValueError("Select a CSV basename, never a directory, server path or URL")
    files = data.get("files")
    if not isinstance(files, dict) or set(files) != {filename}:
        raise ValueError("Explicitly supply exactly the referenced CSV file")
    content = files[filename]
    if not isinstance(content, str) or not 0 < len(content.encode()) <= DATASET_LIMIT:
        raise ValueError("Supply nonempty UTF-8 CSV text up to 900 KiB")
    spec, binding = evaluator["spec"], data.get("binding")
    if (
        not isinstance(binding, dict)
        or set(binding) - {"alias", "provider", "model"}
        or binding.get("alias") != spec["model"]
        or not isinstance(spec["model"], str)
        or not spec["model"]
    ):
        raise ValueError("Bind the evaluator model alias explicitly to a native judge provider")
    if spec["type"] != "llm_judge":
        raise ValueError("Only llm_judge evaluation templates are supported")
    template = validate_template(spec["promptTemplate"])
    if set(VARIABLE.findall(template)) != {"Prompt", "Expected Response", "Response"}:
        raise ValueError(
            "Imported templates require Prompt, Expected Response and Response exactly once"
        )
    judge = validate_evaluator(
        {
            "provider": binding.get("provider"),
            "model": binding.get("model", ""),
            "rubric": IMPORTED_RUBRIC,
            "prompt_template": template,
            "choices": spec["choiceScores"],
            "threshold": data.get("threshold", 0.7),
        }
    )
    cases, numbers = [], []
    try:
        reader = csv.reader(io.StringIO(content.removeprefix("\ufeff"), newline=""), strict=True)
        headers = next(reader, [])
        if len(headers) != 3 or set(headers) != {"sequence_number", "prompt", "expected_response"}:
            raise ValueError(
                "CSV requires unique sequence_number, prompt and expected_response headers"
            )
        for cells in reader:
            if not cells:
                continue
            if len(cells) != 3:
                raise ValueError("CSV records must have exactly three cells")
            row = dict(zip(headers, cells, strict=True))
            if (
                not re.fullmatch(r"[1-9][0-9]{0,8}", row["sequence_number"])
                or int(row["sequence_number"]) in numbers
            ):
                raise ValueError("Sequence numbers must be unique positive integers")
            numbers.append(int(row["sequence_number"]))
            cases.append(
                {
                    "input": row["prompt"],
                    "expected_response": row["expected_response"],
                    "judge": {
                        key: value
                        for key, value in judge.items()
                        if key not in {"kind", "version", "type"}
                    },
                }
            )
            if len(cases) > 100:
                raise ValueError("A dataset supports at most 100 cases")
    except csv.Error:
        raise ValueError("Malformed CSV record") from None
    canonical = validate_dataset({"cases": cases})
    resources = [
        {
            "kind": kind,
            "id": identity(source["name"]),
            "name": source["name"],
            "description": source["description"],
            "data": value,
        }
        for kind, source, value in (
            ("datasets", dataset, canonical),
            ("evaluators", evaluator, judge),
        )
    ]
    return {
        "document": {"format": "nexus-studio-config/v1", "definitions": [], "resources": resources},
        "dataset": canonical,
        "evaluator": judge,
        "source": {
            "filename": filename,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "sequence_numbers": numbers,
        },
        "adaptations": [
            "Template values are inserted once as untrusted JSON strings, not evaluated expressions.",
            "Judge returns native validated JSON choice/reason rather than a bare label.",
            "Choice scores use the explicitly selected native pass threshold; dataset cases copy judge settings.",
            "Sequence numbers are validated and reported; case order follows CSV row order.",
        ],
    }
