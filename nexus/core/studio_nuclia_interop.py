"""Explicit, non-executing adaptation of frozen reference Nuclia template settings.

This converts template settings only, not a complete Python tool descriptor.
Connection credentials, provider defaults and resource activation remain native
configuration responsibilities. No source package is imported or executed.
"""

import copy
import json
import re
import string

from nexus.core.studio_nuclia_templates import validate_template

POLICY = "nexus-strict-v1"
ADAPTATIONS = (
    "Runtime values move from top-level tool arguments to arguments.parameters.",
    "Missing required values fail; explicit null does not select a non-null default.",
    "Missing referenced values fail instead of falling back to the original query.",
    "Configured filters are never removed for missing or empty parameter values.",
    "Whole-value placeholders retain JSON types; interpolated scalars use JSON spelling.",
)


def convert_template_settings(source, *, policy):
    """Return a native template and declared adaptations after explicit policy selection.

    Only named scalar placeholders are portable. Attribute/index lookups, format
    specifications, conversions, context expressions and unknown fields fail.
    The returned template still passes ordinary configuration preflight; calling
    this function never writes records, resolves credentials or activates tools.
    """
    if policy != POLICY:
        raise ValueError("Select the explicit Nuclia template conversion policy nexus-strict-v1")
    if (
        not isinstance(source, dict)
        or not source
        or set(source) - {"template_parameters", "prompt_rephrasing", "filter_expression_template"}
    ):
        raise ValueError(
            "Supply only reference Nuclia template settings; adapt other tool settings separately"
        )
    try:
        encoded = json.dumps(source, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("reference Nuclia template must contain finite JSON values") from None
    if len(encoded) > 16384:
        raise ValueError("reference Nuclia template exceeds 16 KiB")
    parameters = source.get("template_parameters", [])
    if not isinstance(parameters, list) or len(parameters) > 30:
        raise ValueError("reference Nuclia template supports up to thirty parameters")
    normalized = []
    for parameter in parameters:
        if not isinstance(parameter, dict) or set(parameter) - {
            "name",
            "type",
            "required",
            "nullable",
            "default",
            "description",
        }:
            raise ValueError(
                "Unsupported reference Nuclia parameter fields; context expressions need manual adaptation"
            )
        normalized.append({"nullable": True, **copy.deepcopy(parameter)})

    def translate(value, depth=0):
        if depth > 8:
            raise ValueError("reference Nuclia template exceeds nesting limit")
        if isinstance(value, str):
            parts = []
            try:
                for literal, name, specification, conversion in string.Formatter().parse(value):
                    if "{{" in literal or "}}" in literal:
                        raise ValueError
                    parts.append(literal)
                    if name is not None:
                        if (
                            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name)
                            or specification
                            or conversion
                        ):
                            raise ValueError
                        parts.append("{{" + name + "}}")
            except ValueError:
                raise ValueError(
                    "Use named scalar placeholders only in reference Nuclia templates"
                ) from None
            result = "".join(parts)
            # Adjacent escaped braces must not turn literal text into a native variable.
            native_names = re.findall(r"\{\{\s*([A-Za-z][A-Za-z0-9_]{0,63})\s*\}\}", result)
            source_names = [
                name for _, name, _, _ in string.Formatter().parse(value) if name is not None
            ]
            if native_names != source_names:
                raise ValueError("Escaped braces conflict with native Nuclia placeholders")
            return result
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("reference Nuclia template keys must be strings")
            return {key: translate(child, depth + 1) for key, child in value.items()}
        if isinstance(value, list):
            return [translate(child, depth + 1) for child in value]
        return value

    native = {"version": 1, "parameters": normalized}
    prompt = source.get("prompt_rephrasing")
    if prompt is not None:
        if (
            not isinstance(prompt, dict)
            or set(prompt) != {"template"}
            or not isinstance(prompt["template"], str)
        ):
            raise ValueError("reference Nuclia prompt rephrasing requires only template text")
        native["query_prompt"] = translate(prompt["template"])
    expression = source.get("filter_expression_template")
    if expression is not None:
        native["filter_expression"] = translate(expression)
    return {
        "policy": POLICY,
        "adaptations": list(ADAPTATIONS),
        "template": validate_template(native),
    }
