"""Bring a Quick Build back in line with its workspace resources, or forget the build."""

import copy
import hashlib

from nexus.core.studio_configuration import digest
from nexus.core.studio_resource_lifecycle import (
    EXTERNAL,
    component_key,
    content_digest,
    differences,
    marker_id,
)

AGENT_FIELDS = (
    "effort",
    "tool_ids",
    "skill_ids",
    "guardrails",
    "model_id",
    "toolset_ids",
    "connector_ids",
    "timeout_seconds",
    "max_tool_calls",
    "max_model_calls",
    "connector_tools",
    "agent_card",
    "output_schema",
)


def _unsupported(key, fields):
    raise ValueError(
        f"{key} uses settings Quick Build cannot represent ({', '.join(sorted(fields))}); "
        "keep editing it in its own editor"
    )


def agent_from_document(identity, document):
    key = "agents:" + identity
    spec, metadata = document.get("spec", {}), document.get("metadata", {})
    extra = set(spec) - {"agent_type", "purpose", "provider", "model", "max_retries", *AGENT_FIELDS}
    if set(document) - {"apiVersion", "kind", "metadata", "spec"}:
        extra |= set(document) - {"apiVersion", "kind", "metadata", "spec"}
    if spec.get("agent_type", identity) != identity:
        extra.add("agent_type")
    if spec.get("max_retries", 0) != 0:
        extra.add("max_retries")
    if extra:
        _unsupported(key, extra)
    agent = {
        "id": identity,
        "name": metadata.get("name") or identity,
        "description": metadata.get("description") or metadata.get("name") or identity,
        "instructions": spec.get("purpose", ""),
        "provider": spec.get("provider"),
        "model": spec.get("model", ""),
    }
    agent.update({field: spec[field] for field in AGENT_FIELDS if field in spec})
    return agent


def workflow_from_document(identity, document):
    key = "workflows:" + identity
    metadata = document.get("metadata", {})
    extra = set(document) - {"apiVersion", "kind", "metadata", "steps", "graph"}
    if extra:
        _unsupported(key, extra)
    workflow = {
        "id": identity,
        "name": metadata.get("name") or identity,
        "description": metadata.get("description", ""),
    }
    if "graph" in document:
        workflow["graph"] = copy.deepcopy(document["graph"])
        return workflow
    steps = []
    for step in document.get("steps", []):
        extra = set(step) - {"id", "name", "agent_type", "prompt_template"}
        if extra:
            _unsupported(key, {"steps." + field for field in extra})
        steps.append(
            {
                "id": step.get("id"),
                "name": step.get("name") or step.get("id"),
                "agent_type": step.get("agent_type"),
                "prompt": step.get("prompt_template", ""),
            }
        )
    workflow["steps"] = steps
    return workflow


def _resource(value):
    return {
        field: copy.deepcopy(value[field])
        for field in ("kind", "id", "name", "description", "data")
    }


class StudioBuildMaintenance:
    def __init__(self, builder):
        self.builder = builder
        self.studio = builder.studio

    def _editable(self, identity, payload, actor):
        build = self.studio.collaboration.owned(self.builder.get(identity), actor)
        if build["status"] not in {"draft", "saved"}:
            raise ValueError("Wait for this build to finish generating")
        if identity in self.studio.active_builds:
            raise ValueError("Wait for this build to finish generating")
        if payload.get("revision") != build["revision"]:
            raise ValueError("Build changed. Reopen its latest revision.")
        return build

    def current_proposal(self, build):
        """Read the build's resources as they are now; missing ones leave the build."""
        studio, proposal, removed = self.studio, copy.deepcopy(build["proposal"]), []
        agents = []
        for agent in proposal["agents"]:
            try:
                document = studio.definition("agents", agent["id"])
            except FileNotFoundError:
                removed.append("agents:" + agent["id"])
                continue
            agents.append(agent_from_document(agent["id"], document))
        proposal["agents"] = agents

        def workflow(item):
            try:
                return workflow_from_document(
                    item["id"], studio.definition("workflows", item["id"])
                )
            except FileNotFoundError:
                removed.append("workflows:" + item["id"])
                return None

        if proposal.get("workflow"):
            proposal["workflow"] = workflow(proposal["workflow"])
        if "workflows" in proposal:
            proposal["workflows"] = [w for w in map(workflow, proposal["workflows"]) if w]
            if not proposal["workflows"]:
                del proposal["workflows"]
        resources = []
        for item in proposal.get("resources", []):
            if studio.catalog._path(item["kind"], item["id"]).exists():
                resources.append(
                    _resource(studio.catalog.get(item["kind"], item["id"], archived=True))
                )
            else:
                removed.append(component_key(item["kind"], item["id"]))
        if resources:
            proposal["resources"] = resources
        else:
            proposal.pop("resources", None)
        normalized = self.builder.normalize(
            proposal, build["id"], build["provider"], build.get("model", "")
        )
        # Refreshing records the workspace; it must never rewrite a resource.
        drift = []
        for kind, identity, document in self.builder.documents(normalized):
            if studio.definition(kind, identity) != document:
                drift.append(f"{kind}:{identity}")
        for item in normalized.get("resources", []):
            current = _resource(studio.catalog.get(item["kind"], item["id"], archived=True))
            if content_digest(item["kind"], current) != content_digest(item["kind"], item):
                drift.append(component_key(item["kind"], item["id"]))
        if drift:
            raise ValueError(
                "Quick Build would store these resources differently: "
                + ", ".join(drift)
                + ". Keep editing them in their own editors"
            )
        return normalized, removed

    def changes(self, build, proposal, removed):
        before = {
            f"{kind}:{identity}": document
            for kind, identity, document in self.builder.documents(build["proposal"])
        }
        before.update(
            {component_key(i["kind"], i["id"]): i for i in build["proposal"].get("resources", [])}
        )
        after = {
            f"{kind}:{identity}": document
            for kind, identity, document in self.builder.documents(proposal)
        }
        after.update({component_key(i["kind"], i["id"]): i for i in proposal.get("resources", [])})
        result = [
            {"resource": key, "action": "update", "paths": differences(before[key], value)}
            for key, value in sorted(after.items())
            if key in before and before[key] != value
        ]
        result += [{"resource": key, "action": "remove", "paths": []} for key in sorted(removed)]
        return result

    def refresh(self, identity, payload, actor=None):
        """Preview, then confirm, replacing the build proposal with the workspace contents.

        Confirming re-pins the build to the current files. Changed resources become
        inactive at their new content, so they run again only after activation review.
        """
        if self.studio.team:
            self.studio.team.authorize(actor, "/api/builds/" + identity + "/apply")
        with self.studio.lock:
            build = self._editable(identity, payload, actor)
            proposal, removed = self.current_proposal(build)
            changes = self.changes(build, proposal, removed)
            preview = {
                "build_id": identity,
                "revision": build["revision"],
                "digest": digest({"proposal": proposal, "removed": removed}),
                "changes": changes,
            }
            if payload.get("digest") is None:
                return preview
            if payload["digest"] != preview["digest"]:
                raise ValueError("Workspace changed since the preview; review the refresh again")
            from nexus.core.studio import _now

            updated = {
                **build,
                "proposal": proposal,
                "revision": build["revision"] + 1,
                "updated_at": _now(),
            }
            keys = [f"{kind}:{rid}" for kind, rid, _ in self.builder.documents(proposal)]
            if build["status"] == "draft":
                updated.update(
                    base_definitions={
                        key: self.studio.definition(*key.split(":", 1)) for key in keys
                    },
                    base_checksums={
                        key: hashlib.sha256(
                            (
                                self.studio.root
                                / key.split(":", 1)[0]
                                / f"{key.split(':', 1)[1]}.yaml"
                            ).read_bytes()
                        ).hexdigest()
                        for key in keys
                    },
                    base_resources={
                        component_key(i["kind"], i["id"]): self.studio.catalog.get(
                            i["kind"], i["id"]
                        )
                        for i in proposal.get("resources", [])
                    },
                )
            else:
                updated["files"] = [
                    f"{key.split(':', 1)[0]}/{key.split(':', 1)[1]}.yaml" for key in keys
                ]
            deactivated = []
            values = {key: self.studio.definition(*key.split(":", 1)) for key in keys}
            values.update(
                {component_key(i["kind"], i["id"]): i for i in proposal.get("resources", [])}
            )
            for key, value in values.items():
                path = self.studio._path("resource-lifecycle", marker_id(key))
                if not path.exists():
                    continue
                marker = self.studio._read(path)
                current = content_digest(key.split(":", 1)[0], value)
                if marker.get("build_id") != identity or marker.get("content_digest") == current:
                    continue
                if marker.get("active"):
                    deactivated.append(key)
                self.studio._write(
                    path,
                    {
                        **marker,
                        "active": False,
                        "content_digest": current,
                        "reviewed_revision": updated["revision"],
                    },
                )
                if key.split(":", 1)[0] in EXTERNAL and value["data"].get("enabled"):
                    # A changed connection or schedule stays off until activation review.
                    record = self.studio.catalog.get(*key.split(":", 1), archived=True)
                    self.studio._write(
                        self.studio.catalog._path(*key.split(":", 1)),
                        {**record, "data": {**record["data"], "enabled": False}},
                    )
            if deactivated or any(c["action"] == "update" for c in changes):
                updated["lifecycle_status"] = "inactive"
            self.studio._write(self.studio._path("builds", identity), updated)
            return {**preview, "build": updated, "deactivated": deactivated}

    def delete(self, identity, payload, actor=None):
        """Delete the build record only; its resources stay as ordinary workspace items."""
        with self.studio.lock:
            build = self._editable(identity, payload, actor)
            managed = [
                r
                for r in self.studio.records("resource-lifecycle", limit=None)
                if r.get("build_id") == identity
            ]
            if managed or build.get("release_id"):
                # Saved resources leave review-gated management, which is an owner decision.
                self.studio.configuration.authorize(actor)
            if any(
                run.get("kind") == "build"
                and run.get("build_id") == identity
                and run["status"] in {"queued", "running"}
                for run in self.studio.records("runs", limit=None)
            ):
                raise ValueError("Wait for this build to finish generating")
            for marker in managed:
                self.studio._path("resource-lifecycle", marker_id(marker["key"])).unlink(
                    missing_ok=True
                )
            self.studio._path("builds", identity).unlink(missing_ok=True)
            return {
                "id": identity,
                "deleted": True,
                "kept": [marker["key"] for marker in managed]
                or [f"{kind}:{rid}" for kind, rid, _ in self.builder.documents(build["proposal"])],
            }
