"""Owned Studio controls backed by ARC checkpoints, guards, peers and audit queries."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime

from nexus.agents.peer_registry import PeerRegistry
from nexus.core.guards import LaunchGuard, LaunchRecord
from nexus.core.prompt_budget import apply_prompt_budget
from nexus.core.studio_catalog import _text
from nexus.workflows.checkpoints import SQLiteCheckpointStore


def now():
    return datetime.now(UTC).isoformat()


class StudioWorkbench:
    def __init__(
        self,
        studio,
        *,
        peer_registry=None,
        audit_provider=None,
        publisher_factory=None,
        transcription_factory=None,
        decision_client=None,
        router_client=None,
        memory_sources=None,
        redis_factory=None,
    ):
        self.studio = studio
        self.router_client = router_client
        self.peers = peer_registry if peer_registry is not None else PeerRegistry()
        self.audit_provider = audit_provider
        self.audit_error = None
        self.guards = {}
        from nexus.core.studio_memory_sources import StudioMemorySources

        self.memory_sources = StudioMemorySources(self, memory_sources, redis_factory)
        from nexus.core.studio_campaigns import StudioCampaigns
        from nexus.core.studio_jobs import StudioJobs

        self.campaigns = StudioCampaigns(self, publisher_factory)
        self.jobs = StudioJobs(self, transcription_factory, decision_client)
        from nexus.core.studio_inbox import StudioInbox

        self.inbox = StudioInbox(studio)
        from nexus.core.studio_onboarding import StudioOnboarding

        self.onboarding = StudioOnboarding(studio)

    def actor(self, payload):
        if not self.studio.team:
            return None
        identity = payload.get("requester_nexus_id")
        self.studio.team.assert_execution(identity)
        return {"nexus_id": identity, "role": self.studio.team.bindings[identity]}

    def owner(self, actor):
        if self.studio.team and not actor:
            raise PermissionError("Nexus sign-in required")
        return actor["nexus_id"] if actor else None

    def visible(self, record, actor, *, own=False):
        identity = self.owner(actor)
        if self.studio.team:
            allowed = record.get("owner_id", record.get("requester_nexus_id")) == identity
            if not allowed and (own or actor["role"] != "owner"):
                raise PermissionError("This resource belongs to another user")
        return record

    def records(self, collection, actor):
        self.owner(actor)
        return [
            record
            for record in self.studio.records(collection, limit=None)
            if not self.studio.team or self.studio.team.visible(record, actor)
        ]

    def memory(self, actor):
        return self.records("sessions", actor)

    def edit_memory(self, identity, payload, actor):
        with self.studio.lock:
            record = self.visible(self.studio.session(identity), actor)
            if identity in self.studio.active_sessions:
                raise ValueError("Wait for this conversation to finish before editing it")
            if "title" in payload:
                record["title"] = _text(payload["title"], "Conversation name", 200)
            if "archived" in payload:
                if type(payload["archived"]) is not bool:
                    raise ValueError("Archived must be true or false")
                record["archived"] = payload["archived"]
            record["updated_at"] = now()
            self.studio._write(self.studio._path("sessions", identity), record)
            return {key: value for key, value in record.items() if key != "messages"}

    def capture_checkpoint(self, run_id, actor):
        with self.studio.lock:
            run = self.visible(self.studio.run(run_id), actor)
            if run["status"] in {"queued", "running"}:
                raise ValueError(
                    "Wait for the workflow to pause or stop before capturing a checkpoint"
                )
            path = self.studio._path("graph-runs", run_id)
            if not path.exists():
                path = self.studio._path("yaml-runs", run_id)
            if not path.exists():
                raise ValueError("This workflow has no saved canvas or YAML runtime state")
            state = self.studio._read(path)
            snapshot = {
                "id": run_id,
                "owner_id": run.get("requester_nexus_id"),
                "run_id": run_id,
                "status": run["status"],
                "task": run["task"],
                "updated_at": now(),
                "state": state,
                "digest": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            if self.studio.sql_state:
                self.studio._write(self.studio._path("checkpoint-snapshots", run_id), snapshot)
            else:
                store_path = self.studio._path("checkpoints", run_id, "sqlite")
                store_path.parent.mkdir(parents=True, exist_ok=True)
                store = SQLiteCheckpointStore(store_path)
                try:
                    store["snapshot"] = snapshot
                finally:
                    store.close()
            self.studio._write(
                self.studio._path("checkpoint-index", run_id),
                {key: value for key, value in snapshot.items() if key != "state"},
            )
            return snapshot

    def checkpoint(self, identity, actor):
        self.visible(self.studio._read(self.studio._path("checkpoint-index", identity)), actor)
        if self.studio.sql_state:
            return self.visible(
                self.studio._read(self.studio._path("checkpoint-snapshots", identity)), actor
            )
        path = self.studio._path("checkpoints", identity, "sqlite")
        if not path.is_file():
            raise FileNotFoundError("Checkpoint not found")
        store = SQLiteCheckpointStore(path)
        try:
            return self.visible(store["snapshot"], actor)
        finally:
            store.close()

    def resume_checkpoint(self, identity, payload, actor):
        with self.studio.lock:
            snapshot = self.checkpoint(identity, actor)
            self.visible(snapshot, actor, own=True)
            path = self.studio._path("graph-runs", snapshot["run_id"])
            if snapshot["state"].get("format") == "yaml":
                path = self.studio._path("yaml-runs", snapshot["run_id"])
            if (
                payload.get("digest") != snapshot["digest"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != snapshot["digest"]
            ):
                raise ValueError(
                    "Workflow state changed. Capture and review its current checkpoint"
                )
            run = self.studio.run(snapshot["run_id"])
            if run["status"] == "waiting":
                if payload.get("decision") not in {"approve", "reject"}:
                    raise ValueError("Choose approve or reject for the pending gate")
                return self.studio.graph.resume(run["id"], payload["decision"])
            if run["status"] not in {"failed", "interrupted"}:
                raise ValueError("Only failed or interrupted workflows can resume unfinished steps")
            if self.studio.team:
                self.studio.team.assert_execution(run.get("requester_nexus_id"))
            self.studio._update_run(run["id"], status="queued", error=None)
            queued = self.studio.run(run["id"])
            self.studio.resume_workflow(
                run["id"],
                {
                    **snapshot["state"]["payload"],
                    "resume_yaml"
                    if snapshot["state"].get("format") == "yaml"
                    else "resume_graph": True,
                },
            )
            return queued

    def settings_key(self, actor):
        return hashlib.sha256(str(self.owner(actor) or "desktop").encode()).hexdigest()[:32]

    def controls(self, actor):
        key = self.settings_key(actor)
        path = self.studio._path("execution-controls", key)
        values = (
            self.studio._read(path)
            if path.exists()
            else {
                "id": key,
                "owner_id": self.owner(actor),
                "revision": 0,
                "prompt_budget_enabled": False,
                "max_prompt_chars": 65536,
                "summary_max_chars": 1200,
                "cooldown_seconds": 0,
            }
        )
        return {**values, "active_cooldowns": self.guard(actor, values).active_count}

    def save_controls(self, payload, actor):
        with self.studio.lock:
            current = self.controls(actor)
            if payload.get("revision") != current["revision"]:
                raise ValueError("Controls changed. Refresh before saving")
            enabled = payload.get("prompt_budget_enabled", False)
            maximum = payload.get("max_prompt_chars", 65536)
            summary = payload.get("summary_max_chars", 1200)
            cooldown = payload.get("cooldown_seconds", 0)
            if (
                type(enabled) is not bool
                or type(maximum) is not int
                or not 1024 <= maximum <= 262144
            ):
                raise ValueError("Prompt budget must be 1024–262144 characters")
            if type(summary) is not int or not 100 <= summary <= min(16000, maximum):
                raise ValueError(
                    "Summary size must be 100–16000 characters and fit the prompt budget"
                )
            if type(cooldown) is not int or not 0 <= cooldown <= 3600:
                raise ValueError("Cooldown must be 0–3600 seconds")
            record = {
                "id": current["id"],
                "owner_id": self.owner(actor),
                "revision": current["revision"] + 1,
                "updated_at": now(),
                "prompt_budget_enabled": enabled,
                "max_prompt_chars": maximum,
                "summary_max_chars": summary,
                "cooldown_seconds": cooldown,
            }
            self.studio._write(self.studio._path("execution-controls", current["id"]), record)
            self.guards.pop(current["id"], None)
            return self.controls(actor)

    def guard(self, actor, settings=None):
        settings = settings or {"cooldown_seconds": 0}
        key = self.settings_key(actor)
        cooldown = settings["cooldown_seconds"]
        if self.studio.sql_state or key not in self.guards or self.guards[key][0] != cooldown:
            guard = LaunchGuard(cooldown_seconds=cooldown)
            path = self.studio._path("launch-ledger", key)
            if path.exists():
                for record in self.studio._read(path).get("launches", []):
                    item = LaunchRecord(**record)
                    guard._launches[f"{item.issue_id}:{item.agent_type}"] = item
            guard.cleanup_expired()
            self.guards[key] = (cooldown, guard)
        return self.guards[key][1]

    def reserve_launch(self, payload):
        actor = self.actor(payload)
        settings = self.controls(actor)
        if not settings["cooldown_seconds"]:
            return None
        guard = self.guard(actor, settings)
        kind = payload.get("kind", "chat")
        target = str(
            payload.get("agent_id")
            or payload.get("workflow_id")
            or payload.get("build_id")
            or payload.get("input_id")
            or kind
        )
        issue = hashlib.sha256(
            json.dumps(
                [kind, target, payload.get("task"), payload.get("provider"), payload.get("model")],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if not guard.can_launch(issue, target):
            raise ValueError(
                "A matching task was recently launched. Wait for your configured cooldown"
            )
        guard.record_launch(issue, target)
        self.persist_guard(actor, guard)
        return actor, issue, guard

    def persist_guard(self, actor, guard):
        self.studio._write(
            self.studio._path("launch-ledger", self.settings_key(actor)),
            {"launches": [asdict(record) for record in guard._launches.values()]},
        )

    def release_launch(self, claim):
        if claim:
            actor, issue, guard = claim
            guard.clear(issue)
            self.persist_guard(actor, guard)

    def budget_prompt(self, payload, prompt, history):
        settings = self.controls(self.actor(payload))
        if not settings["prompt_budget_enabled"]:
            return history + prompt, None
        maximum = settings["max_prompt_chars"]
        if len(prompt) > maximum:
            raise ValueError(
                "Task and instructions exceed your prompt budget. Shorten them or increase the limit"
            )
        available = maximum - len(prompt)
        budget = apply_prompt_budget(
            history,
            max_chars=available,
            summary_max_chars=min(settings["summary_max_chars"], available),
        )
        return budget["text"] + prompt, {
            key: value for key, value in budget.items() if key != "text"
        }

    @asynccontextmanager
    async def presence(self, payload, run_id, agent_id, provider):
        actor = self.actor(payload)
        name = f"{agent_id or 'assistant'}-{run_id[:8]}"
        metadata = {
            "owner_id": self.owner(actor),
            "agent_id": agent_id,
            "provider": provider,
            "run_id": run_id,
            "state": "running",
        }

        def pulse():
            self.peers.heartbeat(name, description="Active Studio invocation", metadata=metadata)

        pulse()

        async def heartbeat():
            while True:
                await asyncio.sleep(25)
                pulse()

        task = asyncio.create_task(heartbeat())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            self.peers.heartbeat(
                name,
                description="Recent Studio invocation",
                metadata={**metadata, "state": "finished"},
            )

    def peer_cards(self, actor):
        self.owner(actor)
        cards = []
        for card in self.peers.all():
            metadata = card.metadata
            if (
                self.studio.team
                and actor["role"] != "owner"
                and metadata.get("owner_id") != self.owner(actor)
            ):
                continue
            cards.append(
                {
                    "name": card.name,
                    "description": card.description,
                    "skills": list(card.skills),
                    "last_seen": card.last_seen,
                    "agent_id": metadata.get("agent_id"),
                    "provider": metadata.get("provider"),
                    "state": metadata.get("state", "advertised"),
                }
            )
        return cards

    def audit(self, run):
        try:
            event = {
                "timestamp": now(),
                "event_type": "studio.run." + run["status"],
                "run_id": run["id"],
                "owner_id": run.get("requester_nexus_id"),
                "kind": run["kind"],
                "status": run["status"],
                "provider": run.get("provider", "local"),
            }
            if run["status"] in {"completed", "failed", "interrupted", "cancelled"} and run.get("created_at"):
                event["duration_seconds"] = max(
                    0,
                    (datetime.now(UTC) - datetime.fromisoformat(run["created_at"])).total_seconds(),
                )
            if self.studio.sql_state:
                import uuid

                self.studio._write(self.studio._path("audit-records", uuid.uuid4().hex), event)
            path = self.studio._path("audit-events", datetime.now(UTC).strftime("%Y-%m"), "jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
        except (ValueError, OSError):
            self.audit_error = "Execution audit could not be written"

    def analytics(self, actor, hours=24):
        from nexus.adapters.analytics.file_audit_query import FileAuditQueryProvider

        self.owner(actor)
        hours = int(hours)
        if not 1 <= hours <= 8760:
            raise ValueError("Choose 1–8760 hours")
        source = self.audit_provider or FileAuditQueryProvider(
            self.studio._path("audit-events", "index").parent
        )
        if self.studio.sql_state and not self.audit_provider:
            from datetime import timedelta

            cutoff = datetime.now(UTC) - timedelta(hours=hours)
            events = [
                item
                for item in self.studio.records("audit-records", limit=None)
                if datetime.fromisoformat(item["timestamp"]) >= cutoff
            ]
        else:
            events = source.get_events(hours)
        if self.studio.team and actor["role"] != "owner":
            events = [event for event in events if event.get("owner_id") == self.owner(actor)]
        latest = {}
        for event in events:
            if event.get("run_id"):
                latest[event["run_id"]] = event
        statuses, providers = {}, {}
        for event in latest.values():
            status = str(event.get("status", "unknown"))
            provider = str(event.get("provider", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
            providers[provider] = providers.get(provider, 0) + 1
        # Return metadata only, including for an injected audit source.
        allowed = {
            "timestamp",
            "event_type",
            "run_id",
            "kind",
            "status",
            "provider",
            "duration_seconds",
        }
        return {
            "hours": hours,
            "run_count": len(latest),
            "statuses": statuses,
            "providers": providers,
            "events": [{k: v for k, v in event.items() if k in allowed} for event in events[-500:]],
            "source": type(source).__name__,
            "error": self.audit_error,
        }

    def get(self, parts, actor, query=None):
        query = query or {}
        area = parts[0] if parts else ""
        if area == "readiness":
            from nexus.core.studio_readiness import deployment_readiness

            return deployment_readiness(self.studio, actor)
        if area == "inbox":
            items = self.inbox.items(actor)
            if len(parts) == 2:
                item = next((item for item in items if item["id"] == parts[1]), None)
                if item is None:
                    raise FileNotFoundError("Review item was resolved or is not visible")
                return item
            return {
                "items": [
                    {key: value for key, value in item.items() if key != "detail"} for item in items
                ]
            }
        if area == "versions" and len(parts) == 3:
            return self.studio.versions.list(parts[1], parts[2])
        if area == "effects":
            return {"items": self.records("integration-effects", actor)}
        if area == "channels":
            return {"items": self.records("channel-events", actor)}
        if area == "metrics":
            return {
                "format": "prometheus",
                "text": self.studio.telemetry.export_prometheus_metrics(actor),
            }
        if area == "traces":
            if len(parts) == 3 and parts[2] == "otel":
                return self.studio.telemetry.export_opentelemetry_traces(parts[1], actor)
            if len(parts) == 3 and parts[2] == "usage":
                return self.studio.telemetry.summarize_run_tokens(parts[1], actor)
            if len(parts) == 2:
                self.visible(self.studio.run(parts[1]), actor)
                return {
                    name: [
                        item
                        for item in self.records(collection, actor)
                        if item.get("run_id") == parts[1]
                    ]
                    for name, collection in [
                        ("invocations", "invocation-traces"),
                        ("nodes", "node-traces"),
                        ("tools", "tool-traces"),
                        ("guardrails", "guardrail-events"),
                    ]
                }
        if area == "memorysources":
            return (
                {"items": self.memory_sources.list_sources(actor)}
                if len(parts) == 1
                else {"items": self.memory_sources.chats(parts[1], actor)}
            )
        if area == "memory":
            if len(parts) == 1:
                return {"items": self.memory(actor)}
            record = self.visible(self.studio.session(parts[1]), actor)
            return (
                {"format": "nexus-studio-memory/v1", "conversation": record}
                if len(parts) == 3 and parts[2] == "export"
                else record
            )
        if area == "checkpoints":
            if len(parts) == 1:
                return {
                    "items": self.records("checkpoint-index", actor),
                    "runs": [
                        record
                        for record in self.records("runs", actor)
                        if record["kind"] == "workflow"
                        and record["status"] not in {"queued", "running"}
                        and (
                            self.studio._path("graph-runs", record["id"]).exists()
                            or self.studio._path("yaml-runs", record["id"]).exists()
                        )
                    ],
                }
            return self.checkpoint(parts[1], actor)
        if area == "storage":
            return {
                "active": self.studio.artifacts.profile()["id"],
                "profiles": [
                    self.studio.artifacts.profile("local"),
                    *self.studio.records("artifact-backends", limit=None),
                ],
            }
        if area == "externalagents":
            return {
                "items": self.studio.catalog.list("externalagents"),
                "runs": self.records("external-runs", actor),
            }
        if area == "agents":
            return {
                "peers": self.peer_cards(actor),
                "scope": "Local invocations or an explicitly injected peer registry. No remote dispatch is implied.",
            }
        if area == "campaigns":
            return (
                {"items": self.records("campaigns", actor)}
                if len(parts) == 1
                else self.visible(
                    self.studio._read(self.studio._path("campaigns", parts[1])), actor
                )
            )
        if area == "analytics":
            return self.analytics(actor, query.get("hours", ["24"])[0])
        if area in {"transcriptions", "decisions"}:
            return {"items": self.records(area, actor)}
        if area == "controls":
            return self.controls(actor)
        if area == "guide" and len(parts) == 1:
            return self.onboarding.guide_state(actor)
        raise FileNotFoundError("Unknown workbench area")

    def post(self, parts, payload, actor):
        self.owner(actor)
        if parts == ["guide"]:
            return 200, self.onboarding.save_guide_state(payload, actor)
        if len(parts) == 3 and parts[0] == "onboarding":
            if parts[2] == "check":
                return 200, self.onboarding.probe(parts[1], payload, actor)
            if parts[2] == "enable":
                return 200, self.onboarding.enable(parts[1], payload, actor)
        if len(parts) == 3 and parts[0] == "inbox" and parts[2] == "review":
            return 200, self.inbox.review(parts[1], payload, actor)
        if len(parts) == 4 and parts[0] == "versions" and parts[3] == "activate":
            return 200, self.studio.versions.activate(parts[1], parts[2], payload, actor)
        if len(parts) == 3 and parts[0] == "effects" and parts[2] == "resolve":
            from nexus.core.studio_nodes import StudioNodeTypes

            return 200, StudioNodeTypes(self.studio).resolve(parts[1], payload, actor)
        if parts == ["memory", "import"]:
            return 201, self.memory_sources.import_document(payload.get("document"), actor)
        if len(parts) == 3 and parts[0] == "memorysources":
            return 201, self.memory_sources.import_chat(parts[1], parts[2], actor)
        if len(parts) == 3 and parts[0] == "memory" and parts[2] == "edit":
            return 200, self.edit_memory(parts[1], payload, actor)
        if len(parts) == 3 and parts[0] == "checkpoints":
            if parts[2] == "capture":
                return 201, self.capture_checkpoint(parts[1], actor)
            if parts[2] == "resume":
                return 202, self.resume_checkpoint(parts[1], payload, actor)
        if parts == ["storage", "save"]:
            return 201, self.studio.artifacts.save_profile(payload)
        if len(parts) == 3 and parts[0] == "storage":
            if parts[2] == "test":
                return 200, self.studio.artifacts.test(parts[1])
            if parts[2] == "activate":
                return 200, self.studio.artifacts.activate(parts[1])
        if len(parts) == 3 and parts[0] == "connections" and parts[2] == "action":
            result = asyncio.run(
                self.studio.connections.execute(parts[1], payload, self.owner(actor))
            )
            return 200, {"output": json.dumps(result, ensure_ascii=False, default=str)}
        if len(parts) == 3 and parts[0] == "externalagents" and parts[2] == "discover":
            return 200, self.studio.external.discover(parts[1], self.owner(actor))
        if len(parts) == 3 and parts[0] == "externalagents" and parts[2] == "continue":
            return 202, self.studio.external.continue_task(parts[1], payload, actor)
        if len(parts) == 3 and parts[0] == "externalagents" and parts[2] == "cancel":
            return 200, self.studio.external.cancel(parts[1], actor)
        if len(parts) == 3 and parts[0] == "externalagents" and parts[2] == "refresh":
            return 200, self.studio.external.refresh_task(parts[1], actor)
        if parts == ["controls", "save"]:
            return 200, self.save_controls(payload, actor)
        if parts == ["controls", "preview"]:
            settings = self.controls(actor)
            return 200, apply_prompt_budget(
                _text(payload.get("text"), "Preview text", 262144),
                max_chars=settings["max_prompt_chars"],
                summary_max_chars=settings["summary_max_chars"],
            )
        if parts == ["controls", "reset"]:
            with self.studio.lock:
                guard = self.guard(actor, self.controls(actor))
                guard.clear()
                self.persist_guard(actor, guard)
                return 200, self.controls(actor)
        if parts and parts[0] == "campaigns":
            return self.campaigns.post(parts[1:], payload, actor)
        if parts == ["agents", "delegate"]:
            return 202, self.studio.start_run({**payload, "kind": "delegation"})
        if parts == ["transcriptions"]:
            return 202, self.jobs.upload_transcription(payload, actor)
        if parts == ["decisions"]:
            return 202, self.studio.start_run({**payload, "kind": "decision"})
        raise FileNotFoundError("Unknown workbench action")
