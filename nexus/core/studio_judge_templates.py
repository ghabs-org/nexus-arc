"""Bounded judge templates with fixed variables and one-pass data substitution."""

import json
import re

VARIABLE = re.compile(r"\{\{\s*(Prompt|Expected Response|Response)\s*\}\}")


def validate_template(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 16000:
        raise ValueError("Judge template must be nonempty text up to 16000 characters")
    names = VARIABLE.findall(value)
    remainder = VARIABLE.sub("", value)
    if (
        "{{" in remainder
        or "}}" in remainder
        or "{%" in value
        or "{#" in value
        or len(names) != len(set(names))
    ):
        raise ValueError(
            "Use each supported judge variable at most once; expressions and includes are unsupported"
        )
    return value


def render_template(template, case, output):
    validate_template(template)
    values = {
        "Prompt": case["input"],
        "Expected Response": case.get("expected_response", ""),
        "Response": output[:32000],
    }
    result = VARIABLE.sub(
        lambda match: json.dumps(values[match.group(1)], ensure_ascii=False), template
    )
    if len(result.encode()) > 128 * 1024:
        raise ValueError("Rendered judge template exceeds 128 KiB")
    return result
