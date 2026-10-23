"""Toolset validation and packaging CLI for Nexus Studio.

Provides `validate_toolset`, `build_toolset`, and the `nexus toolset` CLI command.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Union
import zipfile

import yaml

SUPPORTED_TYPES = {"string", "number", "integer", "boolean", "array", "object"}


def _normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).strip("_")


def _normalize_version(version: str) -> str:
    return re.sub(r"[^\w\d.]+", "_", version).strip("_")


def _find_toolset_files(target_path: Path) -> tuple[Path, Path, Optional[Path]]:
    """Locate root directory, manifest file, and optional pyproject.toml."""
    if target_path.is_file():
        root_dir = target_path.parent
        manifest_file = target_path
    else:
        root_dir = target_path
        manifest_file = None
        for candidate in ("manifest.yaml", "manifest.yml", "toolset.yaml", "toolset.yml", "manifest.json"):
            p = root_dir / candidate
            if p.is_file():
                manifest_file = p
                break

    if not manifest_file or not manifest_file.is_file():
        raise FileNotFoundError(f"No toolset manifest found in {target_path}")

    pyproject_file = root_dir / "pyproject.toml"
    return root_dir, manifest_file, pyproject_file if pyproject_file.is_file() else None


def _load_manifest(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest at {path} must be a dictionary")
    return data


def _inspect_entrypoint_schema(root_dir: Path, entrypoint: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Inspect tool handler schema via import or AST inspection."""
    errors: list[str] = []
    schema_fields: list[dict[str, Any]] = []

    if ":" not in entrypoint:
        errors.append(f"Invalid entrypoint format '{entrypoint}'. Expected 'module:function_or_class'")
        return schema_fields, errors

    mod_name, func_name = entrypoint.split(":", 1)

    # Try dynamically importing by adding root_dir to sys.path
    root_str = str(root_dir.resolve())
    added_to_path = False
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        added_to_path = True

    try:
        try:
            mod = importlib.import_module(mod_name)
            handler = getattr(mod, func_name, None)
            if handler is None:
                errors.append(f"Function/class '{func_name}' not found in module '{mod_name}'")
            else:
                raw_schema = getattr(handler, "__config_schema__", [])
                for f in raw_schema:
                    if hasattr(f, "describe"):
                        schema_fields.append(f.describe())
                    elif isinstance(f, dict):
                        schema_fields.append(f)
                    elif hasattr(f, "key"):
                        schema_fields.append({
                            "key": getattr(f, "key"),
                            "description": getattr(f, "description", ""),
                            "default": getattr(f, "default", ""),
                            "type": getattr(f, "type", "string"),
                            "required": getattr(f, "required", False),
                        })
        except Exception as import_err:
            # Fall back to AST parsing of the python file if direct import fails
            mod_file = root_dir / (mod_name.replace(".", "/") + ".py")
            if not mod_file.is_file():
                errors.append(f"Could not load module '{mod_name}': {import_err}")
            else:
                try:
                    tree = ast.parse(mod_file.read_text(encoding="utf-8"), filename=str(mod_file))
                    found = False
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            if node.name == func_name:
                                found = True
                                break
                    if not found:
                        errors.append(f"Function/class '{func_name}' not defined in {mod_file.name}")
                except Exception as ast_err:
                    errors.append(f"Failed to parse '{mod_file.name}': {ast_err}")
    finally:
        if added_to_path and root_str in sys.path:
            sys.path.remove(root_str)

    return schema_fields, errors


def validate_toolset(path: Union[str, Path]) -> dict[str, Any]:
    """Validate a toolset package or manifest against schema and entrypoint contracts."""
    target_path = Path(path).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    try:
        root_dir, manifest_file, pyproject_file = _find_toolset_files(target_path)
    except Exception as exc:
        return {
            "valid": False,
            "toolset_id": "",
            "version": "",
            "tools": [],
            "errors": [str(exc)],
            "warnings": [],
        }

    try:
        manifest = _load_manifest(manifest_file)
    except Exception as exc:
        return {
            "valid": False,
            "toolset_id": "",
            "version": "",
            "tools": [],
            "errors": [f"Failed to read manifest: {exc}"],
            "warnings": [],
        }

    toolset_id = str(manifest.get("id") or manifest.get("name") or "").strip()
    if not toolset_id:
        errors.append("Manifest missing required 'id' or 'name'")
    elif not re.fullmatch(r"[A-Za-z0-9_.-]+", toolset_id):
        errors.append(f"Invalid toolset id '{toolset_id}'. Must be alphanumeric with hyphens, underscores, or dots.")

    version = str(manifest.get("version") or "").strip()
    if not version:
        errors.append("Manifest missing required 'version'")

    tools_raw = manifest.get("tools")
    if not isinstance(tools_raw, list) or not tools_raw:
        errors.append("Manifest must contain a non-empty 'tools' list")
        validated_tools = []
    else:
        validated_tools = []
        for idx, t in enumerate(tools_raw):
            if not isinstance(t, dict):
                errors.append(f"Tool at index {idx} must be a dictionary")
                continue
            tid = str(t.get("id") or t.get("name") or "").strip()
            if not tid:
                errors.append(f"Tool at index {idx} missing required 'id' or 'name'")
            entrypoint = str(t.get("entrypoint") or "").strip()
            if not entrypoint:
                errors.append(f"Tool '{tid or idx}' missing required 'entrypoint'")
                schema_fields = []
            else:
                schema_fields, ep_errors = _inspect_entrypoint_schema(root_dir, entrypoint)
                errors.extend(ep_errors)
                for sf in schema_fields:
                    f_type = sf.get("type", "string")
                    if f_type not in SUPPORTED_TYPES:
                        errors.append(f"Tool '{tid}' field '{sf.get('key')}' has unsupported type '{f_type}'")

            validated_tools.append({
                "id": tid,
                "name": t.get("name") or tid,
                "description": t.get("description", ""),
                "entrypoint": entrypoint,
                "config_schema": schema_fields,
            })

    if pyproject_file and pyproject_file.is_file():
        pyproject_text = pyproject_file.read_text(encoding="utf-8")
        if "name" in pyproject_text and toolset_id not in pyproject_text:
            warnings.append(f"pyproject.toml does not appear to reference toolset id '{toolset_id}'")

    return {
        "valid": len(errors) == 0,
        "toolset_id": toolset_id,
        "version": version,
        "tools": validated_tools,
        "errors": errors,
        "warnings": warnings,
        "root_dir": str(root_dir),
        "manifest_path": str(manifest_file),
    }


def build_toolset(path: Union[str, Path], output_dir: Optional[Union[str, Path]] = None) -> Path:
    """Build a PEP 427 wheel package (.whl) for a validated toolset."""
    summary = validate_toolset(path)
    if not summary["valid"]:
        raise ValueError(f"Toolset validation failed: {'; '.join(summary['errors'])}")

    root_dir = Path(summary["root_dir"])
    toolset_id = _normalize_dist_name(summary["toolset_id"])
    version = _normalize_version(summary["version"])

    out_path = Path(output_dir).resolve() if output_dir else root_dir / "dist"
    out_path.mkdir(parents=True, exist_ok=True)

    dist_info = f"{toolset_id}-{version}.dist-info"
    wheel_filename = f"{toolset_id}-{version}-py3-none-any.whl"
    wheel_path = out_path / wheel_filename

    metadata_content = (
        "Metadata-Version: 2.1\n"
        f"Name: {toolset_id}\n"
        f"Version: {version}\n"
        "Summary: Nexus Studio Toolset Package\n"
    )

    wheel_meta_content = (
        "Wheel-Version: 1.0\n"
        "Generator: nexus-toolset-builder (1.0)\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    )

    records: list[str] = []

    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Include python source files and manifest
        for file_path in root_dir.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(root_dir)
            rel_str = str(rel)
            if (
                rel_str.startswith("dist")
                or rel_str.startswith(".")
                or "__pycache__" in rel_str
                or rel_str.endswith(".pyc")
                or rel_str.endswith(".whl")
            ):
                continue

            data = file_path.read_bytes()
            zf.writestr(rel_str, data)
            sha = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("latin1").rstrip("=")
            records.append(f"{rel_str},sha256={sha},{len(data)}")

        # Add dist-info files
        meta_bytes = metadata_content.encode("utf-8")
        zf.writestr(f"{dist_info}/METADATA", meta_bytes)
        sha_m = base64.urlsafe_b64encode(hashlib.sha256(meta_bytes).digest()).decode("latin1").rstrip("=")
        records.append(f"{dist_info}/METADATA,sha256={sha_m},{len(meta_bytes)}")

        wheel_meta_bytes = wheel_meta_content.encode("utf-8")
        zf.writestr(f"{dist_info}/WHEEL", wheel_meta_bytes)
        sha_w = base64.urlsafe_b64encode(hashlib.sha256(wheel_meta_bytes).digest()).decode("latin1").rstrip("=")
        records.append(f"{dist_info}/WHEEL,sha256={sha_w},{len(wheel_meta_bytes)}")

        record_content = "\n".join(records) + f"\n{dist_info}/RECORD,,\n"
        zf.writestr(f"{dist_info}/RECORD", record_content.encode("utf-8"))

    return wheel_path


def toolset_command(args: argparse.Namespace) -> int:
    """CLI dispatcher for `nexus toolset` subcommands."""
    subcommand = getattr(args, "toolset_subcommand", "validate")
    target_path = getattr(args, "path", ".")

    if subcommand == "validate":
        summary = validate_toolset(target_path)
        if getattr(args, "json_output", False):
            print(json.dumps(summary, indent=2))
        else:
            status = "VALID" if summary["valid"] else "INVALID"
            print(f"Toolset '{summary['toolset_id']}' (v{summary['version']}): {status}")
            if summary["tools"]:
                print(f"Tools ({len(summary['tools'])}):")
                for t in summary["tools"]:
                    schemas = len(t.get("config_schema", []))
                    print(f"  - {t['id']} [{t['entrypoint']}] ({schemas} config fields)")
            if summary["warnings"]:
                print(f"Warnings ({len(summary['warnings'])}):")
                for w in summary["warnings"]:
                    print(f"  * {w}")
            if summary["errors"]:
                print(f"Errors ({len(summary['errors'])}):")
                for e in summary["errors"]:
                    print(f"  ! {e}")
        return 0 if summary["valid"] else 1

    elif subcommand == "build":
        try:
            wheel_path = build_toolset(target_path, getattr(args, "output_dir", None))
            if getattr(args, "json_output", False):
                print(json.dumps({"success": True, "wheel": str(wheel_path)}))
            else:
                print(f"Built toolset wheel: {wheel_path}")
            return 0
        except Exception as exc:
            if getattr(args, "json_output", False):
                print(json.dumps({"success": False, "error": str(exc)}))
            else:
                print(f"Build failed: {exc}", file=sys.stderr)
            return 1

    print(f"Unknown toolset subcommand: {subcommand}", file=sys.stderr)
    return 1


def add_parser(subparsers: Any) -> Any:
    """Register `toolset` command in CLI parser."""
    parser = subparsers.add_parser("toolset", help="Validate and build Nexus toolsets")
    toolset_subs = parser.add_subparsers(dest="toolset_subcommand")

    val_p = toolset_subs.add_parser("validate", help="Validate toolset manifest and schemas")
    val_p.add_argument("path", nargs="?", default=".", help="Path to toolset directory or manifest file")
    val_p.add_argument("--json", action="store_true", dest="json_output", help="Output JSON result")

    build_p = toolset_subs.add_parser("build", help="Build toolset wheel archive")
    build_p.add_argument("path", nargs="?", default=".", help="Path to toolset directory")
    build_p.add_argument("--output-dir", help="Target directory for generated wheel (default: ./dist)")
    build_p.add_argument("--json", action="store_true", dest="json_output", help="Output JSON result")

    return parser
