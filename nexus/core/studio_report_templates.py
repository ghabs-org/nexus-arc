"""Report-template lifecycle service for Nexus Studio.

Implements the read, instantiate, verify, package, and unpack lifecycle
for versioned, typed report templates.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Union
import zipfile

import yaml

SUPPORTED_PARAM_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
SUPPORTED_SECTION_FORMATS = {"markdown", "text", "table", "json"}
MAX_TEMPLATE_SIZE = 512 * 1024
MAX_SECTIONS = 50
MAX_PARAMETERS = 50

# Safe variable interpolation pattern: matches {{var_name}} or ${var_name}
VAR_PATTERN = re.compile(r"(?:\{\{([A-Za-z0-9_]+)\}\}|\$\{([A-Za-z0-9_]+)\})")


def _is_typed(value: Any, param_type: str) -> bool:
    if value is None:
        return False
    if param_type == "string":
        return isinstance(value, str)
    if param_type == "integer":
        return type(value) is int
    if param_type == "number":
        return (type(value) in (int, float)) and math.isfinite(value)
    if param_type == "boolean":
        return type(value) is bool
    if param_type == "array":
        return isinstance(value, list)
    if param_type == "object":
        return isinstance(value, dict)
    return False


def read_report_template(source: Union[str, Path, dict[str, Any]]) -> dict[str, Any]:
    """Read and validate structural schema of a report template from path, string, or dict."""
    if isinstance(source, (str, Path)):
        src_path = Path(source)
        if src_path.is_file():
            text = src_path.read_text(encoding="utf-8")
            if len(text.encode("utf-8")) > MAX_TEMPLATE_SIZE:
                raise ValueError(f"Report template file exceeds {MAX_TEMPLATE_SIZE} bytes")
            if src_path.suffix.lower() == ".json":
                data = json.loads(text)
            else:
                data = yaml.safe_load(text)
        else:
            # Source might be a raw string
            if len(str(source).encode("utf-8")) > MAX_TEMPLATE_SIZE:
                raise ValueError(f"Report template content exceeds {MAX_TEMPLATE_SIZE} bytes")
            try:
                data = json.loads(str(source))
            except Exception:
                data = yaml.safe_load(str(source))
    elif isinstance(source, dict):
        data = copy.deepcopy(source)
    else:
        raise TypeError(f"Expected path, str, or dict for report template, got {type(source).__name__}")

    if not isinstance(data, dict):
        raise ValueError("Report template must be a dictionary")

    allowed_keys = {"id", "name", "version", "description", "author", "tags", "parameters", "sections", "metadata"}
    extra_keys = set(data.keys()) - allowed_keys
    if extra_keys:
        raise ValueError(f"Report template contains unsupported fields: {', '.join(sorted(extra_keys))}")

    template_id = str(data.get("id") or "").strip()
    if not template_id or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", template_id):
        raise ValueError(f"Report template id '{template_id}' is invalid. Must be 1-64 alphanumeric/hyphen/underscore characters.")

    name = str(data.get("name") or "").strip()
    if not name or len(name) > 128:
        raise ValueError("Report template requires a non-empty name of at most 128 characters")

    version = data.get("version", 1)
    if not isinstance(version, (int, str)):
        raise ValueError("Report template version must be an integer or string")

    parameters = data.get("parameters", [])
    if not isinstance(parameters, list) or len(parameters) > MAX_PARAMETERS:
        raise ValueError(f"Parameters must be a list of at most {MAX_PARAMETERS} definitions")

    seen_params: Set[str] = set()
    norm_params = []
    for idx, p in enumerate(parameters):
        if not isinstance(p, dict):
            raise ValueError(f"Parameter at index {idx} must be a dictionary")
        p_name = str(p.get("name") or "").strip()
        if not p_name or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", p_name):
            raise ValueError(f"Parameter at index {idx} has invalid name '{p_name}'")
        if p_name in seen_params:
            raise ValueError(f"Duplicate parameter name '{p_name}' in template")
        seen_params.add(p_name)

        p_type = str(p.get("type", "string")).lower()
        if p_type not in SUPPORTED_PARAM_TYPES:
            raise ValueError(f"Parameter '{p_name}' has unsupported type '{p_type}'")

        required = bool(p.get("required", False))
        norm_param = {
            "name": p_name,
            "type": p_type,
            "required": required,
            "description": str(p.get("description", ""))[:1024],
        }
        if "default" in p:
            default_val = p["default"]
            if default_val is not None and not _is_typed(default_val, p_type):
                raise ValueError(f"Parameter '{p_name}' default value does not match declared type '{p_type}'")
            norm_param["default"] = default_val

        norm_params.append(norm_param)

    sections = data.get("sections", [])
    if not isinstance(sections, list) or not sections or len(sections) > MAX_SECTIONS:
        raise ValueError(f"Template requires 1 to {MAX_SECTIONS} sections")

    seen_sections: Set[str] = set()
    norm_sections = []
    for idx, s in enumerate(sections):
        if not isinstance(s, dict):
            raise ValueError(f"Section at index {idx} must be a dictionary")
        s_id = str(s.get("id") or "").strip()
        if not s_id or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", s_id):
            raise ValueError(f"Section at index {idx} has invalid id '{s_id}'")
        if s_id in seen_sections:
            raise ValueError(f"Duplicate section id '{s_id}' in template")
        seen_sections.add(s_id)

        s_title = str(s.get("title") or s_id).strip()
        s_format = str(s.get("format", "markdown")).lower()
        if s_format not in SUPPORTED_SECTION_FORMATS:
            raise ValueError(f"Section '{s_id}' has unsupported format '{s_format}'")

        template_content = str(s.get("content_template") or "")
        norm_sections.append({
            "id": s_id,
            "title": s_title,
            "format": s_format,
            "content_template": template_content,
            "required": bool(s.get("required", True)),
        })

    return {
        "id": template_id,
        "name": name,
        "version": version,
        "description": str(data.get("description", ""))[:2048],
        "author": str(data.get("author", ""))[:256],
        "tags": [str(t)[:64] for t in data.get("tags", []) if isinstance(t, str)],
        "parameters": norm_params,
        "sections": norm_sections,
        "metadata": data.get("metadata", {}),
    }


def verify_report_template(template_source: Union[str, Path, dict[str, Any]]) -> dict[str, Any]:
    """Perform static verification of report template variables, references, and types."""
    errors: list[str] = []
    warnings: list[str] = []
    referenced_vars: set[str] = set()

    try:
        template = read_report_template(template_source)
    except Exception as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "referenced_variables": [],
            "template": None,
        }

    declared_params = {p["name"]: p for p in template["parameters"]}

    for sec in template["sections"]:
        content = sec["content_template"]
        for match in VAR_PATTERN.finditer(content):
            var_name = match.group(1) or match.group(2)
            referenced_vars.add(var_name)
            if var_name not in declared_params:
                errors.append(
                    f"Section '{sec['id']}' references undeclared parameter '{var_name}'"
                )

    # Check for unused parameters
    for p_name in declared_params:
        if p_name not in referenced_vars:
            warnings.append(f"Parameter '{p_name}' is declared but never referenced in any section template")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "referenced_variables": sorted(referenced_vars),
        "template": template,
    }


def _render_value(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, indent=2)
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def instantiate_report_template(
    template_source: Union[str, Path, dict[str, Any]],
    inputs: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Validate inputs, resolve defaults, and render template sections safely."""
    verification = verify_report_template(template_source)
    if not verification["valid"]:
        raise ValueError(f"Cannot instantiate invalid report template: {'; '.join(verification['errors'])}")

    template = verification["template"]
    inputs = inputs or {}
    resolved_params: dict[str, Any] = {}

    for param in template["parameters"]:
        p_name = param["name"]
        p_type = param["type"]
        required = param["required"]

        if p_name in inputs:
            val = inputs[p_name]
            if not _is_typed(val, p_type):
                raise ValueError(
                    f"Input parameter '{p_name}' value '{val}' does not match expected type '{p_type}'"
                )
            resolved_params[p_name] = val
        elif required:
            raise ValueError(f"Missing required parameter '{p_name}' for report template '{template['id']}'")
        else:
            resolved_params[p_name] = param.get("default", None)

    rendered_sections = []
    doc_parts = [f"# {template['name']}\n"]

    for sec in template["sections"]:
        content = sec["content_template"]

        def replacer(match: re.Match) -> str:
            var_name = match.group(1) or match.group(2)
            return _render_value(resolved_params.get(var_name))

        rendered_text = VAR_PATTERN.sub(replacer, content)

        rendered_sections.append({
            "id": sec["id"],
            "title": sec["title"],
            "format": sec["format"],
            "rendered_content": rendered_text,
        })

        doc_parts.append(f"## {sec['title']}\n\n{rendered_text}\n")

    return {
        "id": template["id"],
        "name": template["name"],
        "version": template["version"],
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parameters": resolved_params,
        "sections": rendered_sections,
        "rendered_document": "\n".join(doc_parts).strip(),
        "metadata": template.get("metadata", {}),
    }


def package_report_template(
    template_source: Union[str, Path, dict[str, Any]],
    output_path: Union[str, Path],
) -> Path:
    """Package a verified report template into a distributable zip archive with digest manifest."""
    verification = verify_report_template(template_source)
    if not verification["valid"]:
        raise ValueError(f"Cannot package invalid report template: {'; '.join(verification['errors'])}")

    template = verification["template"]
    out = Path(output_path).resolve()
    if out.is_dir() or not out.suffix:
        out = out / f"{template['id']}-{template['version']}.report-template.zip"

    out.parent.mkdir(parents=True, exist_ok=True)

    template_json_bytes = json.dumps(template, indent=2).encode("utf-8")
    checksum = hashlib.sha256(template_json_bytes).hexdigest()

    manifest = {
        "package_format": "nexus-report-template/v1",
        "id": template["id"],
        "version": template["version"],
        "name": template["name"],
        "checksum_sha256": checksum,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("template.json", template_json_bytes)

    return out


def unpack_report_template(package_path: Union[str, Path], verify_checksum: bool = True) -> dict[str, Any]:
    """Unpack, verify checksum, and read report template from package archive."""
    pkg = Path(package_path).resolve()
    if not pkg.is_file():
        raise FileNotFoundError(f"Report template package not found: {pkg}")

    with zipfile.ZipFile(pkg, "r") as zf:
        names = zf.namelist()
        if "manifest.json" not in names or "template.json" not in names:
            raise ValueError(f"Package {pkg.name} is missing manifest.json or template.json")

        manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
        template_bytes = zf.read("template.json")

        if verify_checksum:
            expected_hash = manifest_data.get("checksum_sha256")
            actual_hash = hashlib.sha256(template_bytes).hexdigest()
            if expected_hash and actual_hash != expected_hash:
                raise ValueError(
                    f"Template checksum mismatch in {pkg.name}: expected {expected_hash}, got {actual_hash}"
                )

        template_data = json.loads(template_bytes.decode("utf-8"))

    return read_report_template(template_data)
