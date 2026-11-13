"""Bounded external data templates, translated without expression execution."""

import json
import re

from nexus.core.studio_workflow_validation import REFERENCE

LANGUAGE = "nexus-template/v1"
TOKEN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


INDEX = re.compile(r"\[([0-9]|[1-9][0-9]{1,3})\]")


def normalize_indices(value):
    if "[" not in value and "]" not in value:
        return value
    cleaned = INDEX.sub(r".\1", value)
    if "[" in cleaned or "]" in cleaned:
        raise ValueError("Invalid external data reference")
    return cleaned


def source_ref(value):
    value = normalize_indices(value)
    if value == "workflow.input":
        return "input"
    if value.startswith("workflow.input."):
        result = "input." + value[len("workflow.input.") :]
    elif re.fullmatch(r"[a-z][a-z0-9_-]*\.output(?:\.[\w-]+)*", value):
        identity, _, rest = value.partition(".output")
        result = "nodes." + identity + rest
    else:
        raise ValueError("Only workflow input and upstream node output references are supported")
    if len(result) > 256 or not REFERENCE.fullmatch(result):
        raise ValueError("Invalid external data reference")
    return result


def import_value(value):
    if isinstance(value, dict):
        # Native operator-looking literals must not acquire new meaning on import.
        if set(value) in ({"ref"}, {"template"}):
            raise ValueError("Literal objects using reserved ref/template keys need manual mapping")
        return {key: import_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [import_value(item) for item in value]
    if isinstance(value, str) and "${" in value:
        raise ValueError("Environment substitution is not expanded during external conversion")
    if not isinstance(value, str) or not any(t in value for t in ("{{", "}}")):
        return value
    matches = list(TOKEN.finditer(value))
    remainder = TOKEN.sub("", value)
    if not matches or "{{" in remainder or "}}" in remainder:
        raise ValueError("Malformed external data template")
    parts, offset = [], 0
    for match in matches:
        if match.start() > offset:
            parts.append(value[offset : match.start()])
        parts.append({"ref": source_ref(match[1].strip())})
        offset = match.end()
    if offset < len(value):
        parts.append(value[offset:])
    if len(parts) == 1 and isinstance(parts[0], dict):
        return parts[0]
    if len(parts) > 128:
        raise ValueError("A mapped template supports at most 128 parts")
    return {"template": parts}


def target_ref(path):
    if path == "input":
        return "workflow.input"
    if path.startswith("input."):
        return "workflow.input." + path[6:]
    if path.startswith("nodes.") and len(path.split(".")) >= 2:
        _, identity, *rest = path.split(".")
        return identity + ".output" + ("." + ".".join(rest) if rest else "")
    raise ValueError("Reference cannot be represented as an external data template")


def export_value(value, language=None):
    if isinstance(value, dict):
        if set(value) == {"ref"}:
            return "{{" + target_ref(value["ref"]) + "}}"
        if set(value) == {"template"} and language == LANGUAGE:
            return "".join(
                item if isinstance(item, str) else export_value(item, language)
                for item in value["template"]
            )
        return {key: export_value(item, language) for key, item in value.items()}
    if isinstance(value, list):
        return [export_value(item, language) for item in value]
    if isinstance(value, str) and ("{{" in value or "}}" in value):
        raise ValueError("Literal template delimiters cannot be exported without changing behavior")
    return value


def template_text(parts, resolve_part):
    if not isinstance(parts, list) or not 1 <= len(parts) <= 128:
        raise ValueError("Mapped templates require 1–128 text/reference parts")
    result = []
    for part in parts:
        if isinstance(part, str):
            result.append(part)
        elif isinstance(part, dict) and set(part) == {"ref"}:
            value = resolve_part(part)
            result.append(
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            )
        else:
            raise ValueError("Mapped templates accept text and data references only")
    text = "".join(result)
    if len(text.encode()) > 65536:
        raise ValueError("Resolved mapped template exceeds 64 KiB")
    return text
