"""Explicit read-only checks; configuration inspection never contacts a provider."""

from datetime import UTC, datetime

from nexus.core.studio_configuration import digest


def probe_action(key, value):
    if key.startswith("externalagents:"):
        return "agent_card"
    if not key.startswith("connectors:"):
        return None
    data = value.get("data", {})
    if data.get("type") == "openapi" and data.get("health_operation"):
        return "read" if "read" in data.get("actions", []) else None
    action = {
        "sqlite": "select_one",
        "http": "fetch",
        "sql": "inspect_schema",
        "mcp": "discover",
        "github": "list_issues",
        "gitlab": "list_issues",
        "mongodb": "inspect_schema",
        "powerbi": "read",
        "teams": "status",
    }.get(data.get("type"))
    if data.get("type") not in {"sqlite", "http"} and action not in data.get("actions", []):
        return None
    return action


class StudioResourceHealth:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle
        self.studio = lifecycle.studio

    def path(self, build_id, key):
        return self.studio._path("resource-health", digest([build_id, key]))

    def inspect(self, build, component):
        key = component["key"]
        value = self.lifecycle.current(key)
        action = probe_action(key, value) if component["managed"] else None
        path = self.path(build["id"], key)
        record = self.studio._read(path) if path.exists() else None
        if record and (
            record["release_id"] != build.get("release_id")
            or record["revision"] != build["revision"]
            or record["component_revision"] != digest(value)
            or record["marker"] != self.lifecycle.marker(key)
        ):
            record = {**record, "status": "stale"}
        return {
            "supported": action is not None,
            "action": action,
            "result": record,
            "status": record["status"] if record else "not_checked",
        }

    def check(self, identity, payload, actor=None):
        self.studio.configuration.authorize(actor)
        with self.studio.lock:
            build = self.studio.collaboration.owned(self.studio.builder.get(identity), actor)
            if (
                build["status"] != "saved"
                or payload.get("revision") != build["revision"]
                or not build.get("release_id")
                or payload.get("release_id") != build["release_id"]
            ):
                raise ValueError("Choose the latest saved build release before checking health")
            key = payload.get("component")
            from nexus.core.studio_resource_lifecycle import document_keys

            if key not in document_keys(self.lifecycle.build_document(build)):
                raise ValueError("Choose a managed component from this build")
            value = self.lifecycle.current(key)
            action = probe_action(key, value)
            if not action:
                raise ValueError("This component has no supported read-only connectivity check")
            executor = self.lifecycle.execution_actor(build)
            if self.studio.team:
                self.studio.team.assert_execution(build["owner_id"])
            readiness = self.lifecycle.readiness(key, value, executor)
            if set(readiness["issues"]) - {"inactive"}:
                raise ValueError("Resolve component prerequisites before checking health")
            marker = self.lifecycle.marker(key)
            revision = digest(value)
            arguments = {"query": "SELECT 1"} if action == "select_one" else {"action": action}
            if value["data"].get("type") == "openapi":
                # Server-reviewed selection only; caller cannot supply another
                # operation or arguments through the health request.
                arguments["arguments"] = {"operation_id": value["data"]["health_operation"]}
        # No workspace lock during I/O. Adapters validate and use the reviewed snapshot;
        # publication below rejects concurrent configuration/release changes as stale.
        status, reason = "passed", None
        try:
            if action == "agent_card":
                self.studio.external.discover(
                    key.split(":", 1)[1],
                    build.get("owner_id"),
                    probe=True,
                    expected_data=value["data"],
                )
            else:
                self.studio.operations.call_connector(
                    key.split(":", 1)[1],
                    arguments,
                    build.get("owner_id"),
                    probe=True,
                    expected_data=value["data"],
                )
        except Exception:
            status, reason = "failed", "connectivity_check_failed"
        record = {
            "format": "nexus-resource-health/v1",
            "build_id": identity,
            "owner_id": build.get("owner_id"),
            "release_id": build["release_id"],
            "revision": build["revision"],
            "component": key,
            "component_revision": revision,
            "marker": marker,
            "action": action,
            "status": status,
            "reason": reason,
            "checked_at": datetime.now(UTC).isoformat(),
        }
        with self.studio.lock:
            latest = self.studio.builder.get(identity)
            try:
                unchanged = digest(self.lifecycle.current(key)) == revision
            except (ValueError, FileNotFoundError):
                unchanged = False
            if latest != build or not unchanged or self.lifecycle.marker(key) != marker:
                record["status"] = "stale"
            if self.studio.team:
                try:
                    self.studio.team.assert_execution(build["owner_id"])
                except PermissionError:
                    record.update(status="failed", reason="execution_access_revoked")
            self.studio._write(self.path(identity, key), record)
        return record
