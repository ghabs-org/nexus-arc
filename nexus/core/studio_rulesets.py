"""Grouped ruleset text from authorized versioned artifacts, never executable logic."""

import hashlib
import json
import math

from nexus.core.studio_connections import execution_identity
from nexus.core.studio_data_tools import current_run_id


def short(value, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Ruleset requires bounded nonempty text")
    return value


def validate(document):
    if not isinstance(document, dict):
        raise ValueError("Ruleset document must be a JSON object")
    if "decision_data" in document:
        if set(document) - {
            "decision_data",
            "default_group_key",
            "include_topics_in_description",
            "include_groups_in_description",
        }:
            raise ValueError("Unsupported ruleset configuration")
        for flag in ("include_topics_in_description", "include_groups_in_description"):
            if flag in document and type(document[flag]) is not bool:
                raise ValueError("Ruleset description flags must be boolean")
        default = short(document.get("default_group_key", "_default"))
        data = document["decision_data"]
    else:
        default, data = "_default", document
    required = {
        "name",
        "description",
        "grouping_parameter",
        "input_parameters",
        "decision_tree_groups",
    }
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError("Ruleset requires the complete decision_data schema")
    for key in ("name", "description", "grouping_parameter"):
        short(data[key], 2000 if key == "description" else 200)
    parameters = data["input_parameters"]
    if not isinstance(parameters, list) or not 1 <= len(parameters) <= 32:
        raise ValueError("Ruleset requires 1 to 32 input parameters")
    names = set()
    for param in parameters:
        if not isinstance(param, dict) or set(param) - {"name", "type", "required", "description"}:
            raise ValueError("Invalid ruleset input parameter")
        name = short(param.get("name"))
        if (
            name in names
            or param.get("type", "string") not in {"string", "boolean", "integer", "number"}
            or type(param.get("required", False)) is not bool
        ):
            raise ValueError("Invalid or duplicate ruleset input parameter")
        names.add(name)
    grouping = next((p for p in parameters if p["name"] == data["grouping_parameter"]), None)
    if not grouping or grouping.get("type", "string") != "string":
        raise ValueError("Grouping parameter must be a declared string input")
    groups = data["decision_tree_groups"]
    if not isinstance(groups, dict) or not 1 <= len(groups) <= 100:
        raise ValueError("Ruleset requires 1 to 100 groups")
    normalized, count = {}, 0
    for key, value in groups.items():
        folded = short(key).casefold()
        if folded in normalized or not isinstance(value, dict) or set(value) != {"decision_trees"}:
            raise ValueError("Invalid or case-ambiguous ruleset group")
        trees = value["decision_trees"]
        if not isinstance(trees, list) or not trees:
            raise ValueError("Ruleset groups require decision trees")
        count += len(trees)
        if count > 100:
            raise ValueError("Ruleset exceeds 100 decision trees")
        for tree in trees:
            if not isinstance(tree, dict) or set(tree) != {"name", "description", "decision_logic"}:
                raise ValueError("Decision trees require name, description and text logic")
            short(tree["name"])
            short(tree["description"], 2000)
            short(tree["decision_logic"], 16384)
        normalized[folded] = (key, value)
    return data, default, normalized


class StudioRulesets:
    def __init__(self, studio):
        from nexus.tools import tool_registry

        self.studio = studio
        name = (
            "studio:"
            + hashlib.sha256(str(studio.state).encode()).hexdigest()[:12]
            + ":ruleset_lookup"
        )

        async def invoke(artifact_id, parameters, revision=None):
            return self.lookup(artifact_id, parameters, revision, self.actor())

        tool_registry.register(
            name,
            invoke,
            category="Data and media",
            description=(
                "Look up grouped policy/ruleset text from a private JSON artifact. Arguments: artifact_id, parameters object, optional revision. Returns pinned evidence and untrusted text, never executes decision logic."
            ),
        )
        self.names = {name: invoke}

    def actor(self):
        if not self.studio.team:
            return None
        identity = execution_identity.get()
        self.studio.team.assert_execution(identity)
        return {"nexus_id": identity, "role": self.studio.team.bindings[identity]}

    def close(self):
        from nexus.tools import tool_registry

        for name, function in self.names.items():
            tool_registry.unregister(name, func=function)

    def inventory(self):
        return {
            "items": [
                {
                    "id": name,
                    "configured": True,
                    "billing": "Local lookup; selected artifact storage",
                    "setup": "Upload an owned JSON artifact with decision_data, then select its ID and optional revision.",
                }
                for name in self.names
            ]
        }

    def lookup(self, artifact_id, parameters, revision=None, actor=None):
        if self.studio.team:
            if not actor:
                raise PermissionError("Ruleset lookup requires Nexus identity")
            self.studio.team.assert_execution(actor["nexus_id"])
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError("Ruleset revision must be a positive integer")
        try:
            current = self.studio.collaboration.owned(
                self.studio.catalog.get("artifacts", artifact_id), actor
            )
            record = self.studio.collaboration.owned(
                self.studio.artifacts.revision(current["id"], revision), actor
            )
        except (FileNotFoundError, PermissionError, ValueError):
            raise ValueError("Ruleset source is unavailable") from None
        if record["data"].get("size", 262145) > 262144:
            raise ValueError("Ruleset source exceeds 256 KiB")
        try:
            content = self.studio.artifacts.get(record)
            if len(content) > 262144:
                raise ValueError
            document = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("Ruleset source must contain bounded UTF-8 JSON") from None
        data, default, groups = validate(document)
        if not isinstance(parameters, dict) or set(parameters) - {
            p["name"] for p in data["input_parameters"]
        }:
            raise ValueError("Ruleset parameters contain unknown inputs")
        for param in data["input_parameters"]:
            name, kind = param["name"], param.get("type", "string")
            if name not in parameters:
                if param.get("required") or name == data["grouping_parameter"]:
                    raise ValueError("A required ruleset parameter is missing")
                continue
            value = parameters[name]
            valid = (
                isinstance(value, str) and len(value) <= 2000
                if kind == "string"
                else type(value) is bool
                if kind == "boolean"
                else type(value) is int and abs(value) <= 1e15
                if kind == "integer"
                else type(value) in (int, float) and abs(value) <= 1e15 and math.isfinite(value)
            )
            if not valid:
                raise ValueError("Ruleset input does not match its declared type")
        selected = parameters[data["grouping_parameter"]].casefold()
        used_default = selected not in groups
        if used_default:
            selected = default.casefold()
        if selected not in groups:
            raise ValueError("No matching ruleset group or configured default")
        group, value = groups[selected]
        logic = "\n\n---\n\n".join(
            "Ruleset Name: "
            + tree["name"]
            + "\nDescription: "
            + tree["description"]
            + "\nLogic:\n"
            + tree["decision_logic"]
            for tree in value["decision_trees"]
        )
        source = {
            "kind": "artifact",
            "artifact_id": record["id"],
            "artifact_revision": record["revision"],
            "filename": record["data"]["filename"],
            "page": 1,
            "sha256": record["data"]["sha256"],
        }
        result = {
            "name": data["name"],
            "group": group,
            "used_default": used_default,
            "decision_logic": logic,
            "decision_trees": value["decision_trees"],
            "source": source,
            "untrusted": True,
        }
        if document.get("include_topics_in_description"):
            result["available_topics"] = sorted(
                {tree["name"] for _, value in groups.values() for tree in value["decision_trees"]}
            )
        if document.get("include_groups_in_description"):
            result["available_groups"] = sorted(
                key for key, _ in groups.values() if key.casefold() != default.casefold()
            )
        if len(json.dumps(result).encode()) > 131072:
            raise ValueError("Ruleset result exceeds 128 KiB")
        run_id = current_run_id.get()
        if run_id:
            with self.studio.lock:
                run = self.studio.collaboration.owned(self.studio.run(run_id), actor)
                citations = list(run.get("citations", []))
                index = next(
                    (
                        i
                        for i, item in enumerate(citations, 1)
                        if all(
                            item.get(k) == source[k]
                            for k in ("artifact_id", "artifact_revision", "page")
                        )
                    ),
                    None,
                )
                if index is None and len(citations) < 40:
                    citations.append(source)
                    index = len(citations)
                    self.studio._update_run(run_id, citations=citations)
                if index:
                    result["citation_ref"] = f"[source:{index}]"
        return result
