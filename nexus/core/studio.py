"""Local workspace control plane, shared by the Studio server and embedders.

Definitions remain ordinary Nexus YAML. Conversation/run records live under
the selected workspace, and provider factories are injectable for applications.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from nexus.adapters.ai.base import EFFORT_LEVELS, ExecutionContext
from nexus.adapters.registry import AdapterRegistry
from nexus.core.studio_graph import StudioGraph, StudioPausedError
from nexus.core.studio_models import default_models
from nexus.core.workflow import WorkflowDefinition
from nexus.core.yaml_loader import YamlWorkflowLoader

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}\Z")
_PROVIDERS = (
    ("codex", "Codex", "codex", "cli", "codex login"),
    ("claude-code", "Claude Code", "claude", "cli", "claude auth login"),
    ("agy", "Google Antigravity", "agy", "cli", "agy"),
    ("copilot", "GitHub Copilot", "copilot", "cli", "copilot login"),
    ("opencode", "OpenCode", "opencode", "cli", "opencode auth login"),
    ("openai", "OpenAI API", "OPENAI_API_KEY", "api", "Set OPENAI_API_KEY before launch"),
    ("claude", "Anthropic API", "ANTHROPIC_API_KEY", "api", "Set ANTHROPIC_API_KEY before launch"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _session_summary(record):
    return {
        **{key: value for key, value in record.items() if key != "messages"},
        "message_count": len(record.get("messages", [])),
    }


def _identity(value: Any) -> str:
    value = str(value or "")
    if not _ID.fullmatch(value):
        raise ValueError("Invalid resource identifier")
    return value


class StudioService:
    """Workspace operations; delegated credentials use Nexus encryption and private state."""

    def __init__(
        self,
        root: str | Path,
        provider_factory: Callable | None = None,
        builder_factory: Callable | None = None,
        transport=None,
        team_policy=None,
        state_dir=None,
        workbench_options=None,
        connection_options=None,
        external_options=None,
        artifact_options=None,
        state_options=None,
        knowledge_options=None,
        speech_options=None,
        data_tool_options=None,
        research_options=None,
        workflow_limits=None,
        media_options=None,
        workspace_file_options=None,
    ):
        self.root = Path(root).resolve()
        self.team = team_policy
        from nexus.core.studio_workflow_policy import workflow_limits as validate_workflow_limits

        self.workflow_limits = validate_workflow_limits(workflow_limits)
        self.state = Path(state_dir).resolve() if state_dir else self.root / ".nexus" / "studio"
        if self.team and (
            not state_dir
            or self.state.is_relative_to(self.root)
            or self.root.is_relative_to(self.state)
        ):
            raise ValueError(
                "Shared mode requires a private state directory outside the project workspace"
            )
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.team and (
            self.state.stat().st_mode & 0o077 or self.state.stat().st_uid != os.getuid()
        ):
            raise ValueError("Shared state must be owned by this process user and have mode 0700")
        self.provider_factory = provider_factory or AdapterRegistry().create_ai
        self._custom_provider_factory = provider_factory is not None
        self.closing = False
        self.sql_state = None
        state_options = state_options or {}
        if state_options:
            from nexus.core.studio_connections import env_name
            from nexus.core.studio_state import StudioSQLState

            url = state_options.get("url") or os.getenv(env_name(state_options.get("url_env")), "")
            if not url:
                raise ValueError("SQL state connection environment reference is not configured")
            self.sql_state = StudioSQLState(url, state_options.get("namespace"))
        self.lock = self.sql_state.lock if self.sql_state else threading.RLock()
        # File bytes accompany SQL records. Mark the backend without a DSN so
        # offline backup cannot accidentally omit the authoritative namespace.
        storage_path = self.state / "storage-backend.json"
        storage = {
            "format": "nexus-studio-storage/v1",
            "backend": "sql" if self.sql_state else "files",
            "namespace": self.sql_state.namespace if self.sql_state else None,
        }
        if storage_path.exists() and self._read(storage_path) != storage:
            if self.sql_state:
                self.sql_state.engine.dispose()
            raise ValueError("Private state belongs to a different storage backend or namespace")
        from nexus.core.studio_schema import upgrade

        self.schema = upgrade(self)
        self._write(storage_path, storage)
        from nexus.core.quick_build import QuickBuilder, build_provider
        from nexus.core.studio_catalog import StudioCatalog

        self.catalog = StudioCatalog(self)
        from nexus.core.studio_skill_import import StudioSkillImporter

        self.skill_import = StudioSkillImporter(self)
        self.graph = StudioGraph(self)
        from nexus.core.studio_legacy_workflows import StudioLegacyWorkflows

        self.legacy = StudioLegacyWorkflows(self)
        self.builder = QuickBuilder(self, builder_factory or provider_factory or build_provider)
        self.active_builds: set[str] = set()
        self.active_sessions: set[str] = set()
        self.active_tasks: dict[str, tuple[Any, Any]] = {}
        from nexus.core.studio_connections import StudioConnections
        from nexus.core.studio_operations import StudioOperations

        self.connections = StudioConnections(self, **(connection_options or {}))
        from nexus.core.studio_oauth import StudioOAuth

        self.oauth = StudioOAuth(self)
        from nexus.core.studio_external import StudioExternalAgents

        self.external = StudioExternalAgents(self, **(external_options or {}))
        from nexus.core.studio_a2a_server import StudioA2AServer

        self.a2a = StudioA2AServer(self)
        from nexus.core.studio_artifacts import StudioArtifacts

        self.artifacts = StudioArtifacts(self, **(artifact_options or {}))
        self.operations = StudioOperations(self)
        from nexus.adapters.transport import MemoryTransport

        self.transport = transport or MemoryTransport()
        self.event_error = None
        self.event_topic = (
            "nexus.studio."
            + (
                hashlib.sha256(self.team.project_key.encode()).hexdigest()[:12] + "."
                if self.team
                else ""
            )
            + "events"
        )
        self.transport.subscribe(self.event_topic, self._record_event)
        from nexus.core.studio_workbench import StudioWorkbench

        self.workbench = StudioWorkbench(self, **(workbench_options or {}))
        if hasattr(self.workbench.router_client, "studio"):
            self.workbench.router_client.studio = self
        from nexus.core.studio_observability import StudioObservability

        self.observability = StudioObservability(self)
        from nexus.core.studio_conversations import StudioConversations
        from nexus.core.studio_execution import StudioExecution

        self.conversations = StudioConversations(self)
        self.execution = StudioExecution(self)
        from nexus.core.studio_telemetry import StudioTelemetry

        self.telemetry = StudioTelemetry(self)
        from nexus.core.studio_messaging import StudioMessaging

        self.messaging = StudioMessaging(self)
        from nexus.core.studio_versions import StudioVersions

        self.versions = StudioVersions(self)
        from nexus.core.studio_knowledge import StudioKnowledge

        self.knowledge = StudioKnowledge(self, **(knowledge_options or {}))
        if hasattr(self.knowledge.embeddings, "studio"):
            self.knowledge.embeddings.studio = self
        from nexus.core.studio_collaboration import StudioCollaboration
        from nexus.core.studio_design import StudioDesign
        from nexus.core.studio_features import StudioFeatures

        self.collaboration = StudioCollaboration(self)
        self.designer = StudioDesign(self)
        self.features = StudioFeatures(self)
        from nexus.core.studio_speech import StudioSpeech

        self.speech = StudioSpeech(self, **(speech_options or {}))
        from nexus.core.studio_configuration import StudioConfiguration

        self.configuration = StudioConfiguration(self)
        from nexus.core.studio_resource_lifecycle import StudioResourceLifecycle

        self.resource_lifecycle = StudioResourceLifecycle(self)
        from nexus.core.studio_data_tools import StudioDataTools

        self.data_tools = StudioDataTools(self, **(data_tool_options or {}))
        from nexus.core.studio_recipes import StudioRecipes
        from nexus.core.studio_research import StudioResearch

        self.research = StudioResearch(self, **(research_options or {}))
        from nexus.core.studio_media import StudioMedia

        self.media = StudioMedia(self, **(media_options or {}))

        from nexus.core.studio_dynamic_embeds import StudioDynamicEmbeds

        self.dynamic_embeds = StudioDynamicEmbeds(self)
        from nexus.core.studio_rulesets import StudioRulesets

        self.rulesets = StudioRulesets(self)
        from nexus.core.studio_assistant import StudioAssistant
        from nexus.core.studio_workspace_files import StudioWorkspaceFiles

        self.workspace_files = StudioWorkspaceFiles(self, **(workspace_file_options or {}))
        self.assistant = StudioAssistant(self)
        self.recipes = StudioRecipes(self)
        from nexus.core.studio_mcp_server import StudioMCPServer

        self.mcp_server = StudioMCPServer(self)
        if self.sql_state:
            from nexus.core.studio_state import StudioDurableExecutor

            self.executor = StudioDurableExecutor(self, state_options.get("workers", 4))
        else:
            self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nexus-studio")
            for run in self.records("runs", limit=None):
                if run["status"] in {"queued", "running"}:
                    self._update_run(
                        run["id"],
                        status="interrupted",
                        error="Previous Studio process stopped. Review its checkpoint and external effects before resuming.",
                    )
                    self.save_unsuccessful_turn(run["id"])
            for receipt in self.records("job-resumptions", limit=None):
                identity = receipt["run_id"]
                if self.run(identity)["status"] in {"queued", "running", "waiting"}:
                    self._update_run(
                        identity,
                        status="interrupted",
                        error="Studio stopped during workflow continuation handoff. Review checkpoints and effects before resuming.",
                    )
                self._path("job-resumptions", identity).unlink(missing_ok=True)
            for remote in self.records("external-runs", limit=None):
                if remote.get("status") in {
                    "sending",
                    "submitted",
                    "working",
                    "continuation-queued",
                }:
                    remote["status"] = "uncertain"
                    self._write(self._path("external-runs", remote["id"]), remote)
            for event in self.records("channel-events", limit=None):
                if event.get("status") == "launching":
                    event.update(
                        status="uncertain",
                        error="Studio stopped while launching this event. Inspect its run before submitting again.",
                    )
                    self._write(self._path("channel-events", event["id"]), event)

        self.speech.cleanup()

    def _path(self, collection: str, identity: str, suffix: str = "json") -> Path:
        path = self.state / collection / f"{_identity(identity)}.{suffix}"
        if not (
            path.resolve().is_relative_to(self.root) or path.resolve().is_relative_to(self.state)
        ):
            raise ValueError("Resource is outside the workspace")
        if self.sql_state and suffix == "json":
            from nexus.core.studio_state import SQLRecordPath

            return SQLRecordPath(self.sql_state, collection, _identity(identity), path)
        return path

    def _write(self, path: Path, value: Any) -> None:
        from nexus.core.studio_state import SQLRecordPath

        if isinstance(path, SQLRecordPath):
            return self.sql_state.put(path.collection, path.identity, value)
        if not (
            path.resolve().is_relative_to(self.root) or path.resolve().is_relative_to(self.state)
        ):
            raise ValueError("Resource is outside the workspace")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self, path: Path) -> dict:
        from nexus.core.studio_state import SQLRecordPath

        if isinstance(path, SQLRecordPath):
            return self.sql_state.get(path.collection, path.identity)
        if not (
            path.resolve().is_relative_to(self.root) or path.resolve().is_relative_to(self.state)
        ):
            raise ValueError("Resource is outside the workspace")
        return json.loads(path.read_text(encoding="utf-8"))

    def providers(self) -> list[dict]:
        """Installation/configuration inventory, not an authentication or quota test."""
        from nexus.adapters.ai.opencode_provider import get_opencode_auth, resolve_opencode_cli

        items: list[dict] = []
        for identity, label, source, kind, setup in _PROVIDERS:
            cli_path = None
            if kind == "cli":
                if identity == "opencode":
                    cli_path = resolve_opencode_cli()
                else:
                    cli_path = shutil.which(source)
                    if not cli_path:
                        for cand in (
                            Path.home() / ".local" / "bin" / source,
                            Path("/opt/homebrew/bin") / source,
                            Path("/usr/local/bin") / source,
                        ):
                            try:
                                if cand.is_file() and os.access(cand, os.X_OK):
                                    cli_path = str(cand)
                                    break
                            except OSError:
                                pass

            configured = bool(cli_path) if kind == "cli" else bool(os.getenv(source))
            status = (
                "CLI installed"
                if kind == "cli" and cli_path
                else "API key configured"
                if kind == "api" and os.getenv(source)
                else "Setup needed"
            )
            setup_text = setup

            if identity == "opencode":
                auth = get_opencode_auth()
                if auth:
                    configured = True
                    if cli_path:
                        status = "CLI installed"
                    else:
                        status = "Logged in"
                        setup_text = f"Logged in ({auth[0]})\nCLI (optional): npm install -g opencode-ai"

            items.append(
                {
                    "id": identity,
                    "name": label,
                    "kind": kind,
                    "configured": configured,
                    "status": status,
                    "setup": setup_text,
                    "auth_verified": False,
                    "quota": None,
                    "models": default_models(identity),
                }
            )
        return items

    def definitions(self, kind: str) -> list[dict]:
        if kind not in {"agents", "workflows"}:
            raise ValueError("Unknown definition collection")
        directory = self.root / kind
        records = []
        for path in sorted(directory.glob("*.yaml")):
            if not path.resolve().is_relative_to(self.root):
                continue
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict):
                    raise ValueError("Definition must be a mapping")
                records.append(
                    {
                        "id": path.stem,
                        "document": doc,
                        "revision": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            except (ValueError, OSError, yaml.YAMLError) as exc:
                records.append({"id": path.stem, "error": str(exc)})
        return records

    def definition(self, kind: str, identity: str) -> dict:
        identity = _identity(identity)
        for item in self.definitions(kind):
            if item["id"] == identity:
                if "error" in item:
                    raise ValueError(item["error"])
                return item["document"]
        raise FileNotFoundError(f"{kind.rstrip('s').title()} not found: {identity}")

    def _definition_document(self, kind: str, payload: dict) -> tuple[str, dict]:
        name = str(payload.get("name") or "").strip()
        identity = str(payload.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
        _identity(identity)
        if not name:
            raise ValueError("Name is required")
        metadata = {"name": name, "description": str(payload.get("description") or "")}
        if kind == "agents":
            provider = str(payload.get("provider") or "codex")
            if provider not in {p[0] for p in _PROVIDERS}:
                raise ValueError("Unknown provider")
            timeout = int(payload.get("timeout_seconds", 600))
            if not 1 <= timeout <= 3600:
                raise ValueError("Timeout must be between 1 and 3600 seconds")
            doc = {
                "apiVersion": "nexus-arc/v1",
                "kind": "Agent",
                "metadata": metadata,
                "spec": {
                    "agent_type": str(payload.get("agent_type") or identity),
                    "provider": provider,
                    "purpose": str(payload.get("instructions") or ""),
                    "timeout_seconds": timeout,
                    "max_retries": 0,
                },
            }
            if payload.get("model"):
                doc["spec"]["model"] = str(payload["model"]).strip()
            if payload.get("effort"):
                effort = str(payload["effort"]).strip().lower()
                if effort not in EFFORT_LEVELS:
                    raise ValueError(f"Effort must be one of {', '.join(EFFORT_LEVELS)}")
                doc["spec"]["effort"] = effort
            if payload.get("guardrails") is not None:
                from nexus.core.studio_observability import validate_guardrails

                doc["spec"]["guardrails"] = validate_guardrails(payload["guardrails"])
            tools = payload.get("tool_ids", [])
            if isinstance(tools, str):
                tools = [name.strip() for name in tools.split(",") if name.strip()]
            if (
                not isinstance(tools, list)
                or len(tools) > 30
                or any(not isinstance(t, str) for t in tools)
            ):
                raise ValueError("Agent tools must be a list of up to 30 registered tool names")
            from nexus.tools import tool_registry

            if any(
                not tool_registry.get(t) or self.team and not self.team.tool_allowed(t, self)
                for t in tools
            ):
                raise ValueError("Choose registered tools allowed in this workspace")
            if tools:
                doc["spec"]["tool_ids"] = tools
            skills = payload.get("skill_ids", [])
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.split(",") if s.strip()]
            self.catalog.context({"skill_ids": skills})
            if skills:
                doc["spec"]["skill_ids"] = skills
            from nexus.core.studio_agent_settings import validate_settings

            doc["spec"].update(validate_settings(self, payload))
        elif kind == "workflows" and payload.get("graph") is not None:
            doc = {
                "apiVersion": "nexus-arc/v1",
                "kind": "Workflow",
                "metadata": metadata,
                "graph": self.graph.validate(payload["graph"]),
            }
        elif kind == "workflows":
            steps = payload.get("steps")
            if not isinstance(steps, list) or not steps:
                raise ValueError("Add at least one workflow step")
            normalized_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    raise ValueError("Each workflow step must be an object")
                normalized = dict(step)
                if "parallel_with" in normalized and "parallel" not in normalized:
                    normalized["parallel"] = normalized.pop("parallel_with")
                if normalized.get("prompt") and not normalized.get("prompt_template"):
                    normalized["prompt_template"] = normalized.pop("prompt")
                normalized_steps.append(normalized)
            doc = {
                "apiVersion": "nexus-arc/v1",
                "kind": "Workflow",
                "metadata": metadata,
                "steps": normalized_steps,
            }
            YamlWorkflowLoader.load_from_dict(doc)
        else:
            raise ValueError("Unknown definition collection")
        return identity, doc

    def save_definition(self, kind: str, payload: dict) -> dict:
        identity, doc = self._definition_document(kind, payload)
        path = self.root / kind / f"{identity}.yaml"
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Resource is outside the workspace")
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Creates only. Existing definitions can be edited in their source YAML.
            with path.open("x", encoding="utf-8") as handle:
                yaml.safe_dump(doc, handle, sort_keys=False, allow_unicode=True)
        result = {
            "id": identity,
            "document": doc,
            "revision": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        self.versions.remember(kind, result)
        return result

    def update_definition(self, kind: str, identity: str, payload: dict) -> dict:
        identity = _identity(identity)
        with self.lock:
            current = next(
                (item for item in self.definitions(kind) if item["id"] == identity), None
            )
            if current is None:
                raise FileNotFoundError("Definition not found")
            if not payload.get("revision") or payload["revision"] != current.get("revision"):
                raise ValueError("Definition changed. Reopen before saving.")
            self.versions.remember(kind, current)
            _, doc = self._definition_document(kind, {**payload, "id": identity})
            if kind == "agents":
                spec = dict(current.get("document", {}).get("spec", {}))
                spec.update(doc["spec"])
                if not payload.get("model"):
                    spec.pop("model", None)
                if not payload.get("skill_ids"):
                    spec.pop("skill_ids", None)
                doc["spec"] = spec
            elif kind == "workflows":
                current_meta = dict(current.get("document", {}).get("metadata", {}))
                current_meta.update(doc["metadata"])
                doc["metadata"] = current_meta
            path = self.root / kind / f"{identity}.yaml"
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("Resource is outside the workspace")
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(
                    yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
                )
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            result = {
                "id": identity,
                "document": doc,
                "revision": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            self.versions.remember(kind, result)
            return result

    def delete_definition(self, kind: str, identity: str) -> dict:
        if kind not in {"agents", "workflows"}:
            raise ValueError("Unknown definition collection")
        identity = _identity(identity)
        path = self.root / kind / f"{identity}.yaml"
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Resource is outside the workspace")
        with self.lock:
            if not path.is_file():
                raise FileNotFoundError(f"{kind.rstrip('s').title()} not found: {identity}")
            path.unlink()
        return {"id": identity, "deleted": True}

    def records(self, collection: str, limit: int | None = 100) -> list[dict]:
        if self.sql_state:
            values = self.sql_state.records(collection)
            if collection == "sessions":
                values = [_session_summary(item) for item in values]
            return sorted(values, key=lambda item: item.get("updated_at", ""), reverse=True)[:limit]
        records = []
        directory = self._path(collection, "index").parent
        for path in directory.glob("*.json"):
            try:
                value = self._read(path)
                if collection == "sessions":
                    value = _session_summary(value)
                records.append(value)
            except (ValueError, OSError):
                continue
        return sorted(records, key=lambda item: item.get("updated_at", ""), reverse=True)[:limit]

    def _new_session(self, session_id: str, payload: dict) -> dict:
        return {
            "id": session_id,
            "title": payload.get("session_title") or payload["task"][:70],
            "messages": [],
            "created_at": _now(),
            "owner_id": payload.get("requester_nexus_id"),
            "knowledge_project_id": payload.get("knowledge_project_id"),
        }

    def save_unsuccessful_turn(self, run_id: str, payload: dict | None = None) -> None:
        """Keep a failed, cancelled or interrupted chat turn in its conversation.

        The turn is marked ``status: failed`` so it is shown but never sent back
        to a model as history. Saving twice for one run is a no-op.
        """
        run = self.run(run_id)
        data = {**run, **(payload or {})}
        session_id = data.get("session_id")
        if data.get("kind", "chat") != "chat" or not session_id or not data.get("task"):
            return
        if run.get("status") not in {"failed", "interrupted", "cancelled"}:
            return
        path = self._path("sessions", session_id)
        with self.lock:
            session = self._read(path) if path.exists() else self._new_session(session_id, data)
            if any(message.get("run_id") == run_id for message in session["messages"]):
                return
            provider = run.get("provider") or data.get("provider") or ""
            session["messages"].extend(
                [
                    {"role": "user", "content": data["task"], "run_id": run_id, "status": "failed"},
                    {
                        "role": "assistant",
                        "content": run.get("error") or f"The task was {run['status']}.",
                        "run_id": run_id,
                        "status": "failed",
                        "provider": provider,
                    },
                ]
            )
            session.update(
                updated_at=_now(),
                provider=provider,
                agent_id=data.get("agent_id"),
                model=data.get("model") or "",
                effort=data.get("effort") or "",
                studio_assistant=bool(data.get("studio_assistant")),
            )
            self._write(path, session)

    def session(self, identity: str) -> dict:
        return self._read(self._path("sessions", identity))

    def run(self, identity: str) -> dict:
        with self.lock:
            return self._read(self._path("runs", identity))

    def _record_event(self, topic: str, payload: dict) -> None:
        event = {
            key: str(payload.get(key, ""))[:200] for key in ("run_id", "status", "kind", "source")
        }
        event.update(id=uuid.uuid4().hex, updated_at=_now())
        self._write(self._path("events", event["id"]), event)

    def mesh_status(self) -> dict:
        runs = self.records("runs")
        return {
            "transport": type(self.transport).__name__,
            "topic": self.event_topic,
            "scope": "recent 100 workspace runs",
            "queued": sum(run["status"] == "queued" for run in runs),
            "running": sum(run["status"] == "running" for run in runs),
            "waiting": sum(run["status"] == "waiting" for run in runs),
            "event_error": self.event_error,
            "events": self.records("events"),
            "workspace": str(self.root),
            "access": "Nexus authenticated team" if self.team else "single-user loopback",
            "workers": len(self.executor.threads) if self.sql_state else 4,
            "state_backend": self.sql_state.engine.dialect.name
            if self.sql_state
            else "local files",
            "durable_jobs": bool(self.sql_state),
        }

    def export_definitions(self) -> dict:
        return {
            "format": "nexus-studio-export/v1",
            "exported_at": _now(),
            "agents": self.definitions("agents"),
            "workflows": self.definitions("workflows"),
            "catalog": {
                kind: self.catalog.list(kind, archived=True)
                for kind in (
                    "prompts",
                    "skills",
                    "toolsets",
                    "models",
                    "decisionmodels",
                    "connectors",
                    "entrypoints",
                    "schedules",
                    "evaluations",
                    "datasets",
                    "evaluators",
                    "experiments",
                    "externalagents",
                )
            },
        }

    def _update_run(self, identity: str, **values: Any) -> None:
        with self.lock:
            run = self.run(identity)
            previous_status = run.get("status")
            old_approvals = run.get("awaiting_approval", [])
            old_inputs = run.get("awaiting_input", [])
            if "provider" in values:
                values["providers_used"] = list(
                    dict.fromkeys([*run.get("providers_used", []), values["provider"]])
                )
            run.update(values, updated_at=_now())
            self._write(self._path("runs", identity), run)
            if hasattr(self, "execution"):
                if "status" in values and values["status"] != previous_status:
                    self.execution.record(
                        identity, "run", status=values["status"], previous_status=previous_status
                    )
                for field, previous, kind in (
                    ("awaiting_approval", old_approvals, "approval"),
                    ("awaiting_input", old_inputs, "input"),
                ):
                    if field in values and values[field] != previous:
                        for item in values[field] or previous:
                            self.execution.record(
                                identity,
                                kind,
                                status="waiting" if values[field] else "resolved",
                                node_id=item.get("id"),
                                node_name=item.get("name"),
                            )
                if values.get("artifact_id"):
                    self.execution.record(
                        identity,
                        "artifact",
                        status="created",
                        artifact_id=values["artifact_id"],
                        artifact_revision=values.get("artifact_revision"),
                    )
            if "status" in values:
                self.workbench.audit(run)
                if values["status"] in {"waiting", "completed", "failed", "interrupted"} and (
                    self._path("graph-runs", identity).exists()
                    or self._path("yaml-runs", identity).exists()
                ):
                    try:
                        actor = (
                            {"nexus_id": run.get("requester_nexus_id"), "role": "owner"}
                            if self.team
                            else None
                        )
                        self.workbench.capture_checkpoint(identity, actor)
                    except (ValueError, OSError, sqlite3.Error):
                        self.workbench.audit_error = "Workflow checkpoint could not be captured"
                try:
                    self.transport.publish(
                        self.event_topic,
                        {
                            "run_id": identity,
                            "status": values["status"],
                            "kind": run["kind"],
                            "source": self.root.name,
                        },
                    )
                except Exception as exc:
                    self.event_error = str(exc)

    def _delta(self, identity: str, text: str) -> None:
        self.conversations.delta(identity, text)

    def start_run(self, payload: dict, *, callback_origin=None) -> dict:
        """Queue an explicitly selected agent/chat/workflow/tool; no implicit provider fallback."""
        payload = dict(payload)
        if "session_title" in payload:
            from nexus.core.studio_catalog import _text

            if payload.get("kind", "chat") != "chat" or payload.get("session_id"):
                raise ValueError(
                    "Use a title only when creating a new chat; rename saved conversations instead"
                )
            payload["session_title"] = _text(
                payload["session_title"], "Conversation name", 200
            ).strip()
        if type(payload.get("studio_assistant", False)) is not bool:
            raise ValueError("Studio assistant selection must be true or false")
        if payload.get("studio_assistant"):
            if payload.get("kind", "chat") != "chat" or payload.get("agent_id"):
                raise ValueError("Studio assistant requires a chat without a custom agent")
            payload["worker_tools"] = "none"
        payload["_media_profile_revisions"] = self.media.revisions()
        payload.pop("_evaluation_case", None)
        payload.pop("_workflow_limits", None)
        payload.pop("_callback_origin", None)
        payload.pop("_speech_profile_snapshot", None)
        if type(payload.get("external_callbacks", False)) is not bool:
            raise ValueError("Callback updates must be true or false")
        if payload.get("external_callbacks"):
            from nexus.core.studio_external_callbacks import origin

            if payload.get("kind") != "external" or payload.get("external_resume_id"):
                raise ValueError("Enable callbacks on the initial external task only")
            payload["_callback_origin"] = origin(
                self.team.public_url
                if self.team
                else callback_origin or self.external.callback_origin
            )
            from nexus.core.auth.delegated_credentials import seal

            seal("external-callback-readiness", {})
        if payload.get("kind", "chat") in {"chat", "build"}:
            from nexus.core.studio_team import worker_tool_level

            # Web search and fetch unless the request picks another tool level.
            payload["worker_tools"] = worker_tool_level(payload.get("worker_tools") or "web")
        elif "worker_tools" in payload:
            raise ValueError("Tools can be chosen for chat and Quick Build runs only")
        if payload.get("kind", "chat") == "chat" and payload.get("session_id"):
            previous_project = self.session(payload["session_id"]).get("knowledge_project_id")
            if previous_project:
                if payload.get("knowledge_project_id", previous_project) != previous_project:
                    raise ValueError("Start a new conversation to change its knowledge project")
                payload["knowledge_project_id"] = previous_project
        if self.team:
            identity = payload.get("requester_nexus_id")
            role = self.team.bindings.get(identity)
            if not role or not self.team.manager.has_project_access(
                identity, self.team.project_key
            ):
                raise PermissionError("Nexus project access is required")
            self.team.authorize({"nexus_id": identity, "role": role}, "/api/runs", payload)
            payload["_role"] = role
        if payload.get("kind") in {
            "delegation",
            "campaign",
            "transcription",
            "decision",
            "external",
        }:
            self.workbench.jobs.prepare(payload)
        payload = self.catalog.expand(payload)
        if payload.get("kind") == "external":
            payload = self.external.prepare_payload(payload)
        payload = self.knowledge.prepare(payload)
        payload["agent_snapshots"] = self.versions.snapshots()
        kind = payload.get("kind", "chat")
        if kind not in {
            "chat",
            "workflow",
            "tool",
            "build",
            "evaluation",
            "delegation",
            "campaign",
            "transcription",
            "decision",
            "external",
            "knowledge-index",
            "design",
            "speech",
        }:
            raise ValueError("Unknown run kind")
        if kind == "evaluation":
            self.operations.evaluation_request(payload)
            payload["task"] = "Evaluate " + payload["suite_snapshot"]["name"]
        if kind == "speech":
            self.speech.prepare(payload)
        if kind != "tool" and not str(payload.get("task") or "").strip():
            raise ValueError("A task is required")
        launch_revisions = {}
        if kind == "workflow":
            launch_key = "workflows:" + str(payload.get("workflow_id"))
            launch_revisions[launch_key] = self.resource_lifecycle.assert_launch(launch_key)
            payload["_workflow_limits"] = dict(self.workflow_limits)
            payload["workflow_snapshot"] = self.definition("workflows", payload.get("workflow_id"))
            payload["workflow_snapshots"] = {
                item["id"]: item["document"]
                for item in self.definitions("workflows")
                if item.get("document") and item["document"].get("graph", {}).get("version") == 2
            }
        if kind == "chat" and payload.get("agent_id"):
            launch_key = "agents:" + payload["agent_id"]
            launch_revisions[launch_key] = self.resource_lifecycle.assert_launch(launch_key)
        extra_targets = []
        if kind == "delegation":
            extra_targets = ["agents:" + value for value in payload["agent_ids"]]
        elif kind == "external":
            extra_targets = ["externalagents:" + payload["external_agent_id"]]
        elif kind == "evaluation":
            extra_targets = ["evaluations:" + payload["evaluation_id"]]
            if payload.get("experiment_id"):
                extra_targets.append("experiments:" + payload["experiment_id"])
        for launch_key in extra_targets:
            launch_revisions[launch_key] = self.resource_lifecycle.assert_launch(launch_key)
        from nexus.core.studio_agent_settings import pin_agents

        pin_agents(self, payload)
        from nexus.core.studio_decision_images import pin as pin_decision_images

        pin_decision_images(self, payload)
        if kind == "tool":
            from nexus.tools import tool_registry

            if self.team and not self.team.tool_allowed(payload.get("tool"), self):
                raise PermissionError("Tool is not enabled for shared execution")
            if not tool_registry.get(str(payload.get("tool") or "")):
                raise ValueError("Unknown tool")
            if not isinstance(payload.get("args", []), list) or not isinstance(
                payload.get("kwargs", {}), dict
            ):
                raise ValueError("Tool arguments must be a list and keyword arguments an object")
            self.media.pin_tool(payload)
        provider = str(payload.get("provider") or "codex")
        if provider not in {p[0] for p in _PROVIDERS}:
            raise ValueError("Unknown provider")
        if payload.get("agent_id"):
            self.definition("agents", payload["agent_id"])
        if kind == "build":
            payload["build_id"] = _identity(payload.get("build_id") or uuid.uuid4().hex)
        session_id = _identity(payload.get("session_id") or uuid.uuid4().hex)
        if self.team and payload.get("session_id"):
            existing_session = self.session(session_id)
            if not self.team.visible(
                existing_session,
                {"nexus_id": payload["requester_nexus_id"], "role": payload["_role"]},
            ):
                raise PermissionError("Conversation is private to another user")
        if (
            payload.get("session_id")
            and self._path("sessions", session_id).exists()
            and self.session(session_id).get("archived")
        ):
            raise ValueError("Restore this archived conversation before continuing it")
        run_id = uuid.uuid4().hex
        record = {
            "id": run_id,
            "kind": kind,
            "requester_nexus_id": payload.get("requester_nexus_id"),
            "status": "queued",
            "provider": {
                "tool": "local-tool",
                "transcription": "whisper-api",
                "decision": "decision-" + str(payload.get("backend") or ""),
                "campaign": "publishing",
                "delegation": "agent-delegation",
                "external": "external-agent",
            }.get(kind, provider),
            "task": str(payload.get("task") or payload.get("tool") or ""),
            "session_id": session_id if kind == "chat" else None,
            "output": "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        if payload.get("studio_assistant"):
            record["studio_assistant"] = True
        if kind == "speech":
            record.update(
                speech_profile=payload["speech_profile"],
                speech_action=payload["_speech_profile_snapshot"]["settings"]["action"],
                speech_profile_revision=payload["_speech_profile_snapshot"]["digest"],
            )
        if kind == "build":
            record["build_id"] = payload["build_id"]
        if payload.get("artifact_ids") and payload.get("artifact_refs"):
            record["artifact_refs"] = [
                {"id": item["id"], "revision": item["revision"]}
                for item in payload["artifact_refs"]
            ]
        with self.lock:
            for launch_key, launch_revision in launch_revisions.items():
                if self.resource_lifecycle.assert_launch(launch_key) != launch_revision:
                    raise ValueError("Resource changed while queuing; review its current revision")
            if self.sql_state:
                if kind == "chat" and any(
                    item.get("session_id") == session_id and item["status"] in {"queued", "running"}
                    for item in self.records("runs", limit=None)
                ):
                    raise ValueError("This conversation already has a running task")
                if kind == "build" and any(
                    item.get("build_id") == payload["build_id"]
                    and item["status"] in {"queued", "running"}
                    for item in self.records("runs", limit=None)
                ):
                    raise ValueError("This build is already generating a proposal")
            if kind == "build":
                if payload["build_id"] in self.active_builds:
                    raise ValueError("This build is already generating a proposal")
                self.builder.validate_request(payload)
                self.active_builds.add(payload["build_id"])
            if kind == "chat" and session_id in self.active_sessions:
                raise ValueError("This conversation already has a running task")
            if kind == "chat":
                self.active_sessions.add(session_id)
            claim = None
            try:
                claim = self.workbench.reserve_launch(payload)
                if kind == "speech":
                    self.speech.bind_input(run_id, payload)
                self._write(self._path("runs", run_id), record)
                self.executor.submit(self._execute, run_id, dict(payload), session_id)
            except BaseException:
                self.workbench.release_launch(claim)
                self.active_sessions.discard(session_id)
                self.active_builds.discard(payload.get("build_id"))
                raise
        return record

    def resume_workflow(self, run_id, payload):
        """Continue only after the prior invocation has stopped touching its checkpoints."""
        with self.lock:
            if self.sql_state:
                self.executor.resume(run_id, payload, "")
                return
            path = self._path("job-resumptions", run_id)
            if run_id in self.active_tasks:
                receipt = {
                    "format": "nexus-workflow-resumption/v1",
                    "run_id": run_id,
                    "owner_id": payload.get("requester_nexus_id"),
                    "source_token": None,
                    "payload": payload,
                    "session_id": "",
                }
                if path.exists() and self._read(path) != receipt:
                    raise ValueError("This workflow already has a pending continuation")
                self._write(path, receipt)
            else:
                path.unlink(missing_ok=True)
                self.executor.submit(self._execute, run_id, payload, "")

    def _finish_unsuccessful_run(self, run_id, payload, *, save_turn=True, **values):
        # Readers must not observe a terminal run before its failed chat receipt.
        # SQLWorkspaceLock also commits these records in one existing transaction.
        with self.lock:
            self._update_run(run_id, **values)
            if save_turn:
                try:
                    self.save_unsuccessful_turn(run_id, payload)
                except Exception:  # preserve terminal status if conversation storage fails
                    pass

    def _execute(self, run_id: str, payload: dict, session_id: str) -> None:
        async def execute():
            with self.lock:
                self.active_tasks[run_id] = (asyncio.get_running_loop(), asyncio.current_task())
            if self.run(run_id).get("cancel_requested") and not self.run(run_id).get(
                "cancellation_cleanup"
            ):
                self._finish_unsuccessful_run(run_id, payload, status="cancelled")
                return
            self._update_run(run_id, status="running")
            from nexus.core.studio_connections import execution_identity

            identity_token = execution_identity.set(payload.get("requester_nexus_id"))
            from nexus.core.studio_data_tools import current_run_id

            run_token = current_run_id.set(run_id)
            from nexus.core.studio_media import media_profile_revisions

            media_token = media_profile_revisions.set(payload.get("_media_profile_revisions"))
            try:
                if self.closing:
                    raise asyncio.CancelledError
                await self._execute_async(run_id, payload, session_id)
            except StudioPausedError:
                additions = (
                    {"cleanup_status": "waiting"}
                    if self.run(run_id).get("cancellation_cleanup")
                    else {}
                )
                self._update_run(run_id, status="waiting", **additions)
            except asyncio.CancelledError:
                requested = self.run(run_id).get("cancel_requested")
                interrupted = self.closing or not requested
                additions = (
                    {"cleanup_status": "interrupted"}
                    if self.run(run_id).get("cancellation_cleanup")
                    else {}
                )
                self._finish_unsuccessful_run(
                    run_id,
                    payload,
                    status="interrupted" if interrupted else "cancelled",
                    error="Execution cancelled; already-started external effects may still complete"
                    if requested
                    else "Studio stopped before the task completed",
                    **additions,
                )
            finally:
                execution_identity.reset(identity_token)
                current_run_id.reset(run_token)
                media_profile_revisions.reset(media_token)

        denied = False
        try:
            asyncio.run(execute())
        except Exception as exc:
            # Lost access must not create records on the user's behalf.
            denied = isinstance(exc, PermissionError)
            if self.run(run_id)["status"] != "cancelled":
                self._finish_unsuccessful_run(
                    run_id, payload, save_turn=not denied, status="failed", error=str(exc)
                )
        finally:
            try:
                if not denied:
                    self.save_unsuccessful_turn(run_id, payload)
            except Exception:  # never block run cleanup on conversation storage
                pass
            with self.lock:
                self.active_sessions.discard(session_id)
                self.active_tasks.pop(run_id, None)
                self.active_builds.discard(payload.get("build_id"))
                if not self.sql_state and not self.closing:
                    pending = self._path("job-resumptions", run_id)
                    if pending.exists():
                        receipt = self._read(pending)
                        if self.run(run_id)["status"] in {"queued", "waiting"}:
                            self._update_run(
                                run_id,
                                status="queued",
                                **(
                                    {"cleanup_status": "queued"}
                                    if self.run(run_id).get("cancellation_cleanup")
                                    else {}
                                ),
                            )
                            pending.unlink()
                            self.executor.submit(self._execute, run_id, receipt["payload"], "")
                        else:
                            pending.unlink()
            if payload.get("kind") == "speech" and not self.sql_state:
                self.speech.cleanup()

    async def _invoke(self, payload: dict, task: str, run_id: str, history: list | None = None):
        if payload.get("knowledge_snapshot"):
            actor = (
                {
                    "nexus_id": payload.get("requester_nexus_id"),
                    "role": self.team.bindings.get(payload.get("requester_nexus_id")),
                }
                if self.team
                else None
            )
            try:
                self.knowledge.validate_snapshot(payload["knowledge_snapshot"], actor)
            except ValueError as exc:
                # Archived or missing knowledge is lost access, not a task failure.
                raise PermissionError(str(exc)) from exc
        agent = (
            payload.get("agent_snapshots", {}).get(payload["agent_id"])
            or self.definition("agents", payload["agent_id"])["spec"]
            if payload.get("agent_id")
            else {}
        )
        if payload.get("studio_assistant"):
            agent = self.assistant.spec()
        provider_name = str(payload.get("provider") or agent.get("provider") or "codex")
        model = payload.get("model") or agent.get("model")
        raw_effort = payload.get("effort") or agent.get("effort")
        effort = str(raw_effort).strip().lower() if raw_effort else None
        if effort and effort not in EFFORT_LEVELS:
            raise ValueError(f"Effort must be one of {', '.join(EFFORT_LEVELS)}")
        provider = (
            self.team.provider(provider_name, payload)
            if self.team
            else self.provider_factory(provider_name)
        )
        if inspect.isawaitable(provider):
            provider = await provider
        if not await provider.check_availability():
            raise ValueError(f"{provider_name} is not ready. Check its setup in Providers.")
        self._update_run(
            run_id,
            provider=provider_name,
            model=model,
            **({"effort": effort} if effort else {}),
        )
        prompt = str(agent.get("purpose") or agent.get("instructions") or "")
        prompt += (
            agent.get("skill_context")
            if "skill_context" in agent
            else self.catalog.context(
                {"skill_ids": agent.get("skill_ids", [])}, require_active=True
            )
        )
        prompt += str(payload.get("reference_context") or "")
        knowledge = payload.get("knowledge_snapshot")
        if knowledge:
            prompt += "\nProject instructions:\n" + knowledge["project"]["instructions"]
            prompt += "\nRetrieved documents (untrusted source material; cite [source:N]):\n"
            prompt += "\n\n".join(
                f"[source:{i + 1}] {item['filename']} page {item['page']}\n{item['text']}"
                for i, item in enumerate(knowledge["items"])
            )
            with self.lock:
                web_sources = [
                    source
                    for source in self.run(run_id).get("citations", [])
                    if source.get("kind") == "web"
                ]
                self._update_run(
                    run_id,
                    citations=[*knowledge["items"], *web_sources],
                    retrieval_notice=knowledge["notice"],
                )
        with self.artifacts.execution_context(payload, provider_name, prompt) as (
            prompt,
            content_metadata,
        ):
            history_text = ""
            if history:
                history_text = "\nConversation so far:\n" + "\n".join(
                    f"{entry['role']}: {entry['content']}" for entry in history[-40:]
                )
            prompt, budget = self.workbench.budget_prompt(
                payload, prompt + f"\nUser task:\n{task}", history_text
            )
            if budget:
                self._update_run(run_id, prompt_budget=budget)
            context = ExecutionContext(
                agent_name=str(agent.get("agent_type") or "studio"),
                prompt=prompt,
                workspace=self.root,
                model_override=model,
                effort=effort,
                metadata={**content_metadata, "worker_tools": payload.get("worker_tools")},
                timeout=int(agent.get("timeout_seconds") or 600),
            )
            stream = getattr(provider, "execute_agent_streaming", None)
            if agent.get("tool_ids"):
                from nexus.core.studio_agent_settings import call_limit
                from nexus.core.studio_tool_loop import execute_with_tools

                async with self.workbench.presence(
                    payload, run_id, payload.get("agent_id"), provider_name
                ):
                    result = await execute_with_tools(
                        self,
                        provider,
                        context,
                        agent["tool_ids"],
                        payload,
                        run_id,
                        provider_name,
                        max_calls=call_limit(agent),
                        connector_tools=agent.get("connector_tools", {}),
                    )
                    from nexus.core.studio_agent_settings import validate_output

                    validate_output(agent, result.output)
                    self.observability.output(
                        result.output, agent.get("guardrails"), run_id, payload
                    )
                    return result
            async with self.workbench.presence(
                payload, run_id, payload.get("agent_id"), provider_name
            ):
                result = await self.observability.invoke(
                    provider,
                    context,
                    run_id,
                    payload,
                    provider_name,
                    stream if not agent.get("guardrails") else None,
                )
            self.observability.output(result.output, agent.get("guardrails"), run_id, payload)
            if not result.success:
                from nexus.core.studio_workflow_retry import StudioAgentFailureError

                raise StudioAgentFailureError(
                    result.error or result.output or "Provider execution failed"
                )
            from nexus.core.studio_agent_settings import validate_output

            validate_output(agent, result.output)
            return result

    async def _execute_async(self, run_id: str, payload: dict, session_id: str) -> None:
        if self.team:
            self.team.assert_execution(payload.get("requester_nexus_id"))
        kind = payload.get("kind", "chat")
        if kind == "speech":
            output = await self.speech.execute(run_id, payload)
        elif kind == "design":
            output = await self.designer.generate(run_id, payload)
        elif kind == "knowledge-index":
            document = self._read(self._path("knowledge-documents", payload.get("document_id")))
            expected = payload.get("requester_nexus_id") or "desktop"
            if document["owner_id"] != expected and not (
                self.team and self.team.bindings.get(expected) == "owner"
            ):
                raise PermissionError("Index job belongs to another account")
            output = await asyncio.to_thread(self.knowledge.index, document["id"], run_id)
        elif kind in {"delegation", "campaign", "transcription", "decision", "external"}:
            output = await self.workbench.jobs.execute(run_id, payload)
        elif kind == "evaluation":
            output = await self.operations.evaluate(run_id, payload)
        elif kind == "build":
            output = await self.builder.generate(run_id, payload)
        elif kind == "tool":
            from nexus.tools import tool_registry

            self.execution.record(run_id, "tool", tool=payload["tool"], status="running")
            try:
                result = await tool_registry.call(
                    payload["tool"], *payload.get("args", []), **payload.get("kwargs", {})
                )
            except BaseException:
                self.execution.record(run_id, "tool", tool=payload["tool"], status="failed")
                raise
            self.execution.record(
                run_id, "tool", tool=payload["tool"], status="completed" if result.ok else "failed"
            )
            if not result.ok:
                raise RuntimeError(result.error or "Tool failed")
            output = (
                result.output
                if isinstance(result.output, str)
                else json.dumps(result.output, ensure_ascii=False, default=str)
            )
        elif kind == "workflow":
            output = await self._workflow(run_id, payload)
        else:
            path = self._path("sessions", session_id)
            with self.lock:
                session = (
                    self._read(path) if path.exists() else self._new_session(session_id, payload)
                )
                history = [m for m in session["messages"] if m.get("status") != "failed"]
            result = await self._invoke(
                payload, payload.get("expanded_task", payload["task"]), run_id, history
            )
            output, embed_parts = self.dynamic_embeds.complete(result.output, payload, run_id)
            structured = self.conversations.content(
                result,
                payload,
                additional=[*self.run(run_id).get("structured_content", []), *embed_parts],
            )
            if structured:
                self._update_run(run_id, content_version=1, structured_content=structured)
                for part in structured:
                    if part["type"] == "artifact":
                        self.execution.record(
                            run_id,
                            "artifact",
                            status="referenced",
                            artifact_id=part["artifact_id"],
                            artifact_revision=part["revision"],
                        )
            session["messages"].extend(
                [
                    {"role": "user", "content": payload["task"]},
                    {
                        "role": "assistant",
                        "content": output,
                        "run_id": run_id,
                        "citations": self.run(run_id).get("citations", []),
                        "retrieval_notice": self.run(run_id).get("retrieval_notice"),
                        "retrieval": payload.get("knowledge_snapshot", {}).get("retrieval"),
                        "provider": self.run(run_id)["provider"],
                        **(
                            {"content_version": 1, "structured_content": structured}
                            if structured
                            else {}
                        ),
                    },
                ]
            )
            session.update(
                updated_at=_now(),
                provider=self.run(run_id)["provider"],
                agent_id=payload.get("agent_id"),
                model=payload.get("model") or "",
                effort=payload.get("effort") or "",
                studio_assistant=bool(payload.get("studio_assistant")),
            )
            with self.lock:
                if self.run(run_id).get("cancel_requested"):
                    raise asyncio.CancelledError
                self._write(path, session)
        if self.run(run_id).get("cancel_requested"):
            raise asyncio.CancelledError
        additions = {}
        try:
            artifact = (
                self.catalog.capture_run({**self.run(run_id), "output": output})
                if kind not in {"knowledge-index", "design", "speech"}
                else None
            )
            if artifact:
                additions["artifact_id"] = artifact["id"]
                additions["artifact_revision"] = artifact["revision"]
        except (ValueError, OSError) as exc:
            additions["artifact_error"] = str(exc)
        with self.lock:
            self.active_builds.discard(payload.get("build_id"))
            self.active_sessions.discard(session_id)
            self._update_run(run_id, status="completed", output=output, **additions)

    async def _workflow(self, run_id: str, payload: dict) -> str:

        if payload.get("resume_graph"):
            return await self.graph.execute(run_id, payload)
        document = payload.get("workflow_snapshot") or self.definition(
            "workflows", payload["workflow_id"]
        )
        if "graph" in document:
            return await self.graph.execute(run_id, payload, document)
        return await self.legacy.execute(run_id, payload, document)

    def cancel_run(self, identity, actor=None):
        with self.lock:
            run = self.collaboration.owned(self.run(identity), actor)
            if run["status"] == "interrupted" and run.get("cancellation_cleanup"):
                from nexus.core.studio_workflow_cancellation import request_cleanup

                if request_cleanup(self, identity):
                    return self.run(identity)
            if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                raise ValueError("Task is already terminal")
            if run.get("cancel_requested") and run["status"] != "waiting":
                return run
            if run["status"] in {"queued", "waiting"}:
                from nexus.core.studio_workflow_cancellation import request_cleanup

                if request_cleanup(self, identity, enqueue=run["status"] != "queued"):
                    return self.run(identity)
            if run["status"] == "waiting" and run.get("awaiting_approval"):
                return self.graph.resume(identity, "reject")
            self._update_run(
                identity,
                cancel_requested=True,
                status="cancelled" if run["status"] in {"queued", "waiting"} else run["status"],
            )
            active = self.active_tasks.get(identity)
            if active:
                active[0].call_soon_threadsafe(active[1].cancel)
            if run.get("kind") == "speech":
                self.speech.cleanup()
            return self.run(identity)

    def validate_workflow(self, identity: str) -> dict:
        document = self.definition("workflows", identity)
        if "graph" in document:
            graph = self.graph.validate(document["graph"])
            return {
                "valid": True,
                "errors": [],
                "warnings": [],
                "flow": [edge["source"] + " → " + edge["target"] for edge in graph["edges"]],
            }
        report = WorkflowDefinition.dry_run(document)
        return {
            "valid": report.is_valid,
            "errors": report.errors,
            "warnings": [],
            "flow": report.predicted_flow,
        }

    def close(self) -> None:
        self.closing = True
        for listener in list(self.a2a.grpc_servers.values()):
            listener.close()
        if self.sql_state:
            self.executor.stop.set()
        self.operations.close()
        with self.lock:
            tasks = list(self.active_tasks.values())
        for loop, task in tasks:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The task's loop finished between the snapshot and shutdown.
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.connections.close()
        self.data_tools.close()
        self.research.close()
        self.media.close()
        self.dynamic_embeds.close()
        self.rulesets.close()
        self.assistant.close()
        for run in [] if self.sql_state else self.records("runs", limit=None):
            if run["status"] in {"running", "queued"}:
                self._update_run(
                    run["id"],
                    status="interrupted",
                    error="Studio stopped before the task completed",
                )
        if not self.sql_state:
            self.workbench.jobs.cleanup_audio()
        self.speech.cleanup()
        self.transport.close()
        if self.sql_state:
            self.sql_state.engine.dispose()
