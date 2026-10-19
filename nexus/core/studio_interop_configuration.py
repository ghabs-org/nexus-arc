"""Non-executing external configuration bridge through native reviewed releases.

Only semantically mapped fields are accepted. Every unsupported field is reported;
failed conversion never exposes a partial executable document.
"""

import copy
import json
import re

import yaml
from yaml.events import AliasEvent

from nexus.core.studio_interop_mappings import LANGUAGE, export_value, import_value


class SAMLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            raise ValueError("YAML aliases are not supported in portable configuration")
        self.interop_nodes = getattr(self, "interop_nodes", 0) + 1
        self.interop_depth = getattr(self, "interop_depth", 0) + 1
        try:
            if self.interop_nodes > 20000 or self.interop_depth > 50:
                raise ValueError("Configuration nesting or node limit exceeded")
            return super().compose_node(parent, index)
        finally:
            self.interop_depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Configuration keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def identity(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 200:
        raise ValueError("Resource name must contain 1–200 characters")
    result = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not result or len(result) > 96:
        raise ValueError("Resource name requires a portable Nexus identifier")
    return result


def duration(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?(?:ms|s|m|h)", value):
        raise ValueError("Use a single duration unit: ms, s, m or h")
    match = re.fullmatch(r"([\d.]+)(ms|s|m|h)", value)
    seconds = float(match[1]) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match[2]]
    if not 1 <= seconds <= 3600:
        raise ValueError("Node timeout must be between 1 and 3600 seconds")
    return int(seconds) if seconds.is_integer() else seconds


class StudioSAMConfiguration:
    def __init__(self, configuration):
        self.configuration = configuration
        self.studio = configuration.studio
        self.diagnostics = []
        self.bindings = {}
        self.names = {}

    def issue(self, path, code, message):
        # Values and parser source excerpts can contain credentials. Never return them.
        self.diagnostics.append({"path": path, "code": code, "message": message})

    def fields(self, value, allowed, path):
        if not isinstance(value, dict):
            self.issue(path, "invalid_type", "Expected an object")
            return {}
        for key in value:
            if key not in allowed:
                safe_key = str(key).replace("~", "~0").replace("/", "~1")[:100]
                self.issue(
                    path + "/" + safe_key,
                    "unsupported_field",
                    "Setting has no lossless mapping in this conversion profile",
                )
        return value

    def attempt(self, path, operation, default=None):
        from nexus.core.studio_configuration import ConfigurationPreflightError

        try:
            return operation()
        except ConfigurationPreflightError as exc:
            for suffix in exc.paths:
                self.issue(
                    path + suffix,
                    "invalid_mapping",
                    "Invalid named workflow reference, cycle, depth or child graph",
                )
            return default
        except (ValueError, TypeError, KeyError, AttributeError, FileNotFoundError, RecursionError):
            self.issue(
                path,
                "invalid_mapping",
                "Invalid or unavailable mapping; check types, references, limits and supported settings",
            )
            return default

    def load(self, data):
        data = self.fields(data, {"text", "documents", "bindings"}, "")
        if ("text" in data) == ("documents" in data):
            self.issue("/documents", "invalid_source", "Supply exactly one of text or documents")
            return []
        if "text" in data:
            text = data["text"]
            if not isinstance(text, str) or len(text.encode()) > 1024 * 1024:
                self.issue("/text", "size_limit", "Source must be text of at most one MiB")
                return []
            try:
                docs = list(yaml.load_all(text, Loader=SAMLoader))
            except (yaml.YAMLError, ValueError, RecursionError):
                self.issue(
                    "/text",
                    "invalid_yaml",
                    "Source must be bounded YAML/JSON with unique string keys, no aliases and no custom tags",
                )
                return []
        else:
            docs = copy.deepcopy(data["documents"])
        bindings = data.get("bindings")
        if (
            isinstance(docs, list)
            and len(docs) == 1
            and isinstance(docs[0], dict)
            and docs[0].get("format") == "nexus-interop-bundle/v1"
        ):
            bundle = self.fields(docs[0], {"format", "documents", "bindings"}, "/bundle")
            if bindings is not None:
                self.issue(
                    "/bindings",
                    "ambiguous_binding",
                    "Bundle bindings and request overrides cannot be combined",
                )
            bindings = bundle.get("bindings", {})
            docs = bundle.get("documents")
        self.bindings = self.fields(
            copy.deepcopy(bindings) if bindings is not None else {},
            {"models", "agents", "tools", "decisions", "skills", "toolsets", "connectors"},
            "/bindings",
        )
        for group in ("models", "agents", "tools", "decisions", "skills", "toolsets", "connectors"):
            values = self.bindings.get(group, {})
            if not isinstance(values, dict):
                self.issue(
                    "/bindings/" + group,
                    "invalid_type",
                    "Bindings must be an object keyed by external name",
                )
                self.bindings[group] = {}
                continue
            for name, binding in values.items():
                path = "/bindings/" + group + "/" + str(name)[:100]
                if group == "decisions":
                    from nexus.core.studio_decisions import validate_binding

                    binding = self.fields(binding, {"backend", "model"}, path)
                    values[name] = binding
                    self.attempt(path, lambda binding=binding: validate_binding(binding))
                elif group in {"tools", "skills", "toolsets", "connectors"}:
                    if not isinstance(binding, str):
                        self.issue(
                            path,
                            "invalid_binding",
                            "Tool binding must name a registered Nexus tool",
                        )
                else:
                    binding = self.fields(binding, {"provider", "model", "profile"}, path)
                    values[name] = binding
                    if "profile" in binding:
                        if set(binding) != {"profile"}:
                            self.issue(
                                path,
                                "ambiguous_binding",
                                "Choose an existing model profile or explicit provider/model, not both",
                            )
                        profile = self.attempt(
                            path,
                            lambda binding=binding: self.studio.catalog.get(
                                "models", binding["profile"]
                            ),
                        )
                        if profile:
                            # Keep the reference, not resolved credential/provider values.
                            continue
                    from nexus.core.studio import _PROVIDERS

                    if not isinstance(binding.get("provider"), str) or binding["provider"] not in {
                        p[0] for p in _PROVIDERS
                    }:
                        self.issue(
                            path + "/provider",
                            "invalid_binding",
                            "Choose an existing Nexus CLI or API provider",
                        )
                    if "model" in binding and (
                        not isinstance(binding["model"], str)
                        or not 1 <= len(binding["model"]) <= 200
                    ):
                        self.issue(
                            path + "/model",
                            "invalid_binding",
                            "Model must be text of 1–200 characters",
                        )
        if not isinstance(docs, list) or not 1 <= len(docs) <= 100:
            self.issue("/documents", "size_limit", "Supply 1–100 resource documents")
            return []
        return docs

    def ref(self, kind, name):
        if not isinstance(name, str) or (kind, name) not in self.names:
            raise ValueError("Unknown resource reference")
        return self.names[(kind, name)]

    def tools(self, entries, path, references=None):
        if not isinstance(entries, list):
            self.issue(path, "invalid_type", "Tools must be a list")
            return []
        result = []
        for i, entry in enumerate(entries):
            where = path + "/" + str(i)
            if isinstance(entry, dict) and references is not None:
                from nexus.core.studio_interop_agent_settings import tool_references

                if self.attempt(
                    where,
                    lambda entry=entry, where=where: tool_references(
                        self, entry, where, references
                    ),
                    True,
                ):
                    continue
            entry = self.fields(entry, {"tool_type", "tool_name"}, where)
            if entry.get("tool_type") != "builtin":
                self.issue(
                    where + "/tool_type",
                    "unsupported_tool",
                    "Configure remote tools as Nexus connectors; only explicitly bound builtin tools convert here",
                )
            name = entry.get("tool_name")
            target = self.bindings.get("tools", {}).get(name) if isinstance(name, str) else None
            if not target:
                self.issue(
                    where + "/tool_name",
                    "binding_required",
                    "Provide an explicit registered Nexus tool binding",
                )
            elif target not in result:
                result.append(target)
        return result

    def agent(self, source, name, description, path, declarative):
        from nexus.core.studio_interop_agent_settings import (
            RUNTIME,
            agent_settings,
            declarative_card,
            resource_binding,
        )

        allowed = (
            {
                "systemPrompt",
                "modelProvider",
                "inputModes",
                "outputModes",
                "skills",
                "toolsets",
                "deploy",
                "additionalConfigurations",
            }
            if declarative
            else {"agent_name", "agent_type", "description", "instruction", "agent_card", "tools"}
            | RUNTIME
        )
        source = self.fields(source, allowed, path)
        instruction = source.get("systemPrompt" if declarative else "instruction")
        if not isinstance(instruction, str) or not 1 <= len(instruction) <= 32000:
            self.issue(
                path + "/instruction",
                "invalid_instruction",
                "Agent instructions must contain 1–32000 characters",
            )
        elif any(t in instruction for t in ("{{", "}}", "${")):
            self.issue(
                path + "/instruction",
                "unsupported_template",
                "Agent instruction template expansion needs explicit manual adaptation",
            )
        tools, references = [], {}
        settings = {}
        if declarative:
            for key in ("inputModes", "outputModes"):
                if key in source and source[key] != ["text"]:
                    self.issue(
                        path + "/" + key,
                        "unsupported_modes",
                        "Only text modes map to this agent profile",
                    )
            if "deploy" in source:
                self.issue(
                    path + "/deploy",
                    "explicit_activation",
                    "Remove external deploy; review and activate through Nexus lifecycle controls",
                )
            extra = self.fields(
                source.get("additionalConfigurations", {}),
                {"tools"} | RUNTIME,
                path + "/additionalConfigurations",
            )
            tools = self.tools(
                extra.get("tools", []), path + "/additionalConfigurations/tools", references
            )
            settings = agent_settings(self, extra, path + "/additionalConfigurations")
            card = declarative_card(self, source, path)
            if card:
                settings["agent_card"] = card
            toolsets = source.get("toolsets", [])
            if not isinstance(toolsets, list) or len(toolsets) > 30:
                self.issue(
                    path + "/toolsets", "invalid_toolsets", "Use up to thirty bound toolsets"
                )
            else:
                for index, alias in enumerate(toolsets):
                    target = resource_binding(
                        self, "toolsets", alias, "toolsets", path + "/toolsets/" + str(index)
                    )
                    if target:
                        references.setdefault("toolset_ids", []).append(target)
                if references.get("toolset_ids"):
                    references["toolset_ids"] = list(dict.fromkeys(references["toolset_ids"]))
            aliases = source.get("modelProvider", [])
            if (
                not isinstance(aliases, list)
                or len(aliases) != 1
                or not isinstance(aliases[0], str)
            ):
                self.issue(
                    path + "/modelProvider",
                    "binding_required",
                    "Select exactly one model alias and explicitly bind it to a Nexus provider",
                )
                binding = {}
            else:
                binding = self.bindings.get("models", {}).get(aliases[0], {})
        else:
            if source.get("agent_type", "standard") != "standard":
                self.issue(
                    path + "/agent_type",
                    "unsupported_type",
                    "Use a workflow resource for workflow agents",
                )
            card = self.fields(
                source.get("agent_card", {}),
                {
                    "description",
                    "skills",
                    "defaultInputModes",
                    "defaultOutputModes",
                    "version",
                    "documentationUrl",
                    "provider",
                },
                path + "/agent_card",
            )
            if card:
                settings["agent_card"] = card
            settings.update(agent_settings(self, source, path))
            tools = self.tools(source.get("tools", []), path + "/tools", references)
            binding = self.bindings.get("agents", {}).get(name, {})
        if not binding:
            self.issue(
                path,
                "binding_required",
                "Explicit Nexus provider binding required; no provider is selected automatically",
            )
        profile_id = binding.get("profile")
        effective = (
            self.attempt(path, lambda: self.studio.catalog.get("models", profile_id)["data"], {})
            if profile_id
            else binding
        )
        return {
            "agent_type": identity(name),
            "purpose": instruction or "",
            "provider": effective.get("provider", "codex"),
            "timeout_seconds": 600,
            "max_retries": 0,
            **({"model": effective["model"]} if effective.get("model") else {}),
            **({"tool_ids": tools} if tools else {}),
            **({"model_id": profile_id} if profile_id else {}),
            **settings,
            **references,
        }

    def workflow_node(self, node, where, source, tool_ids):
        node = self.fields(
            node,
            {
                "id",
                "type",
                "depends_on",
                "input",
                "input_schema_override",
                "output_schema_override",
                "timeout",
                "agent_name",
                "workflow_name",
                "tool_name",
                "instruction",
            },
            where,
        )
        kind = node.get("type") if isinstance(node.get("type"), str) else None
        for key in set(node) & {"agent_name", "workflow_name", "tool_name"} - (
            {"agent_name"}
            if kind == "agent"
            else {"workflow_name"}
            if kind == "workflow"
            else {"tool_name"}
            if kind == "tool"
            else set()
        ):
            self.issue(
                where + "/" + key,
                "incompatible_field",
                "Field does not belong to this node type",
            )
        item = {
            "id": node.get("id"),
            "name": node.get("id"),
            "kind": kind,
            "input": self.attempt(
                where + "/input",
                lambda node=node: import_value(node.get("input", "{{workflow.input}}")),
            ),
        }
        if "depends_on" in node:
            item["depends_on"] = node["depends_on"]
        for src, dst in (
            ("input_schema_override", "input_schema"),
            ("output_schema_override", "output_schema"),
        ):
            if src in node:
                item[dst] = node[src]
        item["timeout_seconds"] = self.attempt(
            where + "/timeout",
            lambda node=node: duration(
                node.get("timeout", source.get("default_node_timeout", "300s"))
            ),
            300,
        )
        if "instruction" in node:
            instruction = node["instruction"]
            if (
                kind != "agent"
                or not isinstance(instruction, str)
                or not 1 <= len(instruction) <= 4000
                or any(t in instruction for t in ("{{", "}}", "${"))
            ):
                self.issue(
                    where + "/instruction",
                    "unsupported_instruction",
                    "Use bounded static invocation instructions on agent nodes",
                )
            else:
                item["invocation_instruction"] = instruction
        if kind in {"agent", "workflow"}:
            key = "agent_name" if kind == "agent" else "workflow_name"
            item["agent_id" if kind == "agent" else "workflow_id"] = self.attempt(
                where + "/" + key,
                lambda node=node, kind=kind, key=key: self.ref(
                    "agents" if kind == "agent" else "workflows", node.get(key)
                ),
                "",
            )
        elif kind == "tool":
            target = (
                self.bindings.get("tools", {}).get(node.get("tool_name"))
                if isinstance(node.get("tool_name"), str)
                else None
            )
            if not target or target not in tool_ids:
                self.issue(
                    where + "/tool_name",
                    "binding_required",
                    "Tool must be declared in workflow tools and explicitly bound to Nexus",
                )
            item["tool_id"] = target or ""
            item["kwargs"] = item.pop("input")
        else:
            self.issue(
                where + "/type",
                "unsupported_node",
                "This external node needs manual translation; branch, map, loop and decision semantics are never silently approximated",
            )
        return item

    def workflow(self, source, path, name):
        from nexus.core.studio_interop_workflows import SAMWorkflowCompiler, needs_compatibility

        if needs_compatibility(source):
            return SAMWorkflowCompiler(self).compile(source, path, name)
        allowed = {
            "agent_name",
            "agent_type",
            "description",
            "nodes",
            "input_schema",
            "output_schema",
            "output_mapping",
            "default_node_timeout",
            "tools",
        }
        source = self.fields(source, allowed, path)
        if "agent_name" in source and source["agent_name"] != name:
            self.issue(
                path + "/agent_name",
                "conflicting_identity",
                "Workflow identity conflicts with its resource name",
            )
        if source.get("agent_type", "workflow") != "workflow":
            self.issue(
                path + "/agent_type",
                "unsupported_type",
                "Workflow app configuration must use workflow agent_type",
            )
        if "description" in source and (
            not isinstance(source["description"], str) or len(source["description"]) > 2000
        ):
            self.issue(
                path + "/description",
                "invalid_description",
                "Workflow description must be text of at most 2000 characters",
            )
        tool_ids = self.tools(source.get("tools", []), path + "/tools")
        nodes = source.get("nodes")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 40:
            self.issue(path + "/nodes", "invalid_nodes", "Use 1–40 workflow nodes")
            nodes = []
        result = {"version": 2, "mapping_language": LANGUAGE, "nodes": []}
        for key in ("input_schema", "output_schema"):
            if key in source:
                result[key] = source[key]
        if "output_mapping" not in source:
            self.issue(
                path + "/output_mapping",
                "required_field",
                "External workflows require an explicit output mapping",
            )
        result["output_mapping"] = self.attempt(
            path + "/output_mapping", lambda: import_value(source.get("output_mapping", {})), {}
        )
        for index, node in enumerate(nodes):
            result["nodes"].append(
                self.workflow_node(node, path + "/nodes/" + str(index), source, tool_ids)
            )
        return result

    def convert(self, data, actor):
        self.configuration.authorize(actor)
        try:
            # Also bounds direct Python consumers and rejects NaN/non-JSON values.
            def bounded(value, depth=0, budget=None):
                budget = budget if budget is not None else [20000]
                budget[0] -= 1
                if depth > 50 or budget[0] < 0:
                    raise ValueError
                if isinstance(value, dict):
                    for key, item in value.items():
                        if not isinstance(key, str):
                            raise ValueError
                        bounded(item, depth + 1, budget)
                elif isinstance(value, list):
                    for item in value:
                        bounded(item, depth + 1, budget)

            bounded(data)
            if len(json.dumps(data, allow_nan=False).encode()) > 2 * 1024 * 1024:
                raise ValueError
        except (ValueError, TypeError, RecursionError):
            self.issue("/", "size_limit", "Supply bounded JSON-compatible configuration")
            return {
                "format": "nexus-interop-conversion/v1",
                "valid": False,
                "diagnostics": self.diagnostics,
                "document": None,
            }
        documents = self.load(data)
        for kind in ("agents", "workflows"):
            for entry in self.studio.definitions(kind):
                self.names[(kind, entry["id"])] = entry["id"]
                if entry.get("document"):
                    self.names[(kind, entry["document"].get("metadata", {}).get("name"))] = entry[
                        "id"
                    ]
        parsed, seen = [], set()
        for index, source in enumerate(documents):
            path = "/documents/" + str(index)
            if not isinstance(source, dict):
                self.issue(path, "invalid_resource", "Resource must be an object")
                continue
            declarative = "kind" in source
            if declarative:
                source = self.fields(source, {"kind", "name", "description", "spec"}, path)
                kind, name = source.get("kind"), source.get("name")
            else:
                kind = "workflow" if source.get("agent_type") == "workflow" else "agent"
                name = source.get("agent_name")
            if not isinstance(kind, str) or kind not in {"agent", "workflow"}:
                self.issue(
                    path + "/kind",
                    "unsupported_kind",
                    "Only agent and workflow resources convert; environment, broker, identity and deployment resources stay outside this bridge",
                )
                continue
            target = self.attempt(path + "/name", lambda name=name: identity(name))
            if target is None:
                continue
            key = (kind + "s", target)
            if key in seen:
                self.issue(
                    path + "/name",
                    "duplicate_identity",
                    "Names collide after Nexus identifier normalization",
                )
            seen.add(key)
            self.names[(kind + "s", name)] = target
            parsed.append((path, source, declarative, kind, name, target))
        definitions = []
        for path, source, declarative, kind, name, target in parsed:
            description = source.get("description", "")
            if not isinstance(description, str) or len(description) > 2000:
                self.issue(
                    path + "/description",
                    "invalid_description",
                    "Description must be text of at most 2000 characters",
                )
            doc = {
                "apiVersion": "nexus-arc/v1",
                "kind": kind.title(),
                "metadata": {"name": name, "description": description},
            }
            spec = source.get("spec") if declarative else source
            if kind == "agent":
                doc["spec"] = self.agent(
                    spec, name, description, path + ("/spec" if declarative else ""), declarative
                )
                if (
                    set(doc["spec"])
                    & {
                        "model_id",
                        "skill_ids",
                        "toolset_ids",
                        "connector_ids",
                        "max_model_calls",
                        "output_schema",
                        "agent_card",
                    }
                    or doc["spec"].get("timeout_seconds") != 600
                ):
                    doc["metadata"]["interop_agent_source"] = {
                        "declarative": declarative,
                        "source": copy.deepcopy(spec),
                        "bindings": {
                            group: copy.deepcopy(self.bindings.get(group, {}))
                            for group in (
                                "models",
                                "agents",
                                "tools",
                                "decisions",
                                "skills",
                                "toolsets",
                                "connectors",
                            )
                        },
                    }
            else:
                if declarative:
                    spec = self.fields(spec, {"appConfig"}, path + "/spec").get("appConfig", {})
                doc["graph"] = self.workflow(
                    spec, path + ("/spec/appConfig" if declarative else ""), name
                )
                if isinstance(spec, dict) and "description" in spec:
                    doc["metadata"]["interop_workflow_description"] = spec["description"]
                if doc["graph"].get("mapping_language") == "interop-template/v1":
                    doc["metadata"]["interop_workflow_source"] = copy.deepcopy(spec)
                    decision = spec.get("decision", {})
                    alias = decision.get("model_alias") if isinstance(decision, dict) else None
                    if isinstance(alias, str) and alias in self.bindings.get("decisions", {}):
                        doc["metadata"]["interop_decision_bindings"] = {
                            alias: copy.deepcopy(self.bindings["decisions"][alias])
                        }
            if kind == "agent":
                if (
                    declarative
                    and isinstance(spec, dict)
                    and isinstance(spec.get("modelProvider"), list)
                    and len(spec["modelProvider"]) == 1
                    and isinstance(spec["modelProvider"][0], str)
                ):
                    doc["metadata"]["interop_model_alias"] = spec["modelProvider"][0]
                else:
                    from nexus.core.studio_configuration import digest

                    selected = {
                        key: doc["spec"][key] for key in ("provider", "model") if key in doc["spec"]
                    }
                    doc["metadata"]["interop_model_alias"] = "nexus-" + digest(selected)[:12]
                source_tools = (
                    spec.get("additionalConfigurations", {}).get("tools", [])
                    if declarative
                    and isinstance(spec, dict)
                    and isinstance(spec.get("additionalConfigurations", {}), dict)
                    else spec.get("tools", [])
                    if isinstance(spec, dict)
                    else []
                )
            else:
                source_tools = spec.get("tools", []) if isinstance(spec, dict) else []
            aliases = {}
            for entry in source_tools if isinstance(source_tools, list) else []:
                tool_name = entry.get("tool_name") if isinstance(entry, dict) else None
                mapped = (
                    self.bindings.get("tools", {}).get(tool_name)
                    if isinstance(tool_name, str)
                    else None
                )
                if isinstance(mapped, str):
                    if mapped in aliases and aliases[mapped] != tool_name:
                        self.issue(
                            path + "/tools",
                            "ambiguous_binding",
                            "Multiple external names for the same Nexus tool require separate manual mappings",
                        )
                    aliases[mapped] = tool_name
            if aliases:
                doc["metadata"]["interop_tool_names"] = aliases
            definitions.append({"collection": kind + "s", "id": target, "document": doc})
        native = {"format": "nexus-studio-config/v1", "definitions": definitions, "resources": []}
        if not self.diagnostics:
            native = self.attempt("/document", lambda: self.configuration.normalize(native))
        return {
            "format": "nexus-interop-conversion/v1",
            "valid": not self.diagnostics,
            "diagnostics": self.diagnostics,
            "document": None if self.diagnostics else native,
        }

    def tool_alias(self, meta, target, path, existing):
        aliases = meta.get("interop_tool_names", {})
        original = aliases.get(target) if isinstance(aliases, dict) else None
        if not isinstance(original, str) or not 1 <= len(original) <= 200:
            self.issue(
                path + "/tool_name",
                "binding_required",
                "Native tools need an original external tool name before external export; use native export otherwise",
            )
            return "unmapped"
        if original in existing and existing[original] != target:
            self.issue(
                path + "/tool_name",
                "ambiguous_binding",
                "A shared external tool name cannot bind different Nexus tools",
            )
        return original

    def export(self, data, actor):
        self.configuration.authorize(actor)
        data = self.fields(data, {"document"}, "")
        native = data.get("document")
        if native is None:
            native = self.configuration.export(actor)
        native = self.fields(native, {"format", "definitions", "resources"}, "/document")
        if native.get("format") != "nexus-studio-config/v1":
            self.issue(
                "/document/format", "invalid_format", "Choose a native Nexus configuration document"
            )
        normalized = self.attempt("/document", lambda: self.configuration.normalize(native))
        if normalized is None:
            return {
                "format": "nexus-interop-export/v1",
                "valid": False,
                "diagnostics": self.diagnostics,
                "bundle": None,
            }
        native = normalized
        definitions = native.get("definitions", [])
        resources = native.get("resources", [])
        if (
            not isinstance(definitions, list)
            or len(definitions) > 100
            or not isinstance(resources, list)
        ):
            self.issue(
                "/document",
                "invalid_resources",
                "Use at most 100 definitions and an array of resources",
            )
            definitions, resources = [], []
        for index, _ in enumerate(resources):
            self.issue(
                "/document/resources/" + str(index),
                "unsupported_resource",
                "This bridge exports agents/workflows only; other resources require native export",
            )
        names = {}
        for index, entry in enumerate(definitions):
            if isinstance(entry, dict) and isinstance(entry.get("document"), dict):
                name = entry["document"].get("metadata", {}).get("name")
                if self.attempt(
                    "/document/definitions/" + str(index) + "/name",
                    lambda name=name: identity(name),
                ) != entry.get("id"):
                    self.issue(
                        "/document/definitions/" + str(index) + "/id",
                        "unsupported_identity",
                        "Resource ID must match its normalized display name for a lossless external export",
                    )
                names[(entry.get("collection"), entry.get("id"))] = name
        for kind in ("agents", "workflows"):
            for entry in self.studio.definitions(kind):
                names.setdefault(
                    (kind, entry["id"]),
                    entry.get("document", {}).get("metadata", {}).get("name", entry["id"]),
                )
        bindings, documents = {"models": {}, "tools": {}}, []

        def mapped(value, graph):
            exported = export_value(value, graph.get("mapping_language"))
            # Round-trip proves literal/operator distinction, including delimiter traps.
            if import_value(exported) != value:
                raise ValueError("Mapping cannot round-trip losslessly")
            return exported

        for index, entry in enumerate(definitions):
            path = "/document/definitions/" + str(index)
            entry = self.fields(entry, {"collection", "id", "document"}, path)
            doc = self.fields(
                entry.get("document"),
                {"apiVersion", "kind", "metadata", "spec", "graph"},
                path + "/document",
            )
            meta = self.fields(
                doc.get("metadata"),
                {
                    "name",
                    "description",
                    "interop_workflow_description",
                    "interop_model_alias",
                    "interop_tool_names",
                    "interop_workflow_source",
                    "interop_decision_bindings",
                    "interop_agent_source",
                },
                path + "/metadata",
            )
            kind = entry.get("collection")
            out = {
                "kind": "agent" if kind == "agents" else "workflow",
                "name": meta.get("name"),
                "description": meta.get("description", ""),
            }
            if kind == "agents" and meta.get("interop_agent_source"):
                original = self.fields(
                    meta["interop_agent_source"],
                    {"declarative", "source", "bindings"},
                    path + "/metadata/interop_agent_source",
                )
                if (
                    not isinstance(original.get("source"), dict)
                    or type(original.get("declarative")) is not bool
                    or not isinstance(original.get("bindings"), dict)
                ):
                    self.issue(
                        path,
                        "invalid_source",
                        "Agent source and resource bindings are required for lossless export",
                    )
                else:
                    for group, aliases in original["bindings"].items():
                        if not isinstance(aliases, dict):
                            self.issue(
                                path, "invalid_binding", "Saved resource bindings must be objects"
                            )
                            continue
                        for alias, binding in aliases.items():
                            existing = bindings.setdefault(group, {}).get(alias)
                            if existing is not None and existing != binding:
                                self.issue(
                                    path,
                                    "ambiguous_binding",
                                    "Shared resource aliases require consistent bindings",
                                )
                            else:
                                bindings[group][alias] = copy.deepcopy(binding)
                    if original["declarative"]:
                        out["spec"] = copy.deepcopy(original["source"])
                    else:
                        out = copy.deepcopy(original["source"])
                    documents.append(out)
                continue
            if kind == "agents":
                spec = self.fields(
                    doc.get("spec"),
                    {
                        "agent_type",
                        "purpose",
                        "provider",
                        "model",
                        "timeout_seconds",
                        "max_retries",
                        "tool_ids",
                    },
                    path + "/spec",
                )
                if "graph" in doc:
                    self.issue(
                        path + "/graph",
                        "unsupported_field",
                        "Agent cannot contain a workflow graph",
                    )
                if (
                    spec.get("agent_type") != entry.get("id")
                    or spec.get("timeout_seconds", 600) != 600
                    or spec.get("max_retries", 0) != 0
                ):
                    self.issue(
                        path + "/spec",
                        "unsupported_agent_policy",
                        "Custom agent types, timeouts or retries need native export",
                    )
                instruction = spec.get("purpose", "")
                if isinstance(instruction, str) and any(
                    t in instruction for t in ("{{", "}}", "${")
                ):
                    self.issue(
                        path + "/spec/purpose",
                        "unsupported_template",
                        "Literal template delimiters would change meaning in external",
                    )
                binding = {
                    "provider": spec.get("provider"),
                    **({"model": spec["model"]} if spec.get("model") else {}),
                }
                from nexus.core.studio_configuration import digest

                alias = meta.get("interop_model_alias", "nexus-" + digest(binding)[:12])
                if not isinstance(alias, str) or not 1 <= len(alias) <= 200:
                    self.issue(
                        path + "/metadata/interop_model_alias",
                        "invalid_alias",
                        "External model alias must be text of 1–200 characters",
                    )
                    alias = "invalid"
                if alias in bindings["models"] and bindings["models"][alias] != binding:
                    self.issue(
                        path + "/metadata/interop_model_alias",
                        "ambiguous_binding",
                        "A shared external model alias cannot bind different Nexus providers/models",
                    )
                bindings["models"][alias] = binding
                out["spec"] = {"systemPrompt": instruction, "modelProvider": [alias]}
                tools = []
                for target in spec.get("tool_ids", []):
                    # external builtin tool names can be namespaced; explicit binding keeps identity.
                    original = self.tool_alias(meta, target, path, bindings["tools"])
                    bindings["tools"][original] = target
                    tools.append({"tool_type": "builtin", "tool_name": original})
                if tools:
                    out["spec"]["additionalConfigurations"] = {"tools": tools}
            elif (
                kind == "workflows"
                and isinstance(doc.get("graph"), dict)
                and doc["graph"].get("mapping_language") == "interop-template/v1"
            ):
                source = meta.get("interop_workflow_source")
                if not isinstance(source, dict):
                    self.issue(
                        path + "/metadata/interop_workflow_source",
                        "source_required",
                        "External compatibility export requires its original source and a matching compiled graph",
                    )
                else:
                    out["spec"] = {"appConfig": copy.deepcopy(source)}
                    decision_bindings = meta.get("interop_decision_bindings", {})
                    if not isinstance(decision_bindings, dict):
                        self.issue(
                            path + "/metadata/interop_decision_bindings",
                            "invalid_binding",
                            "Decision bindings must be an object",
                        )
                    else:
                        for alias, binding in decision_bindings.items():
                            previous = bindings.setdefault("decisions", {}).get(alias)
                            if previous is not None and previous != binding:
                                self.issue(
                                    path + "/metadata/interop_decision_bindings",
                                    "ambiguous_binding",
                                    "Decision aliases require consistent bindings",
                                )
                            else:
                                bindings["decisions"][alias] = copy.deepcopy(binding)
                    aliases = meta.get("interop_tool_names", {})
                    if isinstance(aliases, dict):
                        for target, original in aliases.items():
                            if (
                                not isinstance(original, str)
                                or not isinstance(target, str)
                                or original in bindings["tools"]
                                and bindings["tools"][original] != target
                            ):
                                self.issue(
                                    path + "/metadata/interop_tool_names",
                                    "ambiguous_binding",
                                    "External tool names require consistent string bindings",
                                )
                            else:
                                bindings["tools"][original] = target
            elif kind == "workflows" and isinstance(doc.get("graph"), dict):
                graph = self.fields(
                    doc["graph"],
                    {
                        "version",
                        "mapping_language",
                        "nodes",
                        "edges",
                        "input_schema",
                        "output_schema",
                        "output_mapping",
                    },
                    path + "/graph",
                )
                if graph.get("version") != 2:
                    self.issue(
                        path + "/graph/version",
                        "unsupported_version",
                        "Only structured version-2 graphs have an external mapping",
                    )
                app = {"nodes": []}
                if "interop_workflow_description" in meta:
                    app["description"] = meta["interop_workflow_description"]
                for key in ("input_schema", "output_schema"):
                    if key in graph:
                        app[key] = graph[key]
                if "output_mapping" not in graph:
                    self.issue(
                        path + "/graph/output_mapping",
                        "explicit_output_required",
                        "Choose an explicit structured output mapping before external export",
                    )
                app["output_mapping"] = self.attempt(
                    path + "/graph/output_mapping",
                    lambda graph=graph: mapped(graph.get("output_mapping", {}), graph),
                )
                edges = graph.get("edges", [])
                for edge_index, edge in enumerate(edges):
                    if not isinstance(edge, dict) or set(edge) - {"source", "target"}:
                        self.issue(
                            path + "/graph/edges/" + str(edge_index),
                            "unsupported_edge",
                            "Conditional edges need manual external branch translation",
                        )
                declared_tools = []
                for node_index, node in enumerate(graph.get("nodes", [])):
                    where = path + "/graph/nodes/" + str(node_index)
                    node = self.fields(
                        node,
                        {
                            "id",
                            "name",
                            "kind",
                            "depends_on",
                            "input",
                            "input_schema",
                            "output_schema",
                            "timeout_seconds",
                            "retries",
                            "agent_id",
                            "workflow_id",
                            "tool_id",
                            "args",
                            "kwargs",
                            "prompt",
                        },
                        where,
                    )
                    node_kind = node.get("kind")
                    if node.get("name", node.get("id")) != node.get("id"):
                        self.issue(
                            where + "/name",
                            "unsupported_label",
                            "External nodes use their ID as label; rename the node explicitly before export",
                        )
                    if node.get("retries", 0):
                        self.issue(
                            where + "/retries",
                            "unsupported_retry",
                            "Retry policies require native export or manual external adaptation",
                        )
                    converted = {"id": node.get("id"), "type": node_kind}
                    dependencies = list(
                        dict.fromkeys(
                            [
                                *node.get("depends_on", []),
                                *[
                                    edge["source"]
                                    for edge in edges
                                    if isinstance(edge, dict)
                                    and edge.get("target") == node.get("id")
                                    and edge.get("source") != "START"
                                ],
                            ]
                        )
                    )
                    if dependencies:
                        converted["depends_on"] = dependencies
                    converted["timeout"] = str(node.get("timeout_seconds", 300)) + "s"
                    for src, dst in (
                        ("input_schema", "input_schema_override"),
                        ("output_schema", "output_schema_override"),
                    ):
                        if src in node:
                            converted[dst] = node[src]
                    if node_kind in {"agent", "workflow"}:
                        if node.get("prompt", "{task}") != "{task}":
                            self.issue(
                                where + "/prompt",
                                "unsupported_prompt",
                                "Move custom node prompts into structured input before external export",
                            )
                        collection = "agents" if node_kind == "agent" else "workflows"
                        target = node.get("agent_id" if node_kind == "agent" else "workflow_id")
                        if (collection, target) not in names:
                            self.issue(
                                where, "missing_reference", "Referenced resource is unavailable"
                            )
                        converted["agent_name" if node_kind == "agent" else "workflow_name"] = (
                            names.get((collection, target))
                        )
                        converted["input"] = self.attempt(
                            where + "/input",
                            lambda node=node, graph=graph: mapped(
                                node.get("input", {"ref": "input"}), graph
                            ),
                        )
                    elif node_kind == "tool":
                        if node.get("args"):
                            self.issue(
                                where + "/args",
                                "unsupported_arguments",
                                "External tool arguments are named; positional arguments need manual adaptation",
                            )
                        if "input" in node and node["input"] != {"ref": "input"}:
                            self.issue(
                                where + "/input",
                                "unsupported_input",
                                "Tool input remapping needs manual composition before export",
                            )
                        target = node.get("tool_id")
                        original = self.tool_alias(meta, target, where, bindings["tools"])
                        converted["tool_name"] = original
                        bindings["tools"][original] = target
                        declared_tools.append({"tool_type": "builtin", "tool_name": original})
                        converted["input"] = self.attempt(
                            where + "/kwargs",
                            lambda node=node, graph=graph: mapped(node.get("kwargs", {}), graph),
                        )
                    else:
                        self.issue(
                            where + "/kind",
                            "unsupported_node",
                            "This node's runtime semantics require manual external translation",
                        )
                    app["nodes"].append(converted)
                if declared_tools:
                    app["tools"] = list(
                        {tool["tool_name"]: tool for tool in declared_tools}.values()
                    )
                out["spec"] = {"appConfig": app}
            else:
                self.issue(
                    path,
                    "unsupported_definition",
                    "Only agents and structured workflows can be exported",
                )
            documents.append(out)
        bundle = {"format": "nexus-interop-bundle/v1", "documents": documents, "bindings": bindings}
        if not self.diagnostics:
            # The same import validator certifies that the export is usable here.
            checked = StudioSAMConfiguration(self.configuration).convert(
                {"documents": [bundle]}, actor
            )
            if not checked["valid"]:
                self.diagnostics.extend(checked["diagnostics"])
            else:
                imported = {
                    (row["collection"], row["id"]): row["document"]
                    for row in checked["document"]["definitions"]
                }
                for index, row in enumerate(definitions):
                    graph = row["document"].get("graph", {})
                    if (
                        graph.get("mapping_language") == "interop-template/v1"
                        and imported.get((row["collection"], row["id"]), {}).get("graph") != graph
                    ):
                        self.issue(
                            "/document/definitions/" + str(index) + "/graph",
                            "source_graph_mismatch",
                            "Graph differs from its external source; update/reconvert the source before external export",
                        )
        return {
            "format": "nexus-interop-export/v1",
            "valid": not self.diagnostics,
            "diagnostics": self.diagnostics,
            "bundle": None if self.diagnostics else bundle,
        }
