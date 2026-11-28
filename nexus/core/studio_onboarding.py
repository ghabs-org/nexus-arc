"""Read-only connection checks and interactive Studio onboarding guide state."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from nexus.core.studio_team import ROLES

GUIDE_VERSION = 2

GUIDE_STEPS = (
    {
        "id": "workspace-navigation",
        "version": 1,
        "title": "Workspace navigation",
        "view": "chat",
        "target": "#workspace-navigation",
        "fallback_target": ".sidebar",
        "min_role": "viewer",
        "summary": (
            "Move between Conversations, Quick Build, Agent Library, Workflows, "
            "Knowledge and Connections, and the Activity and Approval Inbox from the navigation rail."
        ),
        "branch_title": "Keyboard & mobile navigation",
        "branch_body": (
            "On narrow screens, open the Navigation drawer from the top bar. "
            "You can move through guide steps with Back and Next or the Left and Right arrow keys."
        ),
        "practice": {
            "label": "Preview workspace map",
            "preview": (
                "Sample map (local preview only): Work holds daily Conversations and Quick Build; "
                "Resources holds reusable Agents, Workflows, and Knowledge; Operations tracks Runs and Approvals."
            ),
        },
    },
    {
        "id": "providers",
        "version": 1,
        "title": "Provider and account selection",
        "view": "providers",
        "target": "#view-providers",
        "fallback_target": "#chat-provider",
        "min_role": "viewer",
        "summary": (
            "Studio supports subscription-backed CLI tools (Codex, Claude Code, Antigravity, "
            "Copilot, OpenCode) alongside API-billed providers (OpenAI and Claude APIs). "
            "Review readiness here without starting sign-in or running a live check."
        ),
        "branch_title": "Subscription CLI vs API keys",
        "branch_body": (
            "Subscription CLIs reuse your local CLI sign-in (or an isolated user container in shared mode), "
            "while API providers bill per token using configured environment credentials."
        ),
        "practice": {
            "label": "Compare provider modes",
            "preview": (
                "Sample comparison (local preview only): CLI subscription = existing account login + effort controls; "
                "API key = direct HTTP requests billed by token usage."
            ),
        },
    },
    {
        "id": "conversations",
        "version": 1,
        "title": "Conversations",
        "view": "chat",
        "target": "#chat-form",
        "fallback_target": "#view-chat",
        "min_role": "operator",
        "summary": (
            "Pick an assistant (including the built-in Studio Assistant), provider, model, and reasoning effort, "
            "attach workspace files with drag-and-drop chips and artifact cards, and inspect structured answers and run history in one thread."
        ),
        "branch_title": "Working with attachments & effort",
        "branch_body": (
            "Drop files onto the upload target or select indexed workspace artifacts. "
            "Choose Studio Assistant for zero-config workspace help without using provider tokens."
        ),
        "practice": {
            "label": "Try sample conversation preview",
            "preview": (
                "Sample prompt (not sent to any provider): 'Summarize open release risks in CHANGELOG.md "
                "and list three verification checks.' → Produces a structured checklist in the conversation panel."
            ),
        },
    },
    {
        "id": "quick-build",
        "version": 1,
        "title": "Quick Build",
        "view": "quick-build",
        "target": "#builder-form",
        "fallback_target": "#view-quick-build",
        "min_role": "builder",
        "summary": (
            "Describe what you want to automate in plain language, then review the proposed agents, "
            "workflow steps, and file diffs before applying anything to your workspace."
        ),
        "branch_title": "Reviewing generated diffs safely",
        "branch_body": (
            "Quick Build stages a draft proposal first. You can inspect every generated definition and "
            "run a test before saving changes to disk."
        ),
        "practice": {
            "label": "Preview sample build draft",
            "preview": (
                "Sample build draft (local preview only): Goal 'Triage incoming support notes' → "
                "Proposes 1 classifier agent + 1 two-step workflow for review before saving."
            ),
        },
    },
    {
        "id": "agents",
        "version": 1,
        "title": "Agents",
        "view": "agents",
        "target": "#view-agents",
        "fallback_target": "#agents-content",
        "min_role": "builder",
        "summary": (
            "Create and manage specialist agents with instructions, provider, model, allowed tools, "
            "multiple skill assignments, and output guardrails, or use ready-made built-in specialists."
        ),
        "branch_title": "Versioning, multi-skills, and instructions",
        "branch_body": (
            "Every saved change records a version snapshot. You can assign multiple skills to an agent, "
            "import skill bundles from GitHub, or consult built-in specialists like Studio Assistant."
        ),
        "practice": {
            "label": "Preview sample agent card",
            "preview": (
                "Sample specialist (local preview only): 'Release Auditor' using Claude Code (high effort) "
                "with read-only repository tools, multiple skills, and a 600s timeout."
            ),
        },
    },
    {
        "id": "workflows",
        "version": 1,
        "title": "Workflows",
        "view": "workflows",
        "target": "#view-workflows",
        "fallback_target": "#workflow-form",
        "min_role": "builder",
        "summary": (
            "Inspect multi-step workflows, conditional branches, visual canvas nodes, input forms, "
            "and saved YAML definitions, or trigger workflows directly from schedules."
        ),
        "branch_title": "Canvas, YAML, and checkpoints",
        "branch_body": (
            "Switch between the step builder, visual canvas, and YAML view. Edit or remove steps, "
            "resume checkpoints, or schedule workflows with custom input payloads."
        ),
        "practice": {
            "label": "Preview sample workflow branch",
            "preview": (
                "Sample workflow (local preview only): Step 1 'Analyze request' → Branch on confidence → "
                "Step 2 'Prepare response' or pause at an approval gate."
            ),
        },
    },
    {
        "id": "projects",
        "version": 1,
        "title": "Knowledge projects",
        "view": "projects",
        "target": "#view-projects",
        "fallback_target": "#projects-content",
        "min_role": "builder",
        "summary": (
            "Organize shared instructions, reference documents, and related conversations into "
            "Knowledge projects so agents can ground their answers in your project material."
        ),
        "branch_title": "Document retrieval and citations",
        "branch_body": (
            "When a conversation selects a Knowledge project, matching passages are cited alongside "
            "the assistant response and can be exported or imported as a portable package."
        ),
        "practice": {
            "label": "Preview sample knowledge project",
            "preview": (
                "Sample project (local preview only): 'Architecture RFCs' with 3 indexed markdown specs "
                "and automatic source citations in linked chats."
            ),
        },
    },
    {
        "id": "workspace-files",
        "version": 2,
        "title": "Workspace files",
        "view": "files",
        "target": "#view-files",
        "fallback_target": "#files-list",
        "min_role": "builder",
        "summary": (
            "Browse and preview files in allowed server folders, upload documents up to 16 MiB, "
            "or create folders directly on the Studio VM."
        ),
        "branch_title": "Allowed roots and server security",
        "branch_body": (
            "Only explicitly configured folders (such as agents, workflows, and authorized project roots) "
            "can be opened. Files stay on the server until explicitly shared or attached."
        ),
        "practice": {
            "label": "Preview workspace file browser",
            "preview": (
                "Sample browser (local preview only): Root 'agents' → Lists specialist markdown specs "
                "with file sizes and instant previews."
            ),
        },
    },
    {
        "id": "connectors-knowledge",
        "version": 1,
        "title": "Library and connectors",
        "view": "library",
        "target": "#integrations-panel",
        "fallback_target": "#view-library",
        "min_role": "owner",
        "summary": (
            "Manage reusable Prompts, Skills, Artifacts, Toolsets, Connectors, Entrypoints, Schedules, "
            "Evaluations, External A2A agents, and Model profiles in the Library."
        ),
        "branch_title": "Schedules, skills, and connector checks",
        "branch_body": (
            "Schedules can trigger either an agent or a ready-made workflow with custom inputs. "
            "Skills support multi-selection import from GitHub, and connectors require read-only verification before enabling."
        ),
        "practice": {
            "label": "Preview connector readiness states",
            "preview": (
                "Sample readiness (local preview only): Draft configuration → Read-only revision check → "
                "Explicit owner confirmation → Enabled for selected agents."
            ),
        },
    },
    {
        "id": "tools",
        "version": 1,
        "title": "Registered tools",
        "view": "tools",
        "target": "#view-tools",
        "fallback_target": "#tools-content",
        "min_role": "operator",
        "summary": (
            "Browse the registered Nexus tools available to your agents and workflow tool nodes, "
            "inspect their categories, or run an isolated tool test with explicit JSON arguments."
        ),
        "branch_title": "Role and policy scoping",
        "branch_body": (
            "In shared team workspaces, the active team policy controls which registered tools are "
            "exposed to operators and builders."
        ),
        "practice": {
            "label": "Preview sample tool call",
            "preview": (
                "Sample tool (local preview only): 'text:wordcount' with args ['Release notes draft'] → "
                "Returns '3' through the local tool registry without calling an AI model."
            ),
        },
    },
    {
        "id": "workbench",
        "version": 1,
        "title": "Workspace tools",
        "view": "workbench",
        "target": "#view-workbench",
        "fallback_target": ".workbench-tabs",
        "min_role": "operator",
        "summary": (
            "Use Workspace tools to manage conversation memory, resume SQLite workflow checkpoints, "
            "coordinate multi-agent delegation, draft publishing campaigns, inspect audit analytics, "
            "transcribe audio, run typed decisions, configure file storage, set prompt budgets, and review deployment checks."
        ),
        "branch_title": "Checkpoints, budgets, and readiness",
        "branch_body": (
            "Each tab focuses on an operational capability: capture or resume paused workflows in Checkpoints, "
            "cap history size in Controls, or inspect environment readiness in Deployment checks."
        ),
        "practice": {
            "label": "Preview workspace tool tabs",
            "preview": (
                "Sample tabs (local preview only): Memory · Checkpoints · Agents · Campaigns · Analytics · "
                "Transcription · Decisions · File storage · Controls · Deployment checks."
            ),
        },
    },
    {
        "id": "workspace-mesh",
        "version": 1,
        "title": "Workspace, mesh, and sharing",
        "view": "workspace",
        "target": "#view-workspace",
        "fallback_target": "#workspace-runtime",
        "min_role": "viewer",
        "summary": (
            "Inspect local runtime queue health, live execution events, and portable definition exports, "
            "plus Response feedback and read-only Conversation sharing from the workspace navigation."
        ),
        "branch_title": "Portable exports & feedback loops",
        "branch_body": (
            "Export definitions as a single JSON bundle, turn response ratings into evaluation cases in "
            "Response feedback, or create revocable read-only snapshots in Conversation sharing."
        ),
        "practice": {
            "label": "Preview runtime health summary",
            "preview": (
                "Sample runtime summary (local preview only): Queue idle · 0 active runs · "
                "Definitions exportable as nexus-workspace.json."
            ),
        },
    },
    {
        "id": "activity-inbox",
        "version": 1,
        "title": "Activity and approval inbox",
        "view": "inbox",
        "target": "#view-inbox",
        "fallback_target": "#inbox-list",
        "min_role": "operator",
        "summary": (
            "Review paused workflow gates, publishing drafts, external action recovery, and run traces "
            "in the Approval inbox and Activity screens before any external change takes effect."
        ),
        "branch_title": "Auditing runs and approvals",
        "branch_body": (
            "Approval items bind your decision to the exact reviewed digest, while Activity records "
            "every conversation, workflow, build, and tool run with step-by-step execution traces."
        ),
        "practice": {
            "label": "Preview sample approval item",
            "preview": (
                "Sample inbox item (local preview only): Waiting gate 'Publish release summary'. "
                "Inspect proposed output, then Approve or Reject without leaving Studio."
            ),
        },
    },
    {
        "id": "studio-assistant",
        "version": 2,
        "title": "Studio assistant",
        "view": "assistant",
        "target": "#view-assistant",
        "fallback_target": "#studio-help-form",
        "min_role": "viewer",
        "summary": (
            "Ask how Studio works or find your agents, workflows, and server files. "
            "Uses built-in documentation and authorized workspace metadata with zero provider tokens."
        ),
        "branch_title": "Local help vs Ask AI",
        "branch_body": (
            "Find local answer uses built-in documentation and current resource names without API keys. "
            "Ask AI optionally routes the question to your configured provider."
        ),
        "practice": {
            "label": "Preview Studio help search",
            "preview": (
                "Sample query (local preview only): 'Where are my agents saved?' → "
                "Points directly to server folder path and links to the Agents editor."
            ),
        },
    },
    {
        "id": "help-reopen",
        "version": 1,
        "title": "Help and reopening the guide",
        "view": "chat",
        "target": "#open-interactive-guide",
        "fallback_target": "#help-guide-button",
        "min_role": "viewer",
        "summary": (
            "Use Help → Interactive guide in the navigation menu or top bar at any time to resume where "
            "you left off or restart the walkthrough from the beginning."
        ),
        "branch_title": "Versioned updates",
        "branch_body": (
            "When new Studio capabilities are added in future releases, Help → Interactive guide highlights "
            "only the new steps without resetting your saved progress."
        ),
        "practice": {
            "label": "Preview guide controls",
            "preview": (
                "Tip: Press Escape at any time to close the guide and return focus to the control you were using."
            ),
        },
    },
)

_STEP_IDS = {step["id"] for step in GUIDE_STEPS}
_VALID_VIEWS = {
    "chat",
    "quick-build",
    "agents",
    "library",
    "workflows",
    "projects",
    "files",
    "assistant",
    "tools",
    "workspace",
    "feedback",
    "sharing",
    "evaluations",
    "operations",
    "inbox",
    "workbench",
    "providers",
    "activity",
}


class StudioOnboarding:
    def __init__(self, studio):
        self.studio = studio

    @staticmethod
    def fingerprint(record):
        return hashlib.sha256(
            json.dumps(
                {
                    "id": record["id"],
                    "revision": record["revision"],
                    "data": record["data"],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def owner(self, actor):
        if self.studio.team and (not actor or actor["role"] != "owner"):
            raise PermissionError("Connection setup requires the owner role")
        return self.studio.workbench.owner(actor)

    def _role(self, actor):
        if self.studio.team:
            if not actor:
                raise PermissionError("Nexus sign-in required")
            return str(actor.get("role") or "viewer")
        return "owner"

    def guide_steps(self, actor=None):
        role = self._role(actor)
        role_rank = ROLES.get(role, 0)
        raw_providers = (
            self.studio.team.providers(self.studio.providers(), actor["nexus_id"])
            if self.studio.team and actor
            else self.studio.providers()
        )
        cli_ready = [p["name"] for p in raw_providers if p.get("kind") == "cli" and p.get("configured")]
        api_ready = [p["name"] for p in raw_providers if p.get("kind") == "api" and p.get("configured")]
        cli_all = [p["name"] for p in raw_providers if p.get("kind") == "cli"]
        api_all = [p["name"] for p in raw_providers if p.get("kind") == "api"]
        steps = []
        for index, item in enumerate(GUIDE_STEPS, start=1):
            min_role = item["min_role"]
            restricted = role_rank < ROLES.get(min_role, 0)
            if restricted:
                role_note = (
                    f"Your current role ({role}) can inspect this area in read-only mode; "
                    f"creating or changing items here requires the {min_role} role."
                )
            elif self.studio.team:
                role_note = f"Available for your {role} role in this shared workspace."
            else:
                role_note = "Available in your local desktop workspace."
            capability_note = ""
            if item["id"] == "providers":
                ready_parts = []
                if cli_ready:
                    ready_parts.append("ready CLI subscriptions: " + ", ".join(cli_ready))
                if api_ready:
                    ready_parts.append("ready API providers: " + ", ".join(api_ready))
                if ready_parts:
                    capability_note = "Currently detected (" + "; ".join(ready_parts) + ")."
                else:
                    capability_note = (
                        f"Supports CLI subscriptions ({', '.join(cli_all)}) and API keys "
                        f"({', '.join(api_all)}). None are marked ready yet. Review setup here when needed."
                    )
            elif item["id"] == "workspace-navigation":
                agents_count = len(self.studio.definitions("agents"))
                workflows_count = len(self.studio.definitions("workflows"))
                if agents_count == 0 and workflows_count == 0:
                    capability_note = (
                        "This workspace has no saved agents or workflows yet. You can start in Conversations "
                        "or draft your first setup with Quick Build."
                    )
                else:
                    capability_note = (
                        f"This workspace currently includes {agents_count} agent(s) and "
                        f"{workflows_count} workflow(s)."
                    )
            steps.append(
                {
                    **item,
                    "index": index,
                    "total": len(GUIDE_STEPS),
                    "restricted": restricted,
                    "role_note": role_note,
                    "capability_note": capability_note,
                }
            )
        return steps

    def guide_state(self, actor=None):
        role = self._role(actor)
        key = self.studio.workbench.settings_key(actor)
        path = self.studio._path("onboarding-guide", key)
        saved = self.studio._read(path) if path.exists() else {}
        status = saved.get("status")
        if status not in {"new", "in_progress", "completed", "dismissed"}:
            status = "new"
        current_step = saved.get("current_step")
        if current_step not in _STEP_IDS:
            current_step = GUIDE_STEPS[0]["id"]
        completed_steps = [
            step_id
            for step_id in (saved.get("completed_steps") or [])
            if isinstance(step_id, str) and step_id in _STEP_IDS
        ]
        seen_version = saved.get("seen_version")
        if type(seen_version) is not int or seen_version < 0:
            seen_version = 0
        origin_view = saved.get("origin_view")
        if origin_view not in _VALID_VIEWS:
            origin_view = None
        steps = self.guide_steps(actor)
        new_steps = [
            step["id"]
            for step in steps
            if step["version"] > seen_version and status in {"completed", "dismissed"}
        ]
        return {
            "id": key,
            "owner_id": self.studio.workbench.owner(actor),
            "workspace": {
                "name": self.studio.root.name,
                "mode": "team" if self.studio.team else "desktop",
            },
            "role": role,
            "version": GUIDE_VERSION,
            "seen_version": seen_version,
            "status": status,
            "current_step": current_step,
            "completed_steps": completed_steps,
            "origin_view": origin_view,
            "upgrade_available": bool(new_steps),
            "new_steps": new_steps,
            "updated_at": saved.get("updated_at"),
            "completed_at": saved.get("completed_at"),
            "dismissed_at": saved.get("dismissed_at"),
            "steps": steps,
        }

    def save_guide_state(self, payload, actor=None):
        if not isinstance(payload, dict):
            raise ValueError("Guide state update must be an object")
        forbidden = {"prompt", "messages", "api_key", "token", "secret", "credentials", "output"}
        if forbidden.intersection(payload):
            raise ValueError("Guide state stores only progress metadata, never prompts or credentials")
        self._role(actor)
        with self.studio.lock:
            current = self.guide_state(actor)
            action = str(payload.get("action") or "step")
            if action not in {
                "start",
                "step",
                "complete",
                "dismiss",
                "reset",
                "acknowledge_upgrade",
            }:
                raise ValueError("Unsupported guide state action")
            status = current["status"]
            step_id = current["current_step"]
            completed = list(current["completed_steps"])
            seen_version = current["seen_version"]
            origin_view = current["origin_view"]
            timestamp = datetime.now(UTC).isoformat()
            completed_at = current.get("completed_at")
            dismissed_at = current.get("dismissed_at")

            requested_origin = payload.get("origin_view")
            if requested_origin is not None:
                if requested_origin != "" and requested_origin not in _VALID_VIEWS:
                    raise ValueError("Invalid origin view")
                origin_view = requested_origin or None

            requested_step = payload.get("current_step")
            if requested_step is not None:
                if requested_step not in _STEP_IDS:
                    raise ValueError("Unknown interactive guide step")
                step_id = requested_step

            if isinstance(payload.get("completed_steps"), list):
                for item in payload["completed_steps"]:
                    if not isinstance(item, str) or item not in _STEP_IDS:
                        raise ValueError("Unknown completed guide step")
                    if item not in completed:
                        completed.append(item)

            if action == "start":
                status = "in_progress"
                seen_version = GUIDE_VERSION
                if requested_step is None and current["status"] in {"new", "completed"}:
                    step_id = GUIDE_STEPS[0]["id"]
            elif action == "step":
                status = "in_progress"
                seen_version = max(seen_version, GUIDE_VERSION)
                completed_step = payload.get("completed_step")
                if completed_step is not None:
                    if completed_step not in _STEP_IDS:
                        raise ValueError("Unknown completed guide step")
                    if completed_step not in completed:
                        completed.append(completed_step)
            elif action == "complete":
                status = "completed"
                seen_version = GUIDE_VERSION
                completed_at = timestamp
                for item in GUIDE_STEPS:
                    if item["id"] not in completed:
                        completed.append(item["id"])
            elif action == "dismiss":
                if status != "completed":
                    status = "dismissed"
                seen_version = max(seen_version, GUIDE_VERSION)
                dismissed_at = timestamp
            elif action == "reset":
                status = "new"
                step_id = GUIDE_STEPS[0]["id"]
                completed = []
                seen_version = 0
                origin_view = None
                completed_at = None
                dismissed_at = None
            elif action == "acknowledge_upgrade":
                seen_version = GUIDE_VERSION

            record = {
                "id": current["id"],
                "owner_id": current["owner_id"],
                "version": GUIDE_VERSION,
                "seen_version": seen_version,
                "status": status,
                "current_step": step_id,
                "completed_steps": completed,
                "origin_view": origin_view,
                "updated_at": timestamp,
                "completed_at": completed_at,
                "dismissed_at": dismissed_at,
            }
            self.studio._write(self.studio._path("onboarding-guide", current["id"]), record)
            return self.guide_state(actor)

    def probe(self, identity, payload, actor):
        self.owner(actor)
        record = self.studio.catalog.get("connectors", identity)
        if payload.get("revision") != record["revision"]:
            raise ValueError("Connection settings changed. Review them again")
        kind = record["data"]["type"]
        action = (
            "discover"
            if kind == "mcp"
            else "list_issues"
            if kind in {"github", "gitlab"}
            else "status"
        )
        # Never accept a browser-selected action or arguments during onboarding.
        self.studio.operations.call_connector(
            identity, {"action": action}, self.owner(actor), probe=True
        )
        with self.studio.lock:
            latest = self.studio.catalog.get("connectors", identity)
            if self.fingerprint(record) != self.fingerprint(latest):
                raise ValueError("Connection changed during the check. Check it again")
            result = {
                "id": identity,
                "owner_id": self.owner(actor),
                "digest": self.fingerprint(record),
                "revision": record["revision"],
                "checked_at": datetime.now(UTC).isoformat(),
                "authorization_verified": kind not in {"telegram", "slack", "discord", "openclaw"},
                "message": "Credentials are configured; messaging authorization is checked on use. No message was sent."
                if kind in {"telegram", "slack", "discord", "openclaw"}
                else "Read-only connection check succeeded. No write action was attempted.",
            }
            self.studio._write(self.studio._path("connection-checks", identity), result)
            return result

    def enable(self, identity, payload, actor):
        owner = self.owner(actor)
        with self.studio.lock:
            record = self.studio.catalog.get("connectors", identity)
            check = self.studio._read(self.studio._path("connection-checks", identity))
            if (
                check.get("owner_id") != owner
                or payload.get("digest") != check["digest"]
                or check["digest"] != self.fingerprint(record)
            ):
                raise ValueError(
                    "Run the connection check for these settings and your account again"
                )
            if payload.get("confirm") is not True:
                raise ValueError("Confirm enabling this connection")
            return self.studio.catalog.save(
                "connectors",
                {
                    "id": identity,
                    "name": record["name"],
                    "description": record["description"],
                    "revision": record["revision"],
                    "requester_nexus_id": owner,
                    "data": {**record["data"], "enabled": True},
                },
            )
