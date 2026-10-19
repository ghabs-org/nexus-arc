"""AI-assisted, reviewable agent/workflow proposals for local Nexus consumers."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from nexus.adapters.ai.base import ExecutionContext
from nexus.adapters.registry import AdapterRegistry

BUILD_PROVIDERS = ("codex", "claude-code", "openai", "claude")
AGENT_PROVIDERS = (*BUILD_PROVIDERS, "copilot", "gemini", "opencode")


def build_provider(name: str):
    """Keep builder CLI settings separate from agents that execute saved workflows."""
    options = {}
    if name == "codex":
        options["extra_args"] = [
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
        ]
    elif name == "claude-code":
        options["extra_args"] = [
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
        ]
    if name not in BUILD_PROVIDERS:
        raise ValueError("Quick Build supports Codex, Claude Code, OpenAI API and Anthropic API")
    return AdapterRegistry().create_ai(name, **options)


def _text(value: Any, label: str, limit: int = 16000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be nonempty text, up to {limit} characters")
    return value.strip()


def _keys(value: Any, allowed: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if unknown := set(value) - allowed:
        raise ValueError(f"Unsupported {label} fields: {', '.join(sorted(unknown))}")
    return value


class QuickBuilder:
    """Generate drafts without saving definitions; apply only a reviewed revision.

    The host's provider factory is injectable for embedders. It must implement
    a generation-only policy comparable to ``build_provider`` when untrusted
    model output must not execute tools. Normal workflow runs retain the host's
    existing provider policy.
    """

    def __init__(self, studio, provider_factory=build_provider):
        self.studio = studio
        self.provider_factory = provider_factory

    def get(self, identity: str) -> dict:
        return self.studio._read(self.studio._path("builds", identity))

    def validate_request(self, payload: dict) -> None:
        _text(payload.get("task"), "Build request")
        if payload.get("provider", "codex") not in BUILD_PROVIDERS:
            raise ValueError(
                "Choose Codex, Claude Code, OpenAI API or Anthropic API for Quick Build"
            )
        if payload.get("model") is not None and not isinstance(payload["model"], str):
            raise ValueError("Model must be text")
        if payload.get("revision") is not None and type(payload["revision"]) is not int:
            raise ValueError("Revision must be an integer")
        path = self.studio._path("builds", payload["build_id"])
        if path.exists():
            draft = self.get(payload["build_id"])
            if draft["status"] == "saved":
                raise ValueError("This build is saved. Start a new build to create more resources.")
            if payload.get("revision") != draft["revision"]:
                raise ValueError("Build changed. Reopen its latest revision before refining.")
            if "proposal" in payload:
                self.normalize(
                    payload["proposal"], payload["build_id"], draft["provider"], draft["model"]
                )
        elif payload.get("revision") is not None:
            raise FileNotFoundError("Build draft not found")

    def normalize(self, proposal: Any, identity: str, provider: str, model: str = "") -> dict:
        """Validate the supported subset before either preview or save."""
        proposal = _keys(
            proposal,
            {"name", "summary", "assumptions", "agents", "workflow", "workflows", "resources"},
            "proposal",
        )
        name = _text(proposal.get("name"), "Build name", 160)
        summary = _text(proposal.get("summary"), "Summary", 4000)
        assumptions = proposal.get("assumptions", [])
        if not isinstance(assumptions, list) or len(assumptions) > 12:
            raise ValueError("Assumptions must be a list of up to 12 items")
        assumptions = [_text(item, "Assumption", 1000) for item in assumptions]
        agents = proposal.get("agents")
        if not isinstance(agents, list) or not 0 <= len(agents) <= 8:
            raise ValueError("A build needs between 1 and 8 agents")
        prefix = f"qb-{identity[:8]}-"

        def resource_id(value):
            value = _text(value, "Resource ID", 96)
            local = value[len(prefix) :] if value.startswith(prefix) else value
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", local):
                raise ValueError(
                    "IDs must use lowercase letters, digits, hyphens or underscores (max 64)"
                )
            return value if value.startswith(prefix) else prefix + value

        proposed_resources = proposal.get("resources", [])
        if not isinstance(proposed_resources, list) or len(proposed_resources) > 20:
            raise ValueError("A build supports up to twenty reusable resources")
        proposed_ids = {}
        for item in proposed_resources:
            if not isinstance(item, dict):
                raise ValueError("Resource must be an object")
            proposed_ids.setdefault(item.get("kind"), set()).add(resource_id(item.get("id")))

        def reference(kind, value):
            candidate = resource_id(value)
            return candidate if candidate in proposed_ids.get(kind, set()) else value

        normalized_agents = []
        agent_ids = set()
        for agent in agents:
            agent = _keys(
                agent,
                {
                    "id",
                    "name",
                    "description",
                    "instructions",
                    "provider",
                    "model",
                    "tool_ids",
                    "skill_ids",
                    "guardrails",
                    "timeout_seconds",
                    "max_tool_calls",
                    "max_model_calls",
                    "connector_tools",
                    "agent_card",
                    "output_schema",
                    "toolset_ids",
                    "connector_ids",
                    "model_id",
                },
                "agent",
            )
            agent_id = resource_id(agent.get("id"))
            if agent_id in agent_ids:
                raise ValueError("Agent IDs must be unique")
            agent_ids.add(agent_id)
            agent_provider = agent.get("provider") or provider
            if agent_provider not in AGENT_PROVIDERS:
                raise ValueError("Unknown agent provider")
            agent_model = agent.get("model", model if agent_provider == provider else "")
            if not isinstance(agent_model, str) or len(agent_model) > 200:
                raise ValueError("Agent model must be text, up to 200 characters")
            normalized_agents.append(
                {
                    "id": agent_id,
                    "name": _text(agent.get("name"), "Agent name", 160),
                    "description": _text(agent.get("description"), "Agent description", 2000),
                    "instructions": _text(agent.get("instructions"), "Agent instructions"),
                    "provider": agent_provider,
                    "model": agent_model.strip(),
                }
            )
            for field in ("tool_ids", "skill_ids"):
                if agent.get(field):
                    values = agent[field]
                    if (
                        not isinstance(values, list)
                        or len(values) > 30
                        or any(not isinstance(v, str) for v in values)
                    ):
                        raise ValueError(f"{field} must contain at most thirty resource IDs")
                    if field == "tool_ids":
                        from nexus.tools import tool_registry

                        if any(
                            not tool_registry.get(v)
                            or self.studio.team
                            and not self.studio.team.tool_allowed(v, self.studio)
                            for v in values
                        ):
                            raise ValueError("Select existing tools enabled in this workspace")
                    else:
                        values = [reference("skills", v) for v in values]
                        self.studio.catalog.context(
                            {
                                "skill_ids": [
                                    v for v in values if v not in proposed_ids.get("skills", set())
                                ]
                            }
                        )
                    normalized_agents[-1][field] = values
            for field, kind in (("toolset_ids", "toolsets"), ("connector_ids", "connectors")):
                if field in agent:
                    if not isinstance(agent[field], list):
                        raise ValueError(field + " must be a list")
                    normalized_agents[-1][field] = [reference(kind, v) for v in agent[field]]
            if agent.get("model_id"):
                normalized_agents[-1]["model_id"] = reference("models", agent["model_id"])
            if "connector_tools" in agent:
                permissions = agent["connector_tools"]
                if not isinstance(permissions, dict):
                    raise ValueError("Connector tool permissions must be an object")
                normalized_agents[-1]["connector_tools"] = {
                    reference("connectors", key): value for key, value in permissions.items()
                }
            for field in (
                "timeout_seconds",
                "max_tool_calls",
                "max_model_calls",
                "output_schema",
                "agent_card",
            ):
                if field in agent:
                    normalized_agents[-1][field] = agent[field]
            if agent.get("guardrails"):
                from nexus.core.studio_observability import validate_guardrails

                normalized_agents[-1]["guardrails"] = validate_guardrails(agent["guardrails"])
        resources = proposal.get("resources", [])
        if not isinstance(resources, list) or len(resources) > 20:
            raise ValueError("A build supports up to twenty reusable resources")
        from nexus.core.studio_build_workflows import normalize_workflow, workflow_shape

        workflow = proposal.get("workflow")
        children = proposal.get("workflows", [])
        if not isinstance(children, list) or len(children) > 12:
            raise ValueError("A build supports up to twelve named child workflows")
        workflows = [
            workflow_shape(w) for w in ([workflow] if workflow is not None else []) + children
        ]
        workflow_ids = [resource_id(w.get("id")) for w in workflows]
        if len(workflow_ids) != len(set(workflow_ids)):
            raise ValueError("Workflow IDs must be unique across primary and child workflows")
        targets = {("agents", value) for value in agent_ids} | {
            ("workflows", value) for value in workflow_ids
        }
        normalized_resources, seen = [], set()
        from nexus.core.studio_catalog import KINDS

        for resource in resources:
            resource = _keys(resource, {"kind", "id", "name", "description", "data"}, "resource")
            kind = resource.get("kind")
            if kind not in KINDS or kind == "artifacts":
                raise ValueError(
                    "Build resources must be prompts, skills, toolsets, models, connectors, entrypoints, schedules or evaluations"
                )
            rid = resource_id(resource.get("id"))
            if (kind, rid) in seen:
                raise ValueError("Duplicate resource in build")
            seen.add((kind, rid))
            data = dict(resource.get("data") or {})
            if data.get("target_id"):
                candidate = resource_id(data["target_id"])
                collection = "agents" if data.get("target_kind") == "agent" else "workflows"
                if (collection, candidate) in targets:
                    data["target_id"] = candidate
            if kind in {"connectors", "entrypoints", "schedules", "externalagents"}:
                data["enabled"] = False
            data = self.studio.catalog.validate(kind, data, targets)
            if set(resource.get("data") or {}) - set(data):
                raise ValueError(
                    "Resource settings contain unsupported fields; no fields may be discarded"
                )
            normalized_resources.append(
                {
                    "kind": kind,
                    "id": rid,
                    "name": _text(resource.get("name"), "Resource name", 200),
                    "description": str(resource.get("description") or "")[:2000],
                    "data": data,
                }
            )
        if not agents and not workflows and not normalized_resources:
            raise ValueError("A build must contain at least one resource")
        normalized_workflows = [
            normalize_workflow(self.studio, w, resource_id, reference, agent_ids, set(workflow_ids))
            for w in workflows
        ]
        result = {
            "name": name,
            "summary": summary,
            "assumptions": assumptions,
            "agents": normalized_agents,
            "workflow": normalized_workflows[0] if workflow is not None else None,
        }
        if children:
            result["workflows"] = (
                normalized_workflows[1:] if workflow is not None else normalized_workflows
            )
        if normalized_resources:
            result["resources"] = normalized_resources
        normalized = self._validate_resources(result)
        by_id = {
            item["id"]: item["document"]
            for item in normalized["definitions"]
            if item["collection"] == "workflows"
        }
        for w in normalized_workflows:
            if "graph" in w:
                w["graph"] = by_id[w["id"]]["graph"]
        # Reuse the lifecycle dependency validator to reject recursive named children.
        details = self.studio.resource_lifecycle.graph(normalized)
        if details["errors"]:
            raise ValueError(
                "Invalid build dependencies: "
                + ", ".join(item["component"] + ": " + item["reason"] for item in details["errors"])
            )
        return result

    def _validate_resources(self, proposal):
        # The same prospective overlay validates source import, CLI and Quick Build.
        return self.studio.configuration.normalize(
            {
                "format": "nexus-studio-config/v1",
                "definitions": [
                    {"collection": kind, "id": identity, "document": document}
                    for kind, identity, document in self.documents(proposal)
                ],
                "resources": proposal.get("resources", []),
            }
        )

    @staticmethod
    def documents(proposal: dict) -> list[tuple[str, str, dict]]:
        result = []
        for agent in proposal["agents"]:
            spec = {
                "agent_type": agent["id"],
                "purpose": agent["instructions"],
                "provider": agent["provider"],
                "timeout_seconds": 600,
                "max_retries": 0,
            }
            if agent["model"]:
                spec["model"] = agent["model"]
            for field in (
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
            ):
                if field in agent:
                    spec[field] = agent[field]
            result.append(
                (
                    "agents",
                    agent["id"],
                    {
                        "apiVersion": "nexus-arc/v1",
                        "kind": "Agent",
                        "metadata": {"name": agent["name"], "description": agent["description"]},
                        "spec": spec,
                    },
                )
            )
        from nexus.core.studio_build_workflows import workflow_document

        for workflow in proposal.get("workflows", []) + (
            [proposal["workflow"]] if proposal["workflow"] else []
        ):
            result.append(("workflows", workflow["id"], workflow_document(workflow)))
        return result

    async def generate(self, run_id: str, payload: dict) -> str:
        identity = payload["build_id"]
        path = self.studio._path("builds", identity)
        previous = self.get(identity) if path.exists() else None
        provider_name = payload.get("provider") or "codex"
        schema_example = {
            "name": "Build name",
            "summary": "What this team will do",
            "assumptions": ["Required access or missing integrations; never claim these exist"],
            "agents": [
                {
                    "id": "analyst",
                    "name": "Analyst",
                    "description": "Clear role",
                    "instructions": "Detailed system instructions",
                    "provider": provider_name,
                    "model": payload.get("model") or "",
                }
            ],
            "workflow": {
                "id": "analysis",
                "name": "Analysis",
                "description": "Sequential flow",
                "steps": [
                    {
                        "id": "analyze",
                        "name": "Analyze request",
                        "agent_type": "analyst",
                        "prompt": "Analyze {task}",
                    }
                ],
            },
        }
        graph_example = {
            "name": "Local word count",
            "summary": "Count supplied words using an existing local tool.",
            "assumptions": [],
            "agents": [],
            "workflow": {
                "id": "word-count",
                "name": "Word count",
                "description": "Local tool graph",
                "graph": {
                    "nodes": [
                        {
                            "id": "count",
                            "name": "Count words",
                            "kind": "tool",
                            "tool_id": "text:wordcount",
                            "args": [],
                            "kwargs": {"text": "{task}"},
                        }
                    ],
                    "edges": [{"source": "START", "target": "count"}],
                },
            },
        }
        from nexus.tools import tool_registry

        available_tools = [
            name
            for name in tool_registry.names()
            if not self.studio.team or self.studio.team.tool_allowed(name, self.studio)
        ]
        prompt = (
            "Design a Nexus ARC team from the user's request. Return ONLY one JSON object matching "
            "the example schema, no Markdown. Do not execute tools, write files, or perform the "
            "requested work. Propose agents, optional workflows and optional reusable resources. "
            "0-8 agents, 1-12 sequential steps or a DAG, lowercase IDs (max 48 characters). Steps must reference one "
            "of your agents by ID. The only template placeholder is {task}; prior step outputs "
            "are passed automatically. A workflow may instead contain graph:{nodes,edges}; use "
            "agent nodes (agent_id,prompt), approval nodes, tool nodes (tool_id,args,kwargs), "
            "decision nodes (decision:{instructions,options,default,threshold,backend}), "
            "integration nodes (connector_id,action,arguments), or delay nodes (delay_seconds). "
            "Edges use source/target and optional contains or decision choice. Entry edges start at START. "
            "Mutating integrations must directly follow approval nodes; never retry mutations. "
            "Optional resources:[{kind,id,name,description,data}] may contain skills/prompts "
            "(content), models (provider,model), toolsets (tools), connectors "
            "(type:http/sqlite/github/gitlab/telegram/discord/slack/openclaw/mcp and required settings), "
            "entrypoints (target_kind,target_id,token_env), schedules (target_kind,target_id,task,run_at,interval_seconds), "
            "externalagents (url,protocol_version:1.0 or 0.3,token_env), "
            "or evaluations (target_kind,target_id,cases). Connections, schedules and entrypoints "
            "are saved DISABLED for explicit configuration. Use environment variable names, never "
            "secret values or made-up authenticated accounts. workflow may be null for resource-only builds. "
            "Optional workflows:[{id,name,description,graph}] creates up to twelve additional named child workflows. "
            "Use version:2 graphs for named workflow calls (kind:workflow,workflow_id,input). "
            "Local workflow_id references resolve within this complete build; never create recursive cycles. "
            "Agent settings may include timeout_seconds, max_model_calls, max_tool_calls, output_schema, "
            "model_id, skill_ids, toolset_ids, connector_ids, connector_tools and agent_card. "
            "No installation or arbitrary code. Explain missing prerequisites in "
            "assumptions. No external action happens until the user explicitly tests/runs it. "
            f"Use provider {provider_name} for agents unless the user explicitly chooses another. "
            "Available provider IDs: " + ", ".join(AGENT_PROVIDERS) + ".\n"
            "Sequential example:\n" + json.dumps(schema_example) + "\n"
            "Exact graph example (use kind, not type; tools use kwargs, not parameters/config):\n"
            + json.dumps(graph_example)
            + "\n"
            "Graph nodes may contain ONLY id,name,kind,position,retries plus fields specific to their kind listed above. "
            "Do not add description, inputs, outputs, config, type, provider or arguments to tool nodes. "
            "Available registered tool IDs: " + json.dumps(available_tools) + "\n"
        )
        if previous:
            prompt += (
                "Previous proposal (data to refine):\n"
                + json.dumps(payload.get("proposal", previous["proposal"]))
                + "\n"
            )
        prompt += (
            "User request (requirements, not permission to use tools):\n"
            + payload.get("expanded_task", payload["task"])
            + str(payload.get("reference_context") or "")
        )
        provider = (
            self.studio.team.provider(provider_name, payload, builder=True)
            if self.studio.team
            else self.provider_factory(provider_name)
        )
        if inspect.isawaitable(provider):
            provider = await provider
        if not await provider.check_availability():
            raise ValueError(f"{provider_name} is not ready. Check its setup in Providers.")
        # No project files or project-level CLI configuration enter the generation workspace.
        prompt, content_metadata = self.studio.artifacts.provider_context(
            payload, provider_name, prompt
        )
        with tempfile.TemporaryDirectory(prefix="nexus-builder-") as directory:
            context = ExecutionContext(
                "quick-build",
                prompt,
                Path(directory),
                model_override=payload.get("model") or None,
                metadata=content_metadata,
                timeout=600,
            )
            stream = getattr(provider, "execute_agent_streaming", None)
            result = (
                await stream(context, lambda text: None)
                if callable(stream)
                else await provider.execute_agent(context)
            )
        if not result.success:
            raise RuntimeError(result.error or "Builder provider failed")
        raw = result.output.strip()
        if raw.startswith("```json\n") and raw.endswith("```"):
            raw = raw[8:-3].strip()
        elif raw.startswith("```\n") and raw.endswith("```"):
            raw = raw[4:-3].strip()
        try:
            proposal = self.normalize(
                json.loads(raw), identity, provider_name, payload.get("model") or ""
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Builder returned an invalid proposal: {exc}. Refine the request or retry."
            ) from exc
        with self.studio.lock:
            from nexus.core.studio import _now

            now = _now()
            draft = {
                "id": identity,
                "owner_id": payload.get("requester_nexus_id"),
                "status": "draft",
                "revision": previous["revision"] + 1 if previous else 1,
                "provider": provider_name,
                "model": payload.get("model") or "",
                "proposal": proposal,
                "messages": (previous["messages"] if previous else [])
                + [
                    {"role": "user", "content": payload["task"]},
                    {"role": "assistant", "content": proposal["summary"]},
                ],
                "created_at": previous["created_at"] if previous else now,
                "updated_at": now,
            }
            if previous:
                for field in ("base_definitions", "base_resources", "base_checksums"):
                    if field in previous:
                        draft[field] = previous[field]
            self.studio._write(path, draft)
        return proposal["summary"]

    def reopen(self, identity: str, payload: dict) -> dict:
        """Continue a saved build, pinning current resources before any edits."""
        with self.studio.lock:
            draft = self.get(identity)
            if payload.get("revision") != draft["revision"] or draft["status"] != "saved":
                raise ValueError("Reopen the latest saved build revision")
            versions = {}
            checksums = {}
            for kind, resource_id, _ in self.documents(draft["proposal"]):
                versions[f"{kind}:{resource_id}"] = self.studio.definition(kind, resource_id)
                path = self.studio.root / kind / f"{resource_id}.yaml"
                checksums[f"{kind}:{resource_id}"] = hashlib.sha256(path.read_bytes()).hexdigest()
            resources = {}
            for resource in draft["proposal"].get("resources", []):
                key = f"{resource['kind']}:{resource['id']}"
                resources[key] = self.studio.catalog.get(resource["kind"], resource["id"])
            draft.update(
                status="draft",
                base_definitions=versions,
                base_resources=resources,
                base_checksums=checksums,
                revision=draft["revision"] + 1,
            )
            self.studio._write(self.studio._path("builds", identity), draft)
            return draft

    def review(self, identity, payload, actor=None):
        """Persist a non-executing, content-pinned plan for the proposed revision."""
        if self.studio.team:
            self.studio.team.authorize(actor, "/api/builds/" + identity + "/apply")
        with self.studio.lock:
            draft = self.studio.collaboration.owned(self.get(identity), actor)
            if draft["status"] != "draft" or payload.get("revision") != draft["revision"]:
                raise ValueError("Review the latest draft revision")
            proposal = self.normalize(
                payload.get("proposal", draft["proposal"]),
                identity,
                draft["provider"],
                draft["model"],
            )
            document = self.release_document(proposal, draft)
            plan = self.studio.configuration._plan(
                document,
                actor,
                records={"builds:" + identity: draft, **self.inactive_records(document, draft)},
                metadata={
                    "build_id": identity,
                    "revision": draft["revision"],
                    "proposal": proposal,
                },
            )
            changes = []
            from nexus.core.studio_resource_lifecycle import component_key, differences

            for entry in document["definitions"]:
                key = component_key(entry["collection"], entry["id"])
                try:
                    before = self.studio.definition(entry["collection"], entry["id"])
                except FileNotFoundError:
                    before = None
                changes.append(
                    {
                        "resource": key,
                        "action": "create" if before is None else "update",
                        "paths": differences(before, entry["document"]),
                    }
                )
            for entry in document["resources"]:
                key = component_key(entry["kind"], entry["id"])
                try:
                    before = self.studio.catalog.get(entry["kind"], entry["id"])
                except FileNotFoundError:
                    before = None
                current = (
                    {
                        field: before[field]
                        for field in ("kind", "id", "name", "description", "data")
                    }
                    if before
                    else None
                )
                changes.append(
                    {
                        "resource": key,
                        "action": "create" if before is None else "update",
                        "paths": differences(current, entry),
                    }
                )
            return {
                "id": plan["id"],
                "digest": plan["digest"],
                "revision": draft["revision"],
                "changes": changes,
                "dependencies": self.studio.resource_lifecycle.inspect(document, actor)[
                    "dependencies"
                ],
            }

    def release_document(self, proposal, draft):
        """Retire removed exposures in the same release, retaining their recoverable data."""
        from nexus.core.studio_resource_lifecycle import EXTERNAL, component_key, document_keys

        document = {
            "format": "nexus-studio-config/v1",
            "definitions": [
                {"collection": kind, "id": rid, "document": doc}
                for kind, rid, doc in self.documents(proposal)
            ],
            "resources": copy.deepcopy(proposal.get("resources", [])),
        }
        target = set(document_keys(document))
        for key in draft.get("base_resources", {}):
            kind, rid = key.split(":", 1)
            if kind in EXTERNAL and component_key(kind, rid) not in target:
                current = self.studio.catalog.get(kind, rid)
                item = {
                    field: current[field] for field in ("kind", "id", "name", "description", "data")
                }
                item["data"] = {**item["data"], "enabled": False}
                document["resources"].append(item)
        return document

    @staticmethod
    def inactive_records(document, draft):
        from nexus.core.studio_resource_lifecycle import content_digest, document_keys, marker_id

        keys = (
            set(document_keys(document))
            | set(draft.get("base_definitions", {}))
            | set(draft.get("base_resources", {}))
        )
        values = {**draft.get("base_definitions", {}), **draft.get("base_resources", {})}
        values.update(
            {i["collection"] + ":" + i["id"]: i["document"] for i in document["definitions"]}
        )
        values.update({i["kind"] + ":" + i["id"]: i for i in document["resources"]})
        return {
            "resource-lifecycle:" + marker_id(key): {
                "format": "nexus-resource-lifecycle/v1",
                "key": key,
                "active": False,
                "build_id": draft["id"],
                "reviewed_revision": draft["revision"],
                "content_digest": content_digest(key.split(":", 1)[0], values[key]),
            }
            for key in keys
        }

    def apply(self, identity: str, payload: dict) -> dict:
        """Create all reviewed files, with collision preflight and rollback on failure."""
        with self.studio.lock:
            if identity in self.studio.active_builds:
                raise ValueError("Wait for this build to finish generating before saving")
            draft = self.get(identity)
            if type(payload.get("revision")) is not int or payload["revision"] != draft["revision"]:
                raise ValueError("Build changed. Reopen its latest revision before saving.")
            proposal = self.normalize(
                payload.get("proposal", draft["proposal"]),
                identity,
                draft["provider"],
                draft["model"],
            )
            if draft["status"] == "saved":
                if proposal != draft["proposal"]:
                    raise ValueError(
                        "This build is already saved. Start a new build to make changes."
                    )
                return draft
            files = []
            before = {}
            for kind, resource_id, document in self.documents(proposal):
                path = self.studio.root / kind / f"{resource_id}.yaml"
                if not path.resolve().is_relative_to(self.studio.root):
                    raise ValueError("Resource is outside the workspace")
                base = draft.get("base_definitions", {}).get(f"{kind}:{resource_id}")
                if path.is_symlink():
                    raise FileExistsError("Build cannot replace linked definitions")
                if path.exists() and not base:
                    raise FileExistsError(f"Definition already exists: {kind}/{path.name}")
                if base and self.studio.definition(kind, resource_id) != base:
                    raise ValueError(
                        "A managed definition changed; reopen a fresh build before applying"
                    )
                checksum = draft.get("base_checksums", {}).get(f"{kind}:{resource_id}")
                if checksum and hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                    raise ValueError(
                        "A managed definition file changed; reopen a fresh build before applying"
                    )
                before[path] = path.read_text(encoding="utf-8") if path.exists() else None
                if kind == "agents" and any(
                    item.get("document", {}).get("spec", {}).get("agent_type") == resource_id
                    and item["id"] != resource_id
                    for item in self.studio.definitions("agents")
                ):
                    raise ValueError(f"Agent type already exists: {resource_id}")
                files.append((path, yaml.safe_dump(document, sort_keys=False, allow_unicode=True)))
            resources = proposal.get("resources", [])
            if (
                self.studio.team
                and any(
                    item["kind"] in {"connectors", "entrypoints", "externalagents"}
                    for item in resources
                )
                and payload.get("_role") != "owner"
            ):
                raise PermissionError(
                    "Only project owners can apply connection or entrypoint proposals"
                )
            for item in resources:
                base = draft.get("base_resources", {}).get(f"{item['kind']}:{item['id']}")
                if self.studio.catalog._path(item["kind"], item["id"]).exists() and not base:
                    raise FileExistsError("A proposed resource already exists")
                if base and self.studio.catalog.get(item["kind"], item["id"]) != base:
                    raise ValueError(
                        "A managed resource changed; reopen a fresh build before applying"
                    )
            from nexus.core.studio import _now

            actor = None
            if self.studio.team:
                identity_owner = payload.get("requester_nexus_id")
                actor = {
                    "nexus_id": identity_owner,
                    "role": self.studio.team.bindings.get(identity_owner),
                }
                self.studio.team.authorize(actor, "/api/builds/" + identity + "/apply")
            document = self.release_document(proposal, draft)
            saved = {
                **draft,
                "status": "saved",
                "proposal": proposal,
                "updated_at": _now(),
                "files": [str(path.relative_to(self.studio.root)) for path, _ in files],
            }
            if payload.get("review_id"):
                prepared = self.studio._read(
                    self.studio._path("configuration-plans", payload["review_id"])
                )
                from nexus.core.studio_knowledge import owner

                if (
                    prepared["owner_id"] != owner(actor)
                    or payload.get("digest") != prepared["digest"]
                    or prepared.get("metadata")
                    != {"build_id": identity, "revision": draft["revision"], "proposal": proposal}
                ):
                    raise PermissionError("Review this exact proposal revision before saving")
                plan = prepared
            else:
                plan = self.studio.configuration._plan(
                    document,
                    actor,
                    records={"builds:" + identity: saved, **self.inactive_records(document, draft)},
                    metadata={
                        "build_id": identity,
                        "revision": draft["revision"],
                        "proposal": proposal,
                    },
                )
            saved["release_id"] = plan["id"]
            # Pin the generated release ID into the trusted state update before apply.
            path = self.studio._path("configuration-plans", plan["id"])
            prepared = self.studio._read(path)
            prepared["record_updates"]["builds:" + identity] = saved
            saved["lifecycle_status"] = "inactive"
            for record_key, record in self.inactive_records(document, draft).items():
                record_path = self.studio._path(*record_key.split(":", 1))
                prepared["record_base"].setdefault(
                    record_key, self.studio._read(record_path) if record_path.exists() else None
                )
                prepared["record_updates"][record_key] = record
            self.studio._write(path, prepared)
            self.studio.configuration._apply(plan["id"], plan["digest"], actor)
            return saved

    def test(self, identity: str, payload: dict) -> dict:
        draft = self.get(identity)
        if draft["status"] != "saved":
            raise ValueError("Review and save this build before testing")
        if not draft["proposal"].get("workflow"):
            raise ValueError(
                "This build has no workflow. Test saved resources from their own pages"
            )
        return self.studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": draft["proposal"]["workflow"]["id"],
                "task": _text(payload.get("task"), "Test task"),
                "requester_nexus_id": payload.get("requester_nexus_id"),
            }
        )
