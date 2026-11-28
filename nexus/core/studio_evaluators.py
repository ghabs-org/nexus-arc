"""Reusable judge definitions; copying a definition never invokes its provider."""

from nexus.core.studio_evaluations import validate_judge


def validate_evaluator(data):
    if (
        not isinstance(data, dict)
        or set(data)
        - {"kind", "version", "type", "provider", "model", "rubric", "threshold", "choices", "prompt_template"}
        or data.get("kind", "evaluator") != "evaluator"
        or type(data.get("version", 1)) is not int
        or data.get("version", 1) != 1
        or data.get("type", "llm_judge") != "llm_judge"
    ):
        raise ValueError("Use a version 1 llm_judge evaluator with supported fields")
    judge = validate_judge(
        {key: value for key, value in data.items() if key not in {"kind", "version", "type"}}
    )
    return {"kind": "evaluator", "version": 1, "type": "llm_judge", **judge}
