"""Bidirectional translation for portable declarative manifest files (kind: manifest).

Supports grouped bundle resource references, target environments (desktop, personal-vm),
variable interpolation (${VAR_NAME}), and conversion to/from native Studio configuration releases.
"""

import copy
import os
import re
from typing import Any

import yaml
from yaml.events import AliasEvent

SUPPORTED_TARGET_ENVIRONMENTS = ("desktop", "personal-vm")

MANIFEST_RESOURCE_KINDS = (
    "connectors",
    "toolsets",
    "skills",
    "agents",
    "workflows",
    "entrypoints",
    "datasets",
    "evaluators",
    "experiments",
    "models",
)


class SafeManifestLoader(yaml.SafeLoader):
    """YAML safe loader with alias and recursion traps for manifests."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise ValueError("YAML aliases are not supported in portable manifests")
        self.manifest_nodes = getattr(self, "manifest_nodes", 0) + 1
        self.manifest_depth = getattr(self, "manifest_depth", 0) + 1
        try:
            if self.manifest_nodes > 20000 or self.manifest_depth > 50:
                raise ValueError("Manifest nesting or node limit exceeded")
            return super().compose_node(parent, index)
        finally:
            self.manifest_depth -= 1

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[str, Any]:
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Manifest keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


class StudioManifestTranslator:
    """Translates portable declarative manifests to/from Studio native releases."""

    def __init__(self, configuration: Any):
        self.configuration = configuration
        self.studio = configuration.studio

    def interpolate(
        self,
        value: Any,
        variables: dict[str, str],
        path: str,
        diagnostics: list[dict[str, str]],
    ) -> Any:
        """Recursively interpolate ${VAR_NAME} and ${VAR_NAME:-default}."""
        if isinstance(value, str):
            pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

            def repl(match: re.Match) -> str:
                var_name = match.group(1)
                default_val = match.group(2)
                if var_name in variables:
                    return str(variables[var_name])
                if var_name in os.environ:
                    return os.environ[var_name]
                if default_val is not None:
                    return default_val
                diagnostics.append(
                    {
                        "path": path,
                        "code": "missing_variable",
                        "message": f"Missing environment variable: {var_name}",
                    }
                )
                return match.group(0)

            return pattern.sub(repl, value)
        elif isinstance(value, dict):
            return {
                k: self.interpolate(v, variables, f"{path}/{k}", diagnostics)
                for k, v in value.items()
            }
        elif isinstance(value, list):
            return [
                self.interpolate(item, variables, f"{path}/{idx}", diagnostics)
                for idx, item in enumerate(value)
            ]
        return value

    def parse_manifest(self, text: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """Safely parse manifest text into documents."""
        diagnostics: list[dict[str, str]] = []
        if len(text.encode("utf-8")) > 1024 * 1024:
            diagnostics.append(
                {
                    "path": "/",
                    "code": "size_exceeded",
                    "message": "Manifest document exceeds one MiB limit",
                }
            )
            return [], diagnostics
        try:
            docs = list(yaml.load_all(text, Loader=SafeManifestLoader))
            return [d for d in docs if isinstance(d, dict)], diagnostics
        except Exception as exc:
            diagnostics.append(
                {
                    "path": "/",
                    "code": "syntax_error",
                    "message": f"Failed to parse manifest YAML: {exc}",
                }
            )
            return [], diagnostics

    def convert(
        self, data: dict[str, Any], actor: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Convert a portable declarative manifest into a native Studio configuration release."""
        self.configuration.authorize(actor)
        diagnostics: list[dict[str, str]] = []

        manifest_doc: dict[str, Any] | None = None
        accompanying_docs: list[dict[str, Any]] = []

        if "text" in data and isinstance(data["text"], str):
            docs, parse_diags = self.parse_manifest(data["text"])
            diagnostics.extend(parse_diags)
            if parse_diags:
                return {"valid": False, "diagnostics": diagnostics, "document": None}
            for d in docs:
                if d.get("kind") == "manifest":
                    if manifest_doc is None:
                        manifest_doc = d
                    else:
                        diagnostics.append(
                            {
                                "path": "/",
                                "code": "multiple_manifests",
                                "message": "Multiple manifest documents found in stream",
                            }
                        )
                else:
                    accompanying_docs.append(d)
        elif "manifest" in data and isinstance(data["manifest"], dict):
            manifest_doc = data["manifest"]
            if isinstance(data.get("documents"), list):
                accompanying_docs = [
                    d for d in data["documents"] if isinstance(d, dict) and d.get("kind") != "manifest"
                ]

        if not manifest_doc or manifest_doc.get("kind") != "manifest":
            diagnostics.append(
                {
                    "path": "/kind",
                    "code": "invalid_manifest",
                    "message": "Expected a document with kind: manifest",
                }
            )
            return {"valid": False, "diagnostics": diagnostics, "document": None}

        # Validate target environment
        target = manifest_doc.get("target")
        target_name: str | None = None
        if isinstance(target, dict):
            target_name = target.get("name")
        elif isinstance(target, str):
            target_name = target
        elif "targets" in manifest_doc and isinstance(manifest_doc["targets"], list):
            target_name = manifest_doc["targets"][0] if manifest_doc["targets"] else None

        req_env = data.get("environment")
        if req_env:
            if req_env not in SUPPORTED_TARGET_ENVIRONMENTS:
                diagnostics.append(
                    {
                        "path": "/target",
                        "code": "unsupported_environment",
                        "message": f"Target environment must be one of {SUPPORTED_TARGET_ENVIRONMENTS}",
                    }
                )
            elif target_name and target_name != req_env:
                diagnostics.append(
                    {
                        "path": "/target/name",
                        "code": "target_mismatch",
                        "message": f"Manifest target '{target_name}' does not match requested environment '{req_env}'",
                    }
                )
            target_name = req_env
        elif target_name:
            if target_name not in SUPPORTED_TARGET_ENVIRONMENTS:
                diagnostics.append(
                    {
                        "path": "/target/name",
                        "code": "unsupported_target",
                        "message": f"Manifest target must be one of {SUPPORTED_TARGET_ENVIRONMENTS}",
                    }
                )

        # Build variable dictionary
        variables: dict[str, str] = {}
        if isinstance(manifest_doc.get("variables"), dict):
            variables.update({str(k): str(v) for k, v in manifest_doc["variables"].items()})
        if isinstance(data.get("variables"), dict):
            variables.update({str(k): str(v) for k, v in data["variables"].items()})

        # Process resources
        resources_map = manifest_doc.get("resources") or manifest_doc.get("spec", {}).get("resources")
        if not isinstance(resources_map, dict):
            diagnostics.append(
                {
                    "path": "/resources",
                    "code": "invalid_resources",
                    "message": "Manifest resources must be a dictionary grouped by kind",
                }
            )
            return {"valid": False, "diagnostics": diagnostics, "document": None}

        # Build index of accompanying docs
        accompanying_by_kind_id: dict[tuple[str, str], dict[str, Any]] = {}
        for doc in accompanying_docs:
            d_kind = str(doc.get("kind", "")).lower()
            d_name = doc.get("name") or doc.get("id") or doc.get("metadata", {}).get("name")
            if d_kind and d_name:
                accompanying_by_kind_id[(d_kind, str(d_name))] = doc
                accompanying_by_kind_id[(d_kind + "s", str(d_name))] = doc

        native_definitions: list[dict[str, Any]] = []
        native_resources: list[dict[str, Any]] = []

        for group_kind, items in resources_map.items():
            if not isinstance(items, list):
                diagnostics.append(
                    {
                        "path": f"/resources/{group_kind}",
                        "code": "invalid_group",
                        "message": f"Resources for '{group_kind}' must be a list",
                    }
                )
                continue

            for idx, item in enumerate(items):
                item_path = f"/resources/{group_kind}/{idx}"
                resource_id = ""
                resource_data: dict[str, Any] | None = None

                if isinstance(item, str):
                    resource_id = item
                    # Look up in accompanying docs first
                    matched = accompanying_by_kind_id.get(
                        (group_kind, resource_id)
                    ) or accompanying_by_kind_id.get((group_kind.rstrip("s"), resource_id))
                    if matched:
                        resource_data = matched
                    else:
                        # Look up in current Studio catalog/definitions
                        if group_kind in {"agents", "workflows"}:
                            try:
                                existing_def = self.studio.definition(group_kind, resource_id)
                                if existing_def:
                                    native_definitions.append(
                                        {
                                            "collection": group_kind,
                                            "id": resource_id,
                                            "document": existing_def.get("document", existing_def),
                                        }
                                    )
                                    continue
                            except (FileNotFoundError, ValueError):
                                pass
                        else:
                            cat_kind = group_kind
                            if self.studio.catalog._path(cat_kind, resource_id).exists():
                                cat_item = self.studio.catalog.get(cat_kind, resource_id)
                                native_resources.append(
                                    {
                                        "kind": cat_kind,
                                        "id": resource_id,
                                        "name": cat_item.get("name", resource_id),
                                        "description": cat_item.get("description", ""),
                                        "data": cat_item.get("data", {}),
                                    }
                                )
                                continue
                        diagnostics.append(
                            {
                                "path": item_path,
                                "code": "unresolved_reference",
                                "message": f"Resource reference '{resource_id}' not found for kind '{group_kind}'",
                            }
                        )
                        continue
                elif isinstance(item, dict):
                    resource_id = str(
                        item.get("id") or item.get("name") or item.get("metadata", {}).get("name") or ""
                    )
                    resource_data = item
                    if not resource_id:
                        diagnostics.append(
                            {
                                "path": item_path,
                                "code": "missing_identity",
                                "message": "Inline resource requires an id or name",
                            }
                        )
                        continue
                else:
                    diagnostics.append(
                        {
                            "path": item_path,
                            "code": "invalid_item",
                            "message": "Resource item must be a string identifier or object specification",
                        }
                    )
                    continue

                if resource_data:
                    # Target environment filtering on inline items
                    item_target = resource_data.get("target") or resource_data.get("environment")
                    if isinstance(item_target, str) and target_name and item_target != target_name:
                        continue
                    if isinstance(item_target, list) and target_name and target_name not in item_target:
                        continue

                    if group_kind in {"agents", "workflows"}:
                        native_definitions.append(
                            {
                                "collection": group_kind,
                                "id": resource_id,
                                "document": resource_data.get("document", resource_data),
                            }
                        )
                    else:
                        native_resources.append(
                            {
                                "kind": group_kind,
                                "id": resource_id,
                                "name": resource_data.get("name", resource_id),
                                "description": resource_data.get("description", ""),
                                "data": resource_data.get("data", resource_data.get("spec", {})),
                            }
                        )

        # Variable interpolation across definitions and resources
        interp_diags: list[dict[str, str]] = []
        interpolated_definitions = self.interpolate(
            native_definitions, variables, "/definitions", interp_diags
        )
        interpolated_resources = self.interpolate(
            native_resources, variables, "/resources", interp_diags
        )
        diagnostics.extend(interp_diags)

        if diagnostics:
            return {
                "valid": False,
                "diagnostics": diagnostics,
                "document": None,
                "manifest": manifest_doc,
                "environment": target_name or "desktop",
            }

        native_document = {
            "format": "nexus-studio-config/v1",
            "definitions": interpolated_definitions,
            "resources": interpolated_resources,
        }

        return {
            "valid": True,
            "diagnostics": [],
            "document": native_document,
            "manifest": manifest_doc,
            "environment": target_name or "desktop",
        }

    def export(
        self, data: dict[str, Any] | None = None, actor: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Export current workspace or native release document into a portable declarative manifest."""
        self.configuration.authorize(actor)
        data = data or {}

        environment = data.get("environment", "desktop")
        if environment not in SUPPORTED_TARGET_ENVIRONMENTS:
            raise ValueError(f"Target environment must be one of {SUPPORTED_TARGET_ENVIRONMENTS}")

        native_document = data.get("document")
        if not native_document:
            native_document = self.configuration.export(actor)

        if (
            not isinstance(native_document, dict)
            or native_document.get("format") != "nexus-studio-config/v1"
        ):
            raise ValueError("Expected a nexus-studio-config/v1 configuration release document")

        manifest_name = data.get("name") or "studio-manifest"
        description = data.get("description") or f"Exported portable manifest for {environment}"

        resources_group: dict[str, list[Any]] = {k: [] for k in MANIFEST_RESOURCE_KINDS}

        # Map definitions
        for item in native_document.get("definitions", []):
            coll = item.get("collection")
            if coll in resources_group:
                resources_group[coll].append(item["id"])

        # Map catalog resources
        for item in native_document.get("resources", []):
            kind = item.get("kind")
            if kind in resources_group:
                resources_group[kind].append(item["id"])
            elif kind:
                if kind not in resources_group:
                    resources_group[kind] = []
                resources_group[kind].append(item["id"])

        # Remove empty groups
        filtered_resources = {k: v for k, v in resources_group.items() if v}

        manifest = {
            "kind": "manifest",
            "name": manifest_name,
            "description": description,
            "target": {"name": environment},
        }

        if data.get("variables"):
            manifest["variables"] = copy.deepcopy(data["variables"])

        manifest["resources"] = filtered_resources

        yaml_content = yaml.dump(
            manifest, sort_keys=False, default_flow_style=False, allow_unicode=True
        )

        return {
            "valid": True,
            "diagnostics": [],
            "manifest": manifest,
            "yaml": yaml_content,
            "environment": environment,
        }
