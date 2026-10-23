"""Versioned, typed Nuclia templates using Nexus's non-executable variable grammar."""

import copy
import json
import math
import re

from nexus.core.studio_catalog import _text
from nexus.core.studio_prompts import VARIABLE, validate_prompt

TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float)}


def typed(value, parameter):
    if value is None:
        return parameter.get("nullable", False)
    expected = TYPES[parameter.get("type", "string")]
    if not isinstance(expected, tuple):
        expected = (expected,)
    return (
        type(value) in expected
        and (not isinstance(value, str) or len(value.encode()) <= 4000)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def validate_template(value):
    if (
        not isinstance(value, dict)
        or set(value) - {"version", "parameters", "query_prompt", "filter_expression"}
        or type(value.get("version")) is not int
        or value["version"] != 1
    ):
        raise ValueError(
            "Nuclia template requires version 1 and supported parameter/prompt/filter fields"
        )
    parameters = value.get("parameters", [])
    if not isinstance(parameters, list) or len(parameters) > 30:
        raise ValueError("Nuclia templates support up to thirty parameters")
    names = {"query"}
    for parameter in parameters:
        if not isinstance(parameter, dict) or set(parameter) - {
            "name",
            "type",
            "required",
            "nullable",
            "default",
            "description",
            "context_path",
        }:
            raise ValueError("Invalid Nuclia template parameter")
        name = parameter.get("name")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name)
            or name in names
            or not isinstance(parameter.get("type", "string"), str)
            or parameter.get("type", "string") not in TYPES
        ):
            raise ValueError(
                "Nuclia parameter names must be unique typed identifiers; query is reserved"
            )
        names.add(name)
        if "context_path" in parameter:
            path = parameter["context_path"]
            if (
                not isinstance(path, list)
                or not 1 <= len(path) <= 8
                or any(
                    not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key)
                    for key in path
                )
            ):
                raise ValueError("Nuclia context path needs one to eight plain object keys")
        if any(type(parameter.get(key, False)) is not bool for key in ("required", "nullable")):
            raise ValueError("Nuclia parameter required/nullable flags must be boolean")
        if "description" in parameter and (
            not isinstance(parameter["description"], str) or len(parameter["description"]) > 1000
        ):
            raise ValueError("Nuclia parameter description exceeds supported text bounds")
        if "default" in parameter and not typed(parameter["default"], parameter):
            raise ValueError("Nuclia parameter default does not match its declared type")

    def inspect(part, depth=0):
        if depth > 8:
            raise ValueError("Nuclia template exceeds nesting limit")
        if isinstance(part, str):
            # Reuse the shared prompt parser; no attribute access, format specifiers or evaluation.
            parsed = validate_prompt({"content": part}) if part else {"content": ""}
            if set(parsed.get("variables", {})) - names:
                raise ValueError("Nuclia template references an undeclared parameter")
        elif isinstance(part, dict):
            for key, child in part.items():
                if not isinstance(key, str) or VARIABLE.search(key):
                    raise ValueError("Nuclia template keys cannot be dynamic")
                inspect(child, depth + 1)
        elif isinstance(part, list):
            for child in part:
                inspect(child, depth + 1)
        elif part is not None and type(part) not in (bool, int, float):
            raise ValueError("Nuclia templates contain JSON values only")

    prompt = value.get("query_prompt")
    if "query_prompt" in value:
        if (
            not isinstance(prompt, str)
            or "query" not in {name.strip() for name in VARIABLE.findall(prompt)}
            or len(prompt.encode()) > 4000
        ):
            raise ValueError("Nuclia query prompt must contain {{query}} and fit 4000 bytes")
        inspect(prompt)
    expression = value.get("filter_expression")
    if "filter_expression" in value:
        if (
            not isinstance(expression, dict)
            or not expression
            or set(expression) - {"field", "paragraph", "operator"}
            or not any(key in expression for key in ("field", "paragraph"))
        ):
            raise ValueError("Nuclia filter template uses field/paragraph and optional operator")
        if expression.get("operator", "and") not in {"and", "or"} or any(
            not isinstance(expression[key], dict)
            for key in ("field", "paragraph")
            if key in expression
        ):
            raise ValueError("Nuclia filter template needs object predicates and and/or operator")
        inspect(expression)
    if len(json.dumps(value, allow_nan=False).encode()) > 16384:
        raise ValueError("Nuclia template exceeds 16 KiB")
    return copy.deepcopy(value)


def prepare_arguments(template, arguments, *, context=None):
    template = validate_template(template)
    supplied = arguments.get("parameters", {})
    definitions = {parameter["name"]: parameter for parameter in template.get("parameters", [])}
    if not isinstance(supplied, dict) or set(supplied) - definitions.keys():
        raise ValueError("Supply only declared Nuclia runtime parameters")
    values = {"query": _text(arguments.get("query"), "Search query", 4000)}
    for name, parameter in definitions.items():
        missing = object()
        contextual = missing
        if "context_path" in parameter:
            if name in supplied:
                raise ValueError("Runtime values cannot override server context: " + name)
            contextual = context
            for key in parameter["context_path"]:
                if not isinstance(contextual, dict) or key not in contextual:
                    contextual = missing
                    break
                contextual = contextual[key]
        if contextual is not missing:
            value = contextual
        elif name in supplied:
            value = supplied[name]
        elif "default" in parameter:
            value = parameter["default"]
        elif parameter.get("required", False):
            raise ValueError("Fill required Nuclia parameter: " + name)
        elif parameter.get("nullable", False):
            value = None
        else:
            continue
        if not typed(value, parameter):
            raise ValueError("Nuclia runtime parameter does not match declared type: " + name)
        values[name] = value

    def selected(name):
        name = name.strip()
        if name not in values:
            raise ValueError("Fill referenced Nuclia parameter: " + name)
        return values[name]

    def expand(part):
        if isinstance(part, str):
            match = VARIABLE.fullmatch(part)
            if match:
                return selected(match[1])

            def text(match):
                value = selected(match[1])
                return value if isinstance(value, str) else json.dumps(value, allow_nan=False)

            return VARIABLE.sub(text, part)
        if isinstance(part, dict):
            return {key: expand(child) for key, child in part.items()}
        if isinstance(part, list):
            return [expand(child) for child in part]
        return part

    result = {key: value for key, value in arguments.items() if key != "parameters"}
    if "query_prompt" in template:
        result["query"] = expand(template["query_prompt"])
    if "filter_expression" in template:
        if "filter_expression" in arguments:
            raise ValueError(
                "Runtime filters cannot override the configured Nuclia filter template"
            )
        result["filter_expression"] = expand(template["filter_expression"])
    return result
