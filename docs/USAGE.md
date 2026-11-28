# Nexus Framework Usage Guide

This guide explains the boundary between the **`nexus-arc` framework** (`nexus/`) and an **application / integration layer** (`examples/nexus-bot/`), and shows how to build workflows, issue-driven automation, and Nexus Studio surfaces with the current API.

---

## Architecture Overview

Nexus separates reusable orchestration primitives from application-specific bots, webhooks, and workspace wiring:

| Layer | Location | Responsibilities |
|---|---|---|
| **Framework (`nexus-arc`)** | `nexus/` | State-machine workflows (`WorkflowEngine`, `YamlWorkflowLoader`), graph workflows (`nexus.workflows`), AI provider routing (`AIOrchestrator`), Nexus Studio control plane (`StudioService`, `serve_studio`), adapter registry (`AdapterRegistry`), pub/sub event bus (`EventBus`), completion protocol (`CompletionSummary`, `scan_for_completions`), plugin runtime (`PluginRegistry`, `WorkflowStateEnginePlugin`, `HandoffManager`), hot-reload (`HotReloadWatcher`), and custom tool SDK (`nexus.tools.sdk`). |
| **Reference Application (`nexus-bot`)** | `examples/nexus-bot/` | Concrete Telegram and Discord bots, GitHub/GitLab webhook receivers, inbox polling, `project_config.yaml` loader, agent persona prompts, and Studio workspace helpers (`studio.py`, `studio_workspace.py`, `studio_cli.py`). |

### Core Framework Imports

Top-level symbols re-exported by `nexus`, plus key submodule entry points:

```python
from nexus import (
    # Models & enums
    Agent,
    AgentResult,
    Task,
    Workflow,
    WorkflowState,
    WorkflowStep,
    # Core engines & protocols
    AIOrchestrator,
    AgentMesh,
    CompletionSummary,
    DetectedCompletion,
    LaunchGuard,
    WorkflowDefinition,
    WorkflowEngine,
    build_completion_comment,
    generate_completion_instructions,
    scan_for_completions,
    # Built-in adapters
    FileStorage,
    GitHubPlatform,
    # Plugin registry (lazy-loaded)
    PluginKind,
    PluginRegistry,
    PluginSpec,
)
from nexus.adapters.registry import AdapterRegistry
from nexus.adapters.storage import FileStorage, PostgreSQLStorageBackend, StorageBackend
from nexus.core.events import EventBus
from nexus.core.studio import StudioService
from nexus.core.yaml_loader import YamlWorkflowLoader
from nexus.studio import make_studio_handler, serve_studio
```

---

## 1. YAML & State-Machine Workflows (`nexus.core.workflow`)

Use `YamlWorkflowLoader` and `WorkflowEngine` when your workflow is defined as an ordered or branched set of `WorkflowStep` steps with retry policies, parallel execution, conditional routing, and approval gates persisted in a `StorageBackend`.

### Defining a YAML Workflow

Create a YAML file (for example, `workflows/bugfix.yaml` or a multi-tier definition under `examples/nexus-bot/workflows/`):

```yaml
metadata:
  name: Bug Fix Pipeline
  version: "1.0"
  description: Triage, fix, and verify an incoming bug report

steps:
  - id: triage
    name: Triage Bug Report
    agent_type: triage

  - id: route_severity
    name: Route by Severity
    agent_type: router
    routes:
      - when: "severity == 'critical'"
        then: hotfix
      - default: standard_fix

  - id: hotfix
    name: Apply Critical Hotfix
    agent_type: developer
    on_success: verify
    retry_policy:
      max_retries: 2
      backoff: exponential
      initial_delay: 1.5

  - id: standard_fix
    name: Implement Standard Fix
    agent_type: developer
    on_success: verify

  - id: verify
    name: Run Verification Suite
    agent_type: qa
    final_step: true
```

`YamlWorkflowLoader` also supports **multi-tier** YAML files where top-level keys (such as `full`, `shortened`, `fast_track`) each define a separate workflow variant and share `metadata` and `step_templates`.

### Loading, Validating, and Running with `WorkflowEngine`

```python
import asyncio
import tempfile
from nexus import FileStorage, WorkflowEngine, WorkflowState
from nexus.core.events import EventBus
from nexus.core.yaml_loader import YamlWorkflowLoader


async def run_yaml_workflow() -> None:
    # 1. Validate and load a workflow definition dict (or use YamlWorkflowLoader.load("path.yaml"))
    definition = {
        "metadata": {
            "name": "Bug Fix Pipeline",
            "version": "1.0",
        },
        "steps": [
            {
                "id": "triage",
                "name": "Triage Bug Report",
                "agent_type": "triage",
            },
            {
                "id": "route_severity",
                "name": "Route by Severity",
                "agent_type": "router",
                "routes": [
                    {"when": "severity == 'critical'", "then": "hotfix"},
                    {"default": "standard_fix"},
                ],
            },
            {
                "id": "hotfix",
                "name": "Apply Critical Hotfix",
                "agent_type": "developer",
                "on_success": "verify",
            },
            {
                "id": "standard_fix",
                "name": "Implement Standard Fix",
                "agent_type": "developer",
                "on_success": "verify",
            },
            {
                "id": "verify",
                "name": "Run Verification Suite",
                "agent_type": "qa",
                "final_step": True,
            },
        ],
    }

    errors = YamlWorkflowLoader.validate_dict(definition)
    assert not errors, f"Invalid workflow: {errors}"

    workflow = YamlWorkflowLoader.load_from_dict(definition, workflow_id="issue-42")

    # 2. Create the engine with a StorageBackend and optional EventBus
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = FileStorage(base_path=tmp_dir)
        bus = EventBus()
        engine = WorkflowEngine(storage=storage, event_bus=bus)

        # 3. Register and start the workflow
        await engine.create_workflow(workflow)
        await engine.start_workflow("issue-42")

        # 4. Complete triage; router step evaluates severity == 'critical' -> activates 'hotfix' (step 3)
        wf = await engine.complete_step(
            "issue-42",
            step_num=1,
            outputs={"severity": "critical", "summary": "Null pointer in auth handler"},
        )
        assert wf.current_step == 3
        print("Active step:", wf.current_step, wf.active_agent_type)

        # Complete hotfix (on_success jumps to 'verify' at step 5, skipping 'standard_fix' at step 4)
        wf = await engine.complete_step("issue-42", step_num=3, outputs={"pr_url": "https://example/pr/99"})
        assert wf.current_step == 5
        wf = await engine.complete_step("issue-42", step_num=5, outputs={"tests_passed": True})
        assert wf.state == WorkflowState.COMPLETED
        print("Final workflow state:", wf.state.value)


if __name__ == "__main__":
    asyncio.run(run_yaml_workflow())
```

### Normalize Workflow Definitions Without Storage

When you need to validate or normalize a workflow spec for inspection or Studio preview without creating a `Workflow` instance, use `WorkflowDefinition`:

```python
from nexus import WorkflowDefinition

normalized = WorkflowDefinition.normalize_workflow_definition(
    {
        "name": "Release Check",
        "version": "1.0",
        "steps": [
            {"id": "build", "name": "Build Artifact", "agent_type": "builder"},
            {"id": "smoke", "name": "Smoke Test", "agent_type": "tester"},
        ],
    }
)
```

---

## 2. Executable Graph Workflows (`nexus.workflows`)

For code-first stateful graphs with typed edges, route dictionaries, human-in-the-loop interrupts (`RequestInput`), event streaming, and SQLite/JSON checkpoints (`SQLiteCheckpointStore`, `JsonCheckpointStore`), use `nexus.workflows`:

```python
import asyncio
from nexus.workflows import Event, RequestInput, START, Workflow, node


@node
async def classify(issue_body: str) -> dict:
    needs_approval = "production" in issue_body.lower()
    return {"issue_body": issue_body, "needs_approval": needs_approval}


@node(rerun_on_resume=True)
async def approval_gate(payload: dict):
    if payload.get("needs_approval"):
        yield Event(message="Waiting for human approval", state={"stage": "approval"})
        yield RequestInput(
            message="Approve production deployment?",
            key="prod_approval",
        )
        return
    yield {"approved": True, "issue_body": payload["issue_body"]}


@node
async def deploy(decision: dict) -> dict:
    return {"deployed": bool(decision.get("approved")), "target": decision.get("issue_body", "")}


async def run_graph_workflow() -> None:
    checkpoints: dict = {}
    wf = Workflow(
        name="deploy-flow",
        edges=[(START, classify, approval_gate, deploy)],
    )

    # First run pauses at RequestInput when 'production' appears in input
    paused = await wf.run(
        "Update production database credentials",
        checkpoints=checkpoints,
    )
    print("Paused prompt:", paused.paused.message if paused.paused else None)

    # Resume from checkpoints with operator input
    completed = await wf.run(
        "Update production database credentials",
        checkpoints=checkpoints,
        resume_inputs={
            "prod_approval": {
                "approved": True,
                "issue_body": "Update production database credentials",
            }
        },
    )
    print("Completed output:", completed.output, "Visited:", completed.visited)


if __name__ == "__main__":
    asyncio.run(run_graph_workflow())
```

---

## 3. Issue-Centric Application Layer (`examples/nexus-bot`)

`examples/nexus-bot` shows how an application wires `nexus-arc` to GitHub/GitLab issues, local workspace directories, Telegram/Discord chat commands, and multi-provider AI CLIs.

### Key Application Files in `examples/nexus-bot`

| File | Purpose |
|---|---|
| `config/project_config.yaml` | Multi-project configuration: workspace path, Git repo, workflow file, operation-to-agent mappings (`operations`), agent launch flags, and provider fallback chains (`ai_providers`). |
| `src/config_loader.py` | Loads and validates `project_config.yaml`, resolving `$ENV_VAR` secrets and per-project settings. |
| `src/inbox_processor.py` | Polls `.nexus/inbox/` across projects, creates a GitHub/GitLab issue, maps the issue type to a workflow tier (`full`, `shortened`, `fast_track`), starts the workflow via `WorkflowStateEnginePlugin`, and launches the initial agent via `AIOrchestrator`. |
| `src/webhook_server.py` | HTTP webhook receiver for GitHub/GitLab issue and comment events; detects agent completion comments and triggers automatic step transitions. |
| `src/telegram_bot.py` / `src/discord_bot.py` | Interactive chat adapters for `/new`, `/status`, `/track`, `/myissues`, `/pause`, `/resume`, `/stop`, `/Continue`, and free-form conversational routing. |
| `src/studio.py` / `src/studio_workspace.py` | Launches Nexus Studio pre-wired with the bot's workspace root, agent YAML definitions, and workflow templates. |

### `project_config.yaml` Structure

```yaml
nexus_dir: .nexus
workflows_dir: workflows

ai_providers:
  default_Order:
    - gemini
    - copilot
    - codex
    - claude
  rate_limit_cooldown: 600

my_project:
  workspace: /path/to/your/workspace
  agents_dir: agents
  git_platform: github
  git_repo: your-org/your-repo
  workflow_definition_path: workflows/default_workflow.yaml

  operations:
    triage: triage
    debug: debug
    bug: debug
    feature: designer
    implement: developer
    develop: developer

  agents:
    triage:
      timeout: 600
      max_retries: 2
      launch_args: ["--model", "claude-sonnet-4-5"]
```

### Bridging Issues to Workflows (`WorkflowStateEnginePlugin`)

`WorkflowStateEnginePlugin` (in `nexus.plugins.builtin.workflow_state_engine_plugin`) wraps `WorkflowEngine`, `WorkflowDefinition`, and `FileStorage` behind an issue-numbered interface used by `examples/nexus-bot`:

```python
import asyncio
import tempfile
from nexus.plugins.builtin.workflow_state_engine_plugin import WorkflowStateEnginePlugin


async def manage_issue_workflow() -> None:
    issue_map: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as storage_dir:
        engine = WorkflowStateEnginePlugin(
            config={
                "storage_dir": storage_dir,
                "workflow_definition_path": "examples/workflows/enterprise_workflow.yaml",
                "issue_workflow_map": issue_map,
                "issue_to_workflow_map_setter": issue_map.__setitem__,
            }
        )

        workflow_id = await engine.create_workflow_for_issue(
            issue_number="128",
            issue_title="Fix OAuth callback timeout",
            project_name="my_project",
            tier_name="shortened",
            task_type="bug",
        )
        assert workflow_id is not None

        await engine.start_workflow(workflow_id)
        status = await engine.get_workflow_status("128")
        print("Active step for issue #128:", status["current_step_name"], "->", status["current_agent_type"])


if __name__ == "__main__":
    asyncio.run(manage_issue_workflow())
```

### Completion Detection & Sub-Agent Delegation (`HandoffManager`)

Agents running on an issue write structured completion files (`completion_summary_<issue>.json`) under `.nexus/tasks/<project>/completions/`. The framework scans these files with `scan_for_completions`, formats comments with `build_completion_comment`, and tracks active lead-to-sub-agent delegations with `HandoffManager`:

```python
from nexus.core.completion import build_completion_comment, scan_for_completions
from nexus.core.models import DelegationCallback, DelegationRequest
from nexus.plugins.plugin_runtime import HandoffManager

# 1. Scan workspace for new agent completion summaries
for item in scan_for_completions("/path/to/workspace", ".nexus"):
    print(f"Issue #{item.issue_number} completed by {item.summary.agent_type} -> next: {item.summary.next_agent}")
    comment_md = build_completion_comment(item.summary)
    print(comment_md)

# 2. Track active agent delegations within a workflow
manager = HandoffManager()
req = DelegationRequest(
    lead_agent="developer",
    sub_agent="reviewer",
    issue_number="128",
    workflow_id="my_project-128-shortened",
    task_description="Review OAuth callback timeout fix",
)
manager.register(req)
pending = manager.pending_for(lead_agent="developer", workflow_id="my_project-128-shortened")
print("Pending delegations:", [d.delegation_id for d in pending])
manager.complete(
    DelegationCallback(
        delegation_id=req.delegation_id,
        sub_agent="reviewer",
        lead_agent="developer",
        issue_number="128",
        workflow_id="my_project-128-shortened",
        result={"summary": "LGTM"},
        success=True,
    )
)
```

---

## 4. Event Bus, AI Orchestrator & Custom Tools

### EventBus Pub/Sub (`nexus.core.events`)

`EventBus` decouples workflow state transitions from side effects such as Telegram/Discord notifications, GitHub comments, and audit logs:

```python
import asyncio
from nexus.core.events import EventBus, NexusEvent, StepCompleted, WorkflowFailed


async def main() -> None:
    bus = EventBus()

    async def on_step_completed(event: NexusEvent) -> None:
        assert isinstance(event, StepCompleted)
        print(f"[{event.workflow_id}] Step {event.step_num} ({event.agent_type}) finished")

    async def on_any_workflow_event(event: NexusEvent) -> None:
        print(f"Audit: {event.event_type} at {event.timestamp.isoformat()}")

    bus.subscribe("step.completed", on_step_completed)
    bus.subscribe_pattern("workflow.*", on_any_workflow_event)

    await bus.emit(
        StepCompleted(
            workflow_id="issue-42",
            step_num=1,
            step_name="triage",
            agent_type="triage",
            outputs={"next_agent": "developer"},
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
```

### Custom Tools (`nexus.tools` & `nexus.tools.sdk`)

Register named workflow tools in the shared `tool_registry` with `@tool`, or attach typed configuration schemas for Studio toolsets with `@with_config_schema`:

```python
from nexus.tools import tool, tool_registry
from nexus.tools.sdk import ConfigSchemaField, SandboxToolContextFacade, ToolResult, with_config_schema


@tool(name="lookup_runbook", description="Fetch the incident runbook for a service")
def lookup_runbook(service_name: str, environment: str = "prod") -> str:
    return f"Runbook for {service_name} ({environment}): check health endpoint and error budget."


@with_config_schema(
    [
        ConfigSchemaField(
            key="runbook_base_url",
            description="Base URL for internal runbooks",
            default="https://runbooks.internal",
            required=True,
        )
    ]
)
def configured_runbook_tool(payload: dict, context: SandboxToolContextFacade) -> ToolResult:
    base_url = context.get_config("runbook_base_url", "https://runbooks.internal")
    service = payload.get("service", "api")
    return ToolResult(output=f"{base_url}/{service}")


print("Registered tools:", tool_registry.names())
```

### Hot-Reloading Plugins (`HotReloadWatcher`)

During development or long-running bot sessions, `HotReloadWatcher` monitors a plugin directory for `.py` file changes and reloads `register_plugins(registry)` contributors into a `PluginRegistry` without restarting core services (requires `pip install 'nexus-arc[hotreload]'`):

```python
from nexus import PluginRegistry
from nexus.plugins.plugin_runtime import HotReloadWatcher

registry = PluginRegistry()
# When watchdog is installed, watch a directory of plugin .py modules:
# watcher = HotReloadWatcher(registry, watch_dir="plugins", poll_interval=1.0)
# watcher.start()
# ...
# watcher.stop()
```

---

## 5. Nexus Studio Control Plane & CLI

Nexus Studio provides an authenticated local control plane and UI for workflows, prompt libraries, experiments, playgrounds, Knowledge bases, artifacts (including isolated PDF preview), voice/speech services, and local runtime status.

### Starting Nexus Studio from the CLI

```bash
# Launch the Nexus Studio control plane on http://127.0.0.1:8788
nexus studio --host 127.0.0.1 --port 8788 --workspace .

# Or launch with the nexus-bot example workspace and agents pre-configured
python examples/nexus-bot/src/studio.py --port 8788

# Interactive terminal chat against configured providers
nexus chat --provider gemini

# Manage studio/provider configuration, toolsets, and AI assistance
nexus config --help
nexus toolset --help
nexus ai-assistance --help
```

### Embedding `StudioService` in Python

```python
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler, serve_studio

# Programmatic access to the Studio control plane for a workspace root
studio = StudioService(root=".", state_dir=".nexus/studio")
print("Workspace root:", studio.root)
print("Available providers:", [p["id"] for p in studio.providers()])
print("Workflow definitions:", [w["id"] for w in studio.definitions("workflows")])
studio.close()
```

---

## Further Reading

- [Architecture Guide](ARCHITECTURE.md) — Core modules, storage backends, and dependency flow.
- [Workflow Specification](WORKFLOW_SPEC.md) — Full schema for YAML workflow definitions, routing expressions, and approval gates.
- [Getting Started with Nexus Bot](GETTING_STARTED_NEXUS_BOT.md) — Step-by-step setup for `examples/nexus-bot`.
- [Tutorials](TUTORIALS.md) — End-to-end walkthroughs for custom workflows and plugins.
- [Studio PDF Preview](STUDIO-PDF-PREVIEW.md) — Isolated PDF extraction architecture, limits, and failure categories.
