"""Versioned external mapping values, with explicit missing/omitted semantics."""

import copy
import json

from nexus.core.studio_interop_mappings import TOKEN, source_ref
from nexus.core.studio_workflow_validation import REFERENCE

LANGUAGE = "interop-template/v1"
DROP = object()


class MissingPortableValueError(ValueError):
    def __init__(self, *, stale=False, omitted=False):
        super().__init__("External reference is unresolved")
        self.stale, self.omitted = stale, omitted


def import_value(value, reference=None):
    reference = reference or (
        lambda name: (source_ref(name), "input" if name.startswith("workflow.input") else "node")
    )
    if isinstance(value, dict):
        if set(value) & {"ref", "interop_ref", "interop_text"} and len(value) == 1:
            raise ValueError("Reserved mapping object requires manual adaptation")
        if set(value) in ({"coalesce"}, {"concat"}):
            key = next(iter(value))
            items = value[key]
            if not isinstance(items, list) or not 1 <= len(items) <= 128:
                raise ValueError("External mapping operators require 1–128 values")
            return {key: [import_value(item, reference) for item in items]}
        return {key: import_value(item, reference) for key, item in value.items()}
    if isinstance(value, list):
        return [import_value(item, reference) for item in value]
    if not isinstance(value, str):
        return value
    if "${" in value:
        raise ValueError("Environment expansion requires manual adaptation")
    matches = list(TOKEN.finditer(value))
    remainder = TOKEN.sub("", value)
    if "{{" in remainder or "}}" in remainder:
        raise ValueError("Invalid external template")
    if not matches:
        return value
    parts, start = [], 0
    for match in matches:
        if match.start() > start:
            parts.append(value[start : match.start()])
        path, category = reference(match[1].strip())
        parts.append(
            {"interop_ref": {"path": {"ref": path}, "source": match[0], "category": category}}
        )
        start = match.end()
    if start < len(value):
        parts.append(value[start:])
    return parts[0] if len(parts) == 1 and isinstance(parts[0], dict) else {"interop_text": parts}


def validate_value(value, depth=0, budget=None):
    budget = budget if budget is not None else [20000]
    budget[0] -= 1
    if depth > 50 or budget[0] < 0:
        raise ValueError("External mapping complexity limit exceeded")
    if isinstance(value, dict):
        if set(value) == {"interop_ref"}:
            ref = value["interop_ref"]
            if (
                not isinstance(ref, dict)
                or set(ref) != {"path", "source", "category"}
                or not isinstance(ref["path"], dict)
                or set(ref["path"]) != {"ref"}
                or not isinstance(ref["path"]["ref"], str)
                or len(ref["path"]["ref"]) > 256
                or not REFERENCE.fullmatch(ref["path"]["ref"])
                or not isinstance(ref["source"], str)
                or len(ref["source"]) > 1000
                or ref["category"] not in ("input", "node", "previous")
            ):
                raise ValueError("Invalid external reference operator")
            return
        if set(value) in ({"interop_text"}, {"coalesce"}, {"concat"}):
            parts = next(iter(value.values()))
            if not isinstance(parts, list) or not 1 <= len(parts) <= 128:
                raise ValueError("External mapping operators require 1–128 values")
            if "interop_text" in value and any(
                not isinstance(p, str) and not (isinstance(p, dict) and set(p) == {"interop_ref"})
                for p in parts
            ):
                raise ValueError("External text accepts literal and reference parts only")
        for item in value.values():
            validate_value(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            validate_value(item, depth + 1, budget)


def reference_value(ref, context):
    path = ref["path"]["ref"].split(".")
    current = context
    for index, key in enumerate(path):
        try:
            current = current[int(key)] if isinstance(current, list) else current[key]
        except (KeyError, TypeError, IndexError, ValueError):
            # An absent/skipped node is nil. A missing field on a completed node is stale.
            absent_node = path[0] in {"nodes", "interop_nodes"} and index <= 1
            absent_previous = path[0] == "previous" and context.get("previous") is None
            raise MissingPortableValueError(
                stale=ref["category"] != "input" and not absent_node and not absent_previous,
                omitted=ref["category"] == "input" and isinstance(current, dict),
            ) from None
    return copy.deepcopy(current)


def text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def resolve_value(value, context):
    if isinstance(value, list):
        result = [resolve_value(item, context) for item in value]
        return [None if item is DROP else item for item in result]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    if set(value) == {"interop_ref"}:
        ref = value["interop_ref"]
        try:
            return reference_value(ref, context)
        except MissingPortableValueError as missing:
            if missing.stale:
                if context.get("_interop_output_mapping"):
                    return ref["source"]
                raise
            if missing.omitted and context.get("_interop_input_mapping"):
                return DROP
            return None
    if set(value) == {"interop_text"}:
        parts, size = [], 0
        for part in value["interop_text"]:
            if isinstance(part, str):
                parts.append(part)
            else:
                ref = part["interop_ref"]
                try:
                    item = reference_value(ref, context)
                except MissingPortableValueError as missing:
                    if missing.stale and not context.get("_interop_output_mapping"):
                        raise
                    parts.append(ref["source"])
                else:
                    parts.append(text(item))
            size += len(parts[-1].encode())
            if size > 65536:
                raise ValueError("External mapped text exceeds 64 KiB")
        result = "".join(parts)
        if len(result.encode()) > 65536:
            raise ValueError("External mapped text exceeds 64 KiB")
        return result
    if set(value) in ({"coalesce"}, {"concat"}):
        operator = next(iter(value))
        # Missing fields of existing nodes remain loud even inside an operator.
        local = {**context, "_interop_input_mapping": False, "_interop_output_mapping": False}
        parts, size = [], 0
        for part in value[operator]:
            item = resolve_value(part, local)
            if operator == "coalesce" and item is not None:
                return item
            if operator == "concat" and item is not None:
                rendered = text(item)
                size += len(rendered.encode())
                if size > 65536:
                    raise ValueError("External mapped text exceeds 64 KiB")
                parts.append(rendered)
        result = None if operator == "coalesce" else "".join(parts)
        if isinstance(result, str) and len(result.encode()) > 65536:
            raise ValueError("External mapped text exceeds 64 KiB")
        return result
    # Regular Nexus references inside the compiler's projection graph stay strict.
    if set(value) == {"ref"}:
        from nexus.core.studio_workflow_v2 import resolve

        return resolve(value, {**context, "_mapping_language": None})
    result = {}
    for key, item in value.items():
        resolved = resolve_value(item, context)
        if resolved is not DROP:
            result[key] = resolved
    return result
