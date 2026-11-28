# Chat Bot Example (Migrated from `nexus`)

This folder is now the canonical home for the Telegram/Discord bot runtimes that previously lived in `ghabs/nexus/src`.

The example also consumes the framework's local agent workspace:

```sh
NEXUS_STUDIO_DIR=./my-workspace nexus-studio
```

This command delegates to `nexus.studio.serve_studio`; all reusable workspace
behavior stays in the framework. See [Nexus Studio](../../docs/STUDIO.md).

Bootstrap/runtime lifecycle reference:

- [`docs/CONFIG_BOOTSTRAP_LIFECYCLE.md`](../../docs/CONFIG_BOOTSTRAP_LIFECYCLE.md)

## What is included

- `src/`: Bot runtime, handlers, processor, webhook server, health check
- `config/project_config.yaml`: NXS-only fallback config (not the primary runtime source)
- `nexus-*.service`: systemd service templates
- `scripts/setup-webhook.sh`: Webhook service setup helper

## Quick start

## Interactive Commands

The bot supports the following key commands across Telegram/Discord:

**Git Platform Management:**

- `/assign <project> <issue#>` - Assign an issue to yourself
- `/implement <project> <issue#>` - Request AI Agent implementation
- `/prepare <project> <issue#>` - Add AI Agent instructions
- `/plan <project> <request>` - Create a new planning task

**Workflow & Monitoring:**

- `/status [project|all]` - View pending tasks in the inbox
- `/track <project> <issue#>` - Track an issue for updates
- `/myissues` - View all your tracked issues
- `/active` or `/wfstate` - View active workflows

**General:**

- `/chat` - Speak directly with the AI
- `/help` - Show all available commands
- `/menu` - Interactive button menu

```bash
cd /opt/nexus-arc
python3 -m venv venv
source venv/bin/activate
pip install -e ".[nexus-bot]"
cd examples/nexus-bot
pip install -e .
cp .env.example .env
```

Set at minimum in `.env`:

- `TELEGRAM_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `PROJECT_CONFIG_PATH=/opt/nexus/config/project_config.yaml`
- `BASE_DIR=/home/ubuntu/git`
- `DEPLOY_TYPE=compose` (or `systemd`)

For production auth/BYOK deployments, validate your env before starting services:

```bash
# Generate a valid master key (32-byte base64url)
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"

# Validate required OAuth/auth/runtime values are not placeholders
./scripts/check-prod-env.sh .env
```

Run the bot runtimes:

```bash
source .env
nexus-telegram-bot
nexus-discord-bot
```

## systemd templates

These unit files are used only when `DEPLOY_TYPE=systemd`.
If `DEPLOY_TYPE=compose` (default), they are not used.

Service files in this folder are pre-pointed to:

- runtime: `/opt/nexus-arc/examples/nexus-bot`
- env file: `/opt/nexus/.env`

Copy and enable as needed:

```bash
sudo cp nexus-telegram.service /etc/systemd/system/
sudo cp nexus-discord.service /etc/systemd/system/
sudo cp nexus-processor.service /etc/systemd/system/
sudo cp nexus-webhook.service /etc/systemd/system/
sudo cp nexus-bridge.service /etc/systemd/system/
sudo cp nexus-health.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## Docker Compose (recommended runtime)

This example includes a dedicated application compose stack:

- `docker-compose.yml`
- `Dockerfile`

It runs six services:

- `telegram`
- `discord`
- `processor`
- `webhook`
- `bridge`
- `health`

All services read environment variables from:

- `./.env`

Set `NEXUS_COMMAND_BRIDGE_AUTH_TOKEN` in `.env` before starting the bridge service.

OpenClaw bridge callers can now send richer requester/session metadata through
this example bridge service, including `requester.operator_id`,
`requester.session_id`, `context.current_project`, and `client.render_mode`.
The OpenClaw plugin can also maintain per-session local context with
`/nexus use <project>`, `/nexus current`, `/nexus usage`, and `/nexus refresh`.
It also keeps the Nexus workspace chat concept available in OpenClaw through
`/chat <message>` and `/chatagents [project]`, so operators can talk directly
to the active workspace-aware agent while still using `/nexus` as the workflow
control plane.
It also supports bridge-safe issue commands like `/nexus new`, `/nexus track`,
`/nexus tracked`, `/nexus untrack`, and `/nexus myissues`, plus workflow
recovery commands like `/nexus reconcile`, `/nexus reprocess`, and
`/nexus kill`. Local confirmation covers risky commands such as
`/nexus implement`, `/nexus stop`, `/nexus kill`, and `/nexus reprocess`.
Bridge responses also expose best-effort `usage` metadata for issue/workflow
commands, sourced from recent completion storage or the newest agent log so the
OpenClaw plugin can show Nexus ARC spend details inline.

When you operate multiple projects through separate OpenClaw profiles, give each
profile its own plugin config with a project-specific `defaultProject` and
bridge token. For example, keep distinct `ghabs`, `acme`, and `projectA`
profiles so bare commands like `/nexus reprocess #42` and `/nexus wfstate #42`
resolve against the correct project context without retyping it every time.

All services read project config from the `nexus` repository folder:

- `/opt/nexus/config/project_config.yaml`

`examples/nexus-bot/config/project_config.yaml` is kept as an NXS-only fallback,
but is not the active compose config source.

Start:

```bash
cd /opt/nexus-arc/examples/nexus-bot
docker compose up -d --build
```

Or run through deploy helper:

```bash
./scripts/deploy.sh up
```

Stop:

```bash
docker compose down
```

Logs:

```bash
docker compose logs -f telegram discord processor webhook bridge health
```

### Config-driven deploy mode

Deployment type is selected by `.env`:

- `DEPLOY_TYPE=compose` → uses Docker Compose
- `DEPLOY_TYPE=systemd` → uses `nexus-*.service` units

Use:

```bash
./scripts/deploy.sh [up|down|restart|status|logs]
```

### Separation from infra cloud-init

Keep this application compose file separate from the infra template in
`vsc-server-infra/cloud-init.yaml.tpl`.

- `cloud-init.yaml.tpl` should bootstrap host infrastructure only
  (Docker engine, databases, logging stack, base packages).
- Nexus app lifecycle should stay in this repository's compose file.

Reference the Nexus compose stack from cloud-init only if you explicitly want
automatic deployment at VM bootstrap time.

## Migration note

The old `ghabs/nexus` repository should now be treated as a thin profile/wrapper layer (config + dependency
declaration), while implementation/runtime logic lives here.

### Build a team with AI

In Nexus Studio, open **Quick Build**, describe a use case, and choose Codex,
Claude Code, OpenAI API or Anthropic API. Review the proposed agents and sequential
workflow, edit instructions/providers if needed, then **Save build** and **Test**.
Saved teams appear in the normal Agents and Workflows views.

The example also consumes the same framework API from the command line:

```sh
nexus-quick-build --dir ./my-workspace --provider codex "A team that reviews writing for clarity"
```

This prints a draft; it does not save agent/workflow YAML or execute the team.
Open Studio against the same directory to finish reviewing and testing it.

### Explore Studio resources and graph workflows

```sh
nexus-studio-workspace --dir ./studio-sample
NEXUS_STUDIO_DIR=./studio-sample nexus-studio
```

The workspace consumer creates sample prompts/skills, an agent, an approval-gated
graph, a toolset, an evaluation suite, and disabled connector/entrypoint/schedule
configurations. It does not call a model, activate a schedule or access an external
service. Replace the sample model ID before using that profile.

In Studio, Library manages reusable content; Evaluations provides reports and
Experiment Lab; the workflow canvas edits parallel branches, tool nodes, routing
conditions and approval gates. Workspace & event mesh shows real execution events
and exports saved definitions. See the framework Studio guide for limits.

### Advanced Studio consumer

```sh
nexus-studio-advanced --dir ./advanced-studio-sample --run-local
```

Creates disabled native Git/MCP/Slack/A2A profiles, a mapped incoming channel,
a guarded agent, structured evaluation, binary-store-backed text artifact,
imported sample history and parallel YAML with an approval step. It inspects
saved definition versions, runs only a local word-count tool and reads its real
checkpoint/trace. No remote account is contacted by this example.

Pass `--runtime-config /private/path/studio-runtime.json` to consume the same
administrator-owned SQL state, legacy history, model router and Loki configuration
as the framework server. See [Studio deployment](../../deploy/studio/README.md).
Quick Build now accepts DAG and resource-only proposals; the interface's
**Workspace tools** area exposes memory, coordination, checkpoints, storage,
analytics and execution controls.

### Use Studio on a phone or tablet

The `nexus-studio` consumer serves the framework's responsive interface directly.
At phone widths, **Menu** opens all workspace sections and recent conversations.
Forms and dialogs stack, actions use touch-sized controls, and graph scrolling
stays inside the canvas. Small screens can scroll chat vertically; a phone's
Enter key adds a line, while **Send message** submits it.

To try the layout locally, open Studio and resize the browser to 390 × 844 or
320 × 568. To access it from an actual phone, use the authenticated shared HTTPS
setup in [Studio deployment](../../deploy/studio/README.md). The phone's localhost
address refers to the phone itself; desktop loopback mode is not a mobile server.


### Try approvals and guided setup without external accounts

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_experience.py \
  --dir /tmp/nexus-experience --check-local
nexus studio --dir /tmp/nexus-experience --port 5004
```

The consumer prepares a local SQLite connection and a paused word-count workflow.
**Approval inbox** reviews and continues it. **Connectors → Saved connections →
Setup guide** checks and explicitly enables the sample database connection. No
provider is called and no external account changes. **Install app** provides native
installation or mobile instructions; **Workspace tools → Deployment checks** reports
what is configured and what needs live validation. Existing advanced samples also
show disabled streaming A2A and persistent MCP connection settings.


### Check a personal VM deployment

Use the reusable verifier through the example consumer (after configuring your VM):

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_verify.py https://YOUR_HOST
```

Set `NEXUS_STUDIO_CHECK_SESSION` in your local environment to verify your existing
Nexus account. Reports omit credentials and private response contents. See
[the personal VM guide](../../deploy/studio/PERSONAL-VM.md) for one-owner setup,
optional subscription workers and a consistent backup/restore procedure.

### Try knowledge, prompt variables and integration setup

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_parity.py
```

This isolated consumer indexes one document, asks a cited question with a variable
prompt, saves feedback/private sharing, checks the catalog and prepares disabled
Tavily/Bedrock profiles. It contacts no provider account. For opt-in research,
container workers and terminal chat, see the [Studio guide](../../docs/STUDIO.md).

## Recovery example

Run `PYTHONPATH=. python examples/nexus-bot/src/studio_recovery.py` from the repository
root to create and restore a disposable Studio snapshot through framework APIs.
It opens no provider account and dispatches no jobs. See
[recovery scope and operator commands](../../docs/STUDIO-RECOVERY.md).

### Try streaming conversations and execution replay

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_conversations.py
```

This disposable consumer uses a synthetic provider to verify progressive text,
typed artifact content, pinned Office sections and approval/node/artifact history.
It contacts no provider account. Reusable rendering, persistence, authorization
and replay services live in the framework. See
[conversation and execution contracts](../../docs/STUDIO-CONVERSATIONS.md).

### Move a knowledge project between workspaces

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_knowledge.py
```

The consumer exports a pinned document and project settings, imports them into a
second disposable workspace with fresh IDs, then verifies local retrieval.
Studio exposes **Import project**, **Export with documents**, default search
settings, related conversations and explicit failed-index retry. See
[portable package limits and authorization](../../docs/STUDIO-KNOWLEDGE-PACKAGES.md).

### Check cached retrieval and local-service failure

Start the checked-in synthetic HTTP service in another terminal, then run the
framework consumer against it:

```sh
postman simulation run postman/simulations/studio-knowledge.sim.yaml --port 49641 --no-history
PYTHONPATH=. python examples/nexus-bot/src/studio_knowledge_vectors.py --url http://127.0.0.1:49641/embeddings/v1
```

This checks selected-endpoint requests, vector reuse, semantic/hybrid ranking,
visible local-service fallback and explicit API/authentication/malformed failures.
It accepts loopback addresses only and uses disposable synthetic sources; no real
provider account is involved. The delayed-service variant proves the actual
client timeout. See [bounds, recovery and timeout commands](../../docs/STUDIO-KNOWLEDGE-INDEX.md).

### Compare prompts and inspect schedules

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_scheduling.py
PYTHONPATH=. python examples/nexus-bot/src/studio_scheduling.py --serve
```

The consumer compares/restores prompt versions, expands a variable prompt and
verifies one-time scheduling through the framework. The optional loopback browser
fixture uses synthetic providers, an actual scheduler and disposable state.
Studio adds readable prompt comparison, explicit schedule Pause/Enable and upcoming
dates/execution history. See [compatibility and recovery limits](../../docs/STUDIO-PROMPTS-SCHEDULES.md).


### Author structured workflows and recover handlers

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_structured_workflows.py
PYTHONPATH=. python examples/nexus-bot/src/studio_structured_workflows.py --serve
```

This consumer maps entries to ordered word counts, runs a bounded loop through a
pinned named workflow and restarts at an approval. Its paused run keeps its original
limits after controller configuration changes. Root/nested completion handlers
consume the framework result. The optional disposable browser server uses synthetic
generation. New Studio canvases expose map/loop/nested/switch/input nodes, schemas,
mappings and handler editing; legacy canvases keep their original format.
See [policy, recovery and migration notes](../../docs/STUDIO-STRUCTURED-WORKFLOWS.md).

### Convert external configuration through reviewed Nexus releases

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_configuration.py
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_configuration.py --controls
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_configuration.py --policies
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_configuration.py --serve
```

The consumer converts an external agent and tool/agent workflow, verifies that conversion
writes nothing, reviews/applies a native release, executes with a synthetic provider
and checks export/reimport with original model/tool names. The optional empty
loopback Studio supports browser Import external/Export checks. No subscription or API
account is contacted. CLI `config convert-interop` and `config export-interop` use the same
owner-authorized services. Unsupported nodes and deployment settings fail with field diagnostics.
`--controls` compiles an external map/loop/switch fixture, executes ordered real word-count
tools through a shared, conditionally executed map template, checks loop/branch metadata, and verifies original-source export/reimport
without a provider account.

`--policies` uses a synthetic API provider that reports failure once, then succeeds.
It verifies bounded retry, real local always/success exit tools and source policy
export/reimport through shared services. No provider account is contacted.

### Typed external decisions and review routing

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_decisions.py
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_decisions.py --case low-confidence
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_decisions.py --case unauthorized
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_decisions.py --serve --case low-confidence
```

This consumer reviews/applies an external choice/boolean/score workflow, runs only its
selected local word-count branch and verifies source/binding export/reimport.
Synthetic decisions exercise low-confidence review and hard credential failure
without a provider account. Optional `--http` accepts only an explicit loopback
Postman simulation endpoint and exercises the actual strict HTTP adapter.
No selected model's real quality or billing is certified.

The native OpenAI decision profile can be exercised against the local contract
simulation, without an account:

```sh
postman simulation run postman/simulations/openai-decisions.sim.yaml --port 49643
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_decisions.py --native --http http://127.0.0.1:49643/native/v1/decisions
```

### Reviewed build activation and rollback

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_build_lifecycle.py
PYTHONPATH=. python examples/nexus-bot/src/studio_build_lifecycle.py --serve
```

This consumer generates through a synthetic provider, reviews/saves an inactive
build, activates its exact revision, tests a local tool and an agent with pinned
model/skill/toolset settings, then refines, deactivates and rolls back. Optional
loopback Studio supports the same review controls on phone, tablet and desktop.
The generator and reviewer are local fixtures; no subscription or API account is
used. [Lifecycle contracts and migration](../../docs/STUDIO-BUILD-LIFECYCLE.md)
describe the separate account-dependent checks.

### external cancellation and separate cleanup review

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_cancellation.py
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_cancellation.py --reject
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_cancellation.py --serve
```

This framework consumer stops a body at approval, then separately approves or
rejects cleanup. Real local word-count tools verify body effects never run,
`on_cancel` finishes, and rejected cleanup retains a warning. The optional server
creates a disposable Studio workspace. No provider or external account is called.

The reviewed-build example also checks a local SQLite connection without enabling
it, persists release-linked health evidence, and advertises saved agent routing
skills through the shared A2A card service. No external account is required.

The reviewed-build fixture now creates two new named child workflows alongside
its primary workflow. All three share review, activation and rollback; the parent
calls both child levels to count supplied words.

Default external configuration example now imports declarative routing capabilities
and an explicitly bound local toolset, then exports/reimports original definitions.
Its bound local toolset stays in native configuration; source export does not
package that dependency.

`src/studio_shared_cleanup.py` demonstrates one cleanup workflow reused by always
and success handlers. It requests each approval separately, verifies two saved
phase receipts and keeps the completed body result. Add `--sql` to use local
SQLite durable workers. No model account, broker or external effect is needed.


`src/studio_shared_loops.py` demonstrates two dependency-ordered external controllers
reusing one local word-count body. The second inherits the first body output;
both retain isolated receipts. Run with `--sql` for SQLite durable workers.
No provider account or external service is required.


`src/studio_openapi_health.py` checks a reviewed, inactive OpenAPI connector using
an explicitly selected read-only health operation. Start the local Postman
simulation first, or run `PYTHONPATH=. python tests/studio_openapi_health_http_acceptance.py`
from repository root for all five HTTP cases. Only synthetic loopback credentials
are used; results omit response bodies and the connector stays disabled.


`src/studio_a2a_http.py` starts a disposable localhost A2A entrypoint, submits via
HTTP+JSON, retries the same message via JSON-RPC 1.0 and retrieves it via 0.3.
It verifies one task, its local word count, and rejection of cancellation after
completion. Add `--sql` for SQLite durable state or `--serve --port 5107` to keep
the fixture available for independent clients. No model account is required.

Optional `--grpc` also sends the same message with the official generated A2A
client and verifies that it resolves to the existing task. Install
`nexus-arc[studio-grpc]` first. Listener stays on loopback, advertises only while
running and closes with Studio. Non-loopback use requires TLS credentials.
