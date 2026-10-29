"""Bounded, non-executing dataset import using the existing evaluation case schema."""

import csv
import io
import json

import yaml

from nexus.core.studio_evaluations import validate_case
from nexus.core.studio_interop_configuration import PortableConfigurationLoader

DATASET_LIMIT = 900 * 1024
CASE_FIELDS = {"input", "contains", "excludes", "checks", "judge", "contexts", "context_source", "input_files", "reference_files"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Dataset keys must be unique")
        result[key] = value
    return result


def read_json(content):
    def invalid_constant(value):
        raise ValueError("Dataset numbers must be finite")

    return json.loads(content, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def validate_dataset(value):
    if isinstance(value, list):
        value = {"cases": value}
    if (
        not isinstance(value, dict)
        or set(value) - {"kind", "version", "cases"}
        or value.get("kind", "dataset") != "dataset"
        or type(value.get("version", 1)) is not int
        or value.get("version", 1) != 1
    ):
        raise ValueError("Use a version 1 dataset with only kind, version and cases")
    cases = value.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError("A dataset needs 1–100 evaluation cases")
    normalized = []
    for index, case in enumerate(cases, 1):
        try:
            if not isinstance(case, dict) or set(case) - CASE_FIELDS:
                raise ValueError("Unsupported evaluation case fields")
            checks = case.get("checks", [])
            if isinstance(checks, list):
                fields = {
                    "exact": {"type", "expected"},
                    "json_equals": {"type", "path", "expected"},
                    "number_range": {"type", "path", "min", "max"},
                }
                for check in checks:
                    if isinstance(check, dict) and set(check) - fields.get(
                        check.get("type"), set()
                    ):
                        raise ValueError("Unsupported structured check fields")
            normalized.append(validate_case(case))
        except RecursionError:
            raise ValueError(f"Dataset case {index}: excessive nesting") from None
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Dataset case {index}: {exc}") from None
    result = {"kind": "dataset", "version": 1, "cases": normalized}
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > DATASET_LIMIT:
        raise ValueError("Normalized dataset exceeds 900 KiB")
    return result


def read_csv(content):
    reader = csv.reader(io.StringIO(content, newline=""), strict=True)
    headers = next(reader, [])
    allowed = {"input", "expected", "contains", "excludes", "contexts", "checks", "judge", "input_files", "reference_files"}
    if (
        "input" not in headers
        or len(headers) < 2
        or len(set(headers)) != len(headers)
        or set(headers) - allowed
    ):
        raise ValueError(
            "CSV needs unique input and assertion headers; unsupported columns are rejected"
        )
    cases = []
    for row in reader:
        if not row:
            continue
        if len(row) != len(headers):
            raise ValueError(f"CSV record {len(cases) + 1} has missing or extra cells")
        values = dict(zip(headers, row, strict=True))
        case = {"input": values["input"]}
        for key in allowed - {"input", "expected"}:
            if values.get(key, "").strip():
                case[key] = read_json(values[key])
        if values.get("expected", ""):
            if not isinstance(case.get("checks", []), list):
                raise ValueError("CSV checks must be a JSON list")
            case.setdefault("checks", []).append({"type": "exact", "expected": values["expected"]})
        cases.append(case)
        if len(cases) > 100:
            raise ValueError("A dataset supports at most 100 cases")
    return cases


def parse_dataset(payload):
    if not isinstance(payload, dict) or set(payload) - {"format", "content"}:
        raise ValueError("Dataset import accepts only format and content")
    content = payload.get("content")
    if not isinstance(content, str) or not 0 < len(content.encode("utf-8")) <= DATASET_LIMIT:
        raise ValueError("Choose nonempty UTF-8 dataset text up to 900 KiB")
    content = content.removeprefix("\ufeff")
    try:
        if payload.get("format") == "json":
            value = read_json(content)
        elif payload.get("format") == "yaml":
            value = yaml.load(content, Loader=PortableConfigurationLoader)
        elif payload.get("format") == "csv":
            value = read_csv(content)
        else:
            raise ValueError("Choose csv, yaml or json dataset format")
        return validate_dataset(value)
    except (yaml.YAMLError, csv.Error, RecursionError):
        raise ValueError("Invalid or excessively nested dataset file") from None
