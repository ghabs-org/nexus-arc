"""Reviewed resource transitions shared by Studio builds and framework consumers."""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
import uuid

from nexus.core.studio_configuration import digest
from nexus.core.studio_knowledge import owner

EXTERNAL = {"connectors", "entrypoints", "schedules", "externalagents"}


def component_key(kind, identity):
    from nexus.core.studio import _identity
    from nexus.core.studio_catalog import KINDS

    if kind not in {"agents", "workflows", *KINDS}:
        raise ValueError("Unknown component collection")
    return kind + ":" + _identity(identity)


def marker_id(key):
    return hashlib.sha256(key.encode()).hexdigest()


def content_digest(kind, value):
    if kind in {"agents", "workflows"}:
        return digest(value)
    data = {key: item for key, item in value["data"].items() if key != "enabled"}
    return digest(
        {
            "kind": kind,
            "id": value["id"],
            "name": value["name"],
            "description": value.get("description", ""),
            "data": data,
        }
    )


def document_keys(document):
    return [component_key(i["collection"], i["id"]) for i in document["definitions"]] + [
        component_key(i["kind"], i["id"]) for i in document["resources"]
    ]


def dependencies(kind, document):
    """Read only typed reference fields; instructions/URLs never become references."""
    refs = set()
    data = document.get("spec", {}) if kind == "agents" else document.get("data", {})
    if kind == "experiments":
        refs.add(component_key("evaluations", data["evaluation_id"]))
    if kind == "agents":
        for field, collection in (
            ("skill_ids", "skills"),
            ("toolset_ids", "toolsets"),
            ("connector_ids", "connectors"),
        ):
            refs.update(component_key(collection, value) for value in data.get(field, []))
        if data.get("model_id"):
            refs.add(component_key("models", data["model_id"]))
    if kind in {"schedules", "entrypoints", "evaluations"}:
        refs.add(
            component_key(
                "agents" if data["target_kind"] == "agent" else "workflows", data["target_id"]
            )
        )
    if kind == "workflows":
        refs.update(
            component_key("agents", item["agent_type"]) for item in document.get("steps", [])
        )

        def walk(value):
            if not isinstance(value, dict):
                return
            for node in value.get("nodes", []):
                if node.get("agent_id"):
                    refs.add(component_key("agents", node["agent_id"]))
                if node.get("workflow_id"):
                    refs.add(component_key("workflows", node["workflow_id"]))
                if node.get("connector_id"):
                    refs.add(component_key("connectors", node["connector_id"]))
                if str(node.get("tool_id", "")).startswith("connector:"):
                    refs.add(component_key("connectors", node["tool_id"].rsplit(":", 1)[-1]))
                if isinstance(node.get("decision"), dict) and node["decision"].get("decision_model_id"):
                    refs.add(component_key("decisionmodels", node["decision"]["decision_model_id"]))
                walk(node.get("body"))
            for field in ("on_success", "on_error"):
                walk(value.get(field))
            for handler in value.get("interop_exit_handlers", []):
                walk(handler["body"])

        walk(document.get("graph"))
    # Connector tool IDs have a workspace prefix; the final segment is its ID.
    for tool in data.get("tools", []) + data.get("tool_ids", []):
        if tool.startswith("connector:"):
            refs.add(component_key("connectors", tool.rsplit(":", 1)[-1]))
    return sorted(refs)


def differences(before, after, path=""):
    """Bounded readable changed paths; no credential values enter a change summary."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            result.extend(differences(before.get(key), after.get(key), path + "/" + key))
        return result[:200]
    return [path or "/"]


class StudioResourceLifecycle:
    def __init__(self, studio):
        self.studio = studio

    def marker(self, key):
        path = self.studio._path("resource-lifecycle", marker_id(key))
        return self.studio._read(path) if path.exists() else None

    def current(self, key):
        kind, identity = key.split(":", 1)
        if kind in {"agents", "workflows"}:
            return self.studio.definition(kind, identity)
        return self.studio.catalog.get(kind, identity)

    def document(self, keys):
        result = {"format": "nexus-studio-config/v1", "definitions": [], "resources": []}
        for key in sorted(set(keys)):
            kind, identity = key.split(":", 1)
            value = self.current(key)
            if kind in {"agents", "workflows"}:
                result["definitions"].append(
                    {"collection": kind, "id": identity, "document": value}
                )
            else:
                result["resources"].append(
                    {field: value[field] for field in ("kind", "id", "name", "description", "data")}
                )
        return result

    def graph(self, document):
        known = {
            component_key(i["collection"], i["id"]): i["document"] for i in document["definitions"]
        }
        known.update({component_key(i["kind"], i["id"]): i for i in document["resources"]})
        managed = set(known)
        edges, errors, visiting, visited = [], [], set(), set()

        def visit(key, depth=0):
            if key in visiting:
                errors.append({"component": key, "reason": "dependency_cycle"})
                return
            if key in visited:
                return
            if depth > 32 or len(visited | visiting) >= 1000:
                raise ValueError("Component dependency graph exceeds its bound")
            visiting.add(key)
            try:
                value = known[key] if key in known else self.current(key)
            except (FileNotFoundError, ValueError):
                errors.append({"component": key, "reason": "missing_dependency"})
            else:
                known[key] = value
                for target in dependencies(key.split(":", 1)[0], value):
                    edges.append({"source": key, "target": target})
                    visit(target, depth + 1)
            visiting.remove(key)
            visited.add(key)

        for key in sorted(managed):
            visit(key)
        return {"edges": edges, "errors": errors, "values": known, "managed": managed}

    def readiness(self, key, value, actor, *, probe_workers=False, worker_cache=None):
        kind = key.split(":", 1)[0]
        data = value.get("spec", {}) if kind == "agents" else value.get("data", {})
        refs = set()

        def scan(item):
            if isinstance(item, dict):
                for field, child in item.items():
                    if field.endswith("_env") and isinstance(child, str) and child:
                        refs.add(child)
                    else:
                        scan(child)
            elif isinstance(item, list):
                for child in item:
                    scan(child)

        scan(data)
        if kind == "connectors" and data.get("type") == "mcp":
            refs.update(data.get("env", {}).values())
        environment = os.environ
        execution_denied = False
        if self.studio.team:
            try:
                self.studio.team.assert_execution(owner(actor))
                environment, error = self.studio.team.environment_resolver(owner(actor))
            except PermissionError:
                execution_denied, environment, error = True, {}, True
            if error:
                environment = {}
        missing = sorted(name for name in refs if not environment.get(name))
        issues = ["missing_credentials"] if missing else []
        if kind == "connectors" and data.get("auth", {}).get("type") == "oauth":
            try:
                if not self.studio.oauth.status(value["id"], actor)["connected"]:
                    issues.append("delegated_account_required")
            except (PermissionError, ValueError):
                issues.append("delegated_account_required")
        if execution_denied:
            issues.append("execution_access_revoked")
        provider = (
            data.get("provider")
            if kind == "models" or (kind == "agents" and not data.get("model_id"))
            else None
        )
        if provider and not self.studio._custom_provider_factory:
            inventory = self.studio.providers()
            if self.studio.team:
                inventory = self.studio.team.providers(inventory, owner(actor))
            if not any(item["id"] == provider and item["configured"] for item in inventory):
                issues.append("provider_setup_required")
        if (
            kind == "connectors"
            and data.get("type") == "mcp"
            and data.get("transport", "stdio") == "stdio"
        ):
            if not shutil.which(data["command"], path=environment.get("PATH", os.defpath)):
                issues.append("mcp_executable_missing")
        if kind == "connectors" and data.get("type") == "sqlite":
            path = self.studio.root / data["path"]
            if not path.resolve().is_relative_to(self.studio.root) or not path.is_file():
                issues.append("database_file_missing")
        from nexus.core.studio_activation_setup import local_prerequisites

        prerequisites = local_prerequisites(
            self.studio,
            key,
            value,
            probe_workers=probe_workers and not execution_denied,
            worker_cache=worker_cache,
        )
        issues.extend(prerequisites["issues"])
        marker = self.marker(key)
        if marker and marker.get("active") is False:
            issues.append("inactive")
        if (
            marker
            and marker.get("content_digest")
            and marker["content_digest"] != content_digest(kind, value)
        ):
            issues.append("unreviewed_changes")
        return {
            "ready": not issues,
            "issues": issues,
            "missing_environment": missing,
            "prerequisite_checks": prerequisites["checks"],
            "prerequisite_fingerprint": prerequisites["fingerprint"],
            "live_health": "not_checked",
        }

    def inspect(self, document, actor=None, *, probe_workers=False):
        graph = self.graph(document)
        components = []
        worker_cache = {}
        for key, value in sorted(graph["values"].items()):
            readiness = self.readiness(
                key, value, actor, probe_workers=probe_workers, worker_cache=worker_cache
            )
            kind = key.split(":", 1)[0]
            data = value.get("data", {})
            components.append(
                {
                    "key": key,
                    "name": value.get("name") or value.get("metadata", {}).get("name", key),
                    "managed": key in graph["managed"],
                    "revision": digest(value),
                    "enabled": data.get("enabled") if kind in EXTERNAL else None,
                    "status": "inactive"
                    if "inactive" in readiness["issues"]
                    else "needs_setup"
                    if not readiness["ready"]
                    else "disabled"
                    if kind in EXTERNAL and not data.get("enabled")
                    else "available",
                    **readiness,
                }
            )
        return {
            "components": components,
            "dependencies": graph["edges"],
            "errors": graph["errors"],
            "live_health": "not_checked",
        }

    def build_document(self, build):
        return {
            "format": "nexus-studio-config/v1",
            "definitions": [
                {"collection": kind, "id": identity, "document": doc}
                for kind, identity, doc in self.studio.builder.documents(build["proposal"])
            ],
            "resources": build["proposal"].get("resources", []),
        }

    def execution_actor(self, build):
        if not self.studio.team:
            return None
        identity = build.get("owner_id")
        return {"nexus_id": identity, "role": self.studio.team.bindings.get(identity)}

    def inspect_build(self, identity, actor=None):
        build = self.studio.collaboration.owned(self.studio.builder.get(identity), actor)
        document = self.build_document(build)
        if build["status"] == "saved":
            document = self.document(document_keys(document))
        result = self.inspect(document, self.execution_actor(build))
        if build["status"] == "saved":
            from nexus.core.studio_resource_health import StudioResourceHealth

            health = StudioResourceHealth(self)
            for component in result["components"]:
                component["health"] = health.inspect(build, component)
                component["live_health"] = component["health"]["status"]
            result["health_summary"] = {
                "supported": sum(1 for c in result["components"] if c["health"]["supported"]),
                "checked": sum(1 for c in result["components"] if c["health"]["result"]),
                "passed": sum(1 for c in result["components"] if c["health"]["status"] == "passed"),
                "failed": sum(1 for c in result["components"] if c["health"]["status"] == "failed"),
                "stale": sum(1 for c in result["components"] if c["health"]["status"] == "stale"),
            }
            if any(c["health"]["result"] for c in result["components"]):
                result["live_health"] = "partial"
        releases = [
            r
            for r in self.studio.records("configuration-releases", limit=None)
            if r.get("metadata", {}).get("build_id") == identity
        ]
        result.update(
            build_id=identity,
            revision=build["revision"],
            status=build["status"],
            release_id=build.get("release_id"),
            releases=[
                {
                    "id": r["id"],
                    "revision": r["metadata"]["revision"],
                    "name": r["metadata"]["proposal"]["name"],
                    "action": r["metadata"].get("action", "save"),
                    "created_at": r.get("created_at"),
                }
                for r in sorted(
                    releases, key=lambda r: (r.get("created_at", ""), r["id"]), reverse=True
                )
            ],
        )
        return result

    def plan_build(self, identity, payload, actor=None):
        self.studio.configuration.authorize(actor)
        with self.studio.lock:
            build = self.studio.collaboration.owned(self.studio.builder.get(identity), actor)
            if build["status"] != "saved" or payload.get("revision") != build["revision"]:
                raise ValueError("Review the latest saved build revision")
            action = payload.get("action")
            if action not in {"activate", "deactivate", "rollback"}:
                raise ValueError("Choose activate, deactivate or rollback")
            original = self.build_document(build)
            keys = document_keys(original)
            document = self.document(keys)
            after = copy.deepcopy(build)
            if action == "rollback":
                release = self.studio._read(
                    self.studio._path("configuration-releases", payload.get("release"))
                )
                if release.get("metadata", {}).get("build_id") != identity or release[
                    "owner_id"
                ] != owner(actor):
                    raise PermissionError("Choose a release belonging to this build/account")
                document = copy.deepcopy(release["document"])
                after.update(
                    proposal=release["metadata"]["proposal"], revision=build["revision"] + 1
                )
                # Removed components remain recoverable, but may never remain scheduled/exposed.
                target = set(document_keys(document))
                for item in self.document(keys)["resources"]:
                    if (
                        component_key(item["kind"], item["id"]) not in target
                        and item["kind"] in EXTERNAL
                    ):
                        item["data"]["enabled"] = False
                        document["resources"].append(item)
            enable = payload.get("enable", [])
            if (
                not isinstance(enable, list)
                or set(enable) - set(document_keys(document))
                or any(key.split(":", 1)[0] not in EXTERNAL for key in enable)
            ):
                raise ValueError("Select credential-dependent components from this reviewed build")
            if action != "activate" and enable:
                raise ValueError("Only activation can explicitly enable resources")
            normalized = self.studio.configuration.normalize(document)
            details = self.inspect(
                document, self.execution_actor(build), probe_workers=action == "activate"
            )
            errors = list(details["errors"])
            records = {"builds:" + identity: after}
            for key in keys + [k for k in document_keys(normalized) if k not in keys]:
                active = action == "activate" and key in document_keys(normalized)
                records["resource-lifecycle:" + marker_id(key)] = {
                    "format": "nexus-resource-lifecycle/v1",
                    "key": key,
                    "active": active,
                    "build_id": identity,
                    "reviewed_revision": after["revision"],
                }
                kind = key.split(":", 1)[0]
                expected = next(
                    (
                        i["document"]
                        for i in normalized["definitions"]
                        if component_key(i["collection"], i["id"]) == key
                    ),
                    None,
                )
                if expected is None:
                    expected = next(
                        (
                            i
                            for i in normalized["resources"]
                            if component_key(i["kind"], i["id"]) == key
                        ),
                        None,
                    )
                if expected is None:
                    expected = self.current(key)
                records["resource-lifecycle:" + marker_id(key)]["content_digest"] = content_digest(
                    kind, expected
                )
            for item in normalized["resources"]:
                key = component_key(item["kind"], item["id"])
                if item["kind"] in EXTERNAL:
                    item["data"]["enabled"] = action == "activate" and (
                        key in enable or self.current(key)["data"].get("enabled", False)
                    )
            if action == "activate":
                for item in details["components"]:
                    blocking = set(item["issues"])
                    if item["managed"]:
                        blocking.discard("inactive")
                    if blocking:
                        errors.append(
                            {"component": item["key"], "reason": ",".join(sorted(blocking))}
                        )
                    if (
                        item["key"].split(":", 1)[0] in EXTERNAL
                        and not item["enabled"]
                        and item["key"] not in enable
                    ):
                        errors.append(
                            {"component": item["key"], "reason": "explicit_enable_required"}
                        )
            before = self.studio.configuration.current(normalized)
            marker_base = {
                key: self.marker(key)
                for key in set(keys) | {item["key"] for item in details["components"]}
            }
            plan = {
                "id": uuid.uuid4().hex,
                "owner_id": owner(actor),
                "build_id": identity,
                "revision": build["revision"],
                "action": action,
                "document": normalized,
                "base": before,
                "record_updates": records,
                "build_base": build,
                "marker_base": marker_base,
                "dependency_revisions": {
                    item["key"]: item["revision"]
                    for item in details["components"]
                    if not item["managed"]
                },
                "prerequisite_fingerprints": {
                    item["key"]: item["prerequisite_fingerprint"] for item in details["components"]
                },
                "errors": errors,
                "changes": [],
            }
            for key in document_keys(normalized):
                current = self.current(key)
                proposed = next(
                    (
                        i["document"]
                        for i in normalized["definitions"]
                        if component_key(i["collection"], i["id"]) == key
                    ),
                    None,
                )
                if proposed is None:
                    proposed = next(
                        i
                        for i in normalized["resources"]
                        if component_key(i["kind"], i["id"]) == key
                    )
                    current = {
                        field: current[field]
                        for field in ("kind", "id", "name", "description", "data")
                    }
                plan["changes"].append(
                    {
                        "resource": key,
                        "action": action,
                        "paths": differences(current, proposed),
                        "active": action == "activate",
                    }
                )
            plan["digest"] = digest(plan)
            self.studio._write(self.studio._path("resource-plans", plan["id"]), plan)
            return {
                key: plan[key]
                for key in ("id", "digest", "build_id", "revision", "action", "errors", "changes")
            } | {"dependencies": details["dependencies"]}

    def transition_build(self, identity, payload, actor=None):
        self.studio.configuration.authorize(actor)
        with self.studio.lock:
            build = self.studio.collaboration.owned(self.studio.builder.get(identity), actor)
            plan = self.studio._read(self.studio._path("resource-plans", payload.get("plan_id")))
            if (
                plan["build_id"] != identity
                or plan["owner_id"] != owner(actor)
                or payload.get("digest") != plan["digest"]
            ):
                raise PermissionError("Review this account's build plan before applying")
            if plan.get("result"):
                return plan["result"]
            if plan["errors"]:
                raise ValueError("Resolve every dependency/readiness error before activation")
            if (
                build != plan["build_base"]
                or self.studio.configuration.current(plan["document"]) != plan["base"]
                or any(self.marker(key) != value for key, value in plan["marker_base"].items())
            ):
                raise ValueError("Workspace or build changed; review a fresh transition plan")
            for key, revision in plan.get("dependency_revisions", {}).items():
                try:
                    matches = digest(self.current(key)) == revision
                except (FileNotFoundError, ValueError):
                    matches = False
                if not matches:
                    raise ValueError("Component dependency changed; review a fresh transition plan")
            if plan["action"] == "activate":
                # Credential availability may change after review. Never expose values.
                # Reject changed local setup before starting any worker probe.
                for item in self.inspect(plan["document"], self.execution_actor(build))[
                    "components"
                ]:
                    expected = plan.get("prerequisite_fingerprints", {}).get(item["key"])
                    if expected != item["prerequisite_fingerprint"]:
                        raise ValueError(
                            "Component software or worker setup changed; review a fresh plan"
                        )
                for item in self.inspect(
                    plan["document"], self.execution_actor(build), probe_workers=True
                )["components"]:
                    expected = plan.get("prerequisite_fingerprints", {}).get(item["key"])
                    if expected != item["prerequisite_fingerprint"]:
                        raise ValueError(
                            "Component software or worker setup changed; review a fresh plan"
                        )
                    blocking = set(item["issues"])
                    if item["managed"]:
                        blocking.discard("inactive")
                    if blocking:
                        raise ValueError("Component prerequisites changed; review a fresh plan")
            enabled = [
                component_key(i["kind"], i["id"])
                for i in plan["document"]["resources"]
                if i["data"].get("enabled")
            ]
            updated = copy.deepcopy(plan["record_updates"])
            state = updated["builds:" + identity]
            state["lifecycle_status"] = "active" if plan["action"] == "activate" else "inactive"
            configuration = self.studio.configuration._plan(
                plan["document"],
                actor,
                records=updated,
                metadata={
                    "build_id": identity,
                    "revision": state["revision"],
                    "proposal": state["proposal"],
                    "action": plan["action"],
                },
                enabled=enabled,
            )
            state["release_id"] = configuration["id"]
            # Result receipt and build state share the recoverable release commit.
            result = {
                "id": identity,
                "revision": state["revision"],
                "status": state["lifecycle_status"],
                "release_id": configuration["id"],
                "action": plan["action"],
            }
            plan["result"] = result
            stored_path = self.studio._path("configuration-plans", configuration["id"])
            stored = self.studio._read(stored_path)
            stored["record_updates"]["builds:" + identity] = state
            stored["record_updates"]["resource-plans:" + plan["id"]] = plan
            stored["record_base"]["resource-plans:" + plan["id"]] = {
                key: value for key, value in plan.items() if key != "result"
            }
            self.studio._write(stored_path, stored)
            self.studio.configuration._apply(configuration["id"], configuration["digest"], actor)
            return result

    def assert_launch(self, key):
        seen = set()
        revisions = {}

        def visit(current):
            if current in seen:
                return
            seen.add(current)
            marker = self.marker(current)
            if marker and not marker.get("active"):
                raise ValueError(
                    "Resource is inactive. Review and activate its saved build before running"
                )
            value = self.current(current)
            revisions[current] = {"value": digest(value), "lifecycle": marker}
            if (
                marker
                and marker.get("content_digest")
                and marker["content_digest"] != content_digest(current.split(":", 1)[0], value)
            ):
                raise ValueError(
                    "Resource changed since activation. Review and activate its current revision"
                )
            for child in dependencies(current.split(":", 1)[0], value):
                visit(child)

        visit(key)
        return digest(revisions)
