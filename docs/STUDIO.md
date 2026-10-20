# Nexus Studio

A local or authenticated team workspace over Nexus ARC. Start with Quick Build,
chat with saved agents, execute workflows, manage connections, inspect results and
resume approved work. **Workspace tools** is the user-facing name for memory,
checkpoints, coordination, campaigns, analytics, transcription, decisions, file
storage and execution controls. Existing `#workbench/...` links remain valid.

## Start and accounts

```sh
pip install -e '.[documents,mcp]'
nexus studio --dir ./my-workspace --port 5003
```

Open `http://127.0.0.1:5003`. Desktop mode binds to loopback and uses the selected
project directory as the coding agents' workspace. Run one file-backed Studio
process per state directory. SQL mode supports multiple controllers/workers.

| Provider | Account setup |
| --- | --- |
| Codex | Official `codex` CLI and its local sign-in |
| Claude Code | Official `claude` CLI and its local sign-in |
| Copilot, Codex CLI, OpenCode | Their installed official/local CLI configuration |
| OpenAI API | `OPENAI_API_KEY`, `nexus-arc[openai]` |
| Anthropic API | `ANTHROPIC_API_KEY`, `nexus-arc[anthropic]` |

CLI account usage follows the provider's installed configuration and plan; Studio
does not issue subscription tokens or promise that every call uses subscription
billing. Installation checks do not verify login, quota or billing. A failed
selected provider fails the run; there is no automatic switch to another account.
API-only features such as Whisper transcription and rubric judges require their
configured API account. Shared mode resolves credentials for the requesting Nexus
user and isolates supported coding CLIs in reviewed containers.

## Build with AI

Quick Build offers use-case, problem, role and process starters, conversation
refinement, saved draft history, resource attachments, review, save and test.
Generation supports Codex, Claude Code, OpenAI API and Anthropic API. Generation
runs separately from execution, with coding tools disabled/restricted by the
builder adapter; normal desktop workflow execution uses the project workspace.

A proposal may contain zero to eight agents, a sequential workflow or a DAG with
up to 40 nodes, and up to 20 related resources. Resource-only plans are supported.
Resources include prompts, skills, models, evaluations, toolsets, connections,
entrypoints, schedules and external A2A agent profiles. Connections, external
agents, schedules and entrypoints are saved **disabled**. Account setup and
explicit activation remain separate. Team builders cannot apply owner-only
connection/entrypoint/external-agent resources. Saving checks collisions and
rolls back newly created files/resources if application fails.

Review instructions and graph/resource settings before Save. Test uses the real
saved workflow. Generated configuration does not install software, connect an
account or perform remote changes. Build generation and model execution consume
the selected account's usage.

Agents and workflows remain ordinary `agents/*.yaml` and `workflows/*.yaml`.
Edits create immutable Studio versions. **Versions** previews a saved definition;
**Activate this version** restores it for new runs with an optimistic revision
check. Queued runs pin their agent instructions and workflow definition. Catalog
resources have their own history/archive controls and are resolved when used.
External edits become a captured version on the next Studio edit/version view.

## Workflows and tools

The canvas supports agent, registered tool, approval, typed decision, integration
and delay nodes; parallel branches; conditional text or exact decision-choice
edges; and zero to three retries per node. Mutating integrations must directly
follow approval gates and cannot retry automatically. Every external mutation
records an attempt before sending. After an uncertain result, **Workspace tools
→ Controls → External action review** records the observed remote outcome or
permits an explicitly reviewed retry. Then resume the workflow checkpoint.
This prevents automatic duplicate sends; it cannot guarantee exactly-once effects
in an external service that lacks its own idempotency mechanism.

Legacy Nexus YAML uses the original workflow engine, including routes, loops,
retries, persisted approval gates and resumable checkpoints. Contiguous parallel
groups of at most four steps use `parallel: [earlier_step_id]` (Studio also accepts
`parallel_with` when creating definitions). Group members cannot contain internal
routes/final-step overrides; arbitrary branching belongs in the canvas. Successful
parallel outputs persist before siblings finish, so a failed sibling does not
force them to run again. The normal execution/loop limits still apply.

Enabled connections register tools. Saved agents receive tools only through their
explicit `tool_ids` allowlist. Studio's bounded JSON tool-call loop supports up to
eight calls with metadata traces; it does not claim every provider exposes the
same native SDK tool protocol. Native mutation tools cannot self-approve by
returning `confirm: true` from a model.

## Connections and incoming events

The catalog lists 19 shipped integrations and shows actual Studio configuration
coverage. Browsing it never loads accounts or performs remote actions.

* **GitHub/GitLab:** repository setup; read/list/create/update/close issues;
  comments; branch creation; linked/list/get/create pull/merge requests;
  PR/MR comments and merge. Only enabled actions execute. Writes require explicit
  confirmation or a workflow gate. Shared Git runs use the user's linked Nexus
  credentials; GitLab also refreshes OAuth and enforces the sign-in host.
* **Telegram, Slack, Discord:** fixed-destination bot connections, notification
  sends/updates, incoming verified events and user-bound approval buttons.
  Discord ingress supports signed commands/buttons; ordinary Gateway traffic
  continues through the existing ARC bot runtime.
* **OpenClaw:** fixed-destination notifications and an explicit signed bridge
  envelope. Approval callback codes are delivered as text for the bridge to
  return, rather than claiming native buttons on every OpenClaw channel.
* **MCP:** trusted executable/arguments, explicit environment references,
  discovery with input schemas, selected tool names and separately selected
  read-only tools. Install the `mcp` extra and trusted server software yourself.
  Per-call stdio is the default. Enable **Keep MCP sessions open** for an
  account/configuration-scoped pool (16 sessions, 60-second idle cleanup). Calls
  serialize within a session; configuration changes, revoked access and tool
  errors close or reject the session. Failed calls are never replayed.
* **HTTP GET/SQLite:** bounded read-only access, redirects disabled, explicit
  private-network opt-in for HTTP, and workspace-contained read-only SQL queries.
* **LinkedIn/X/Meta/Discord publishing:** existing ARC account/publisher adapters
  through Campaigns, with validation, exact-revision approval and explicit
  single-attempt publishing. Uncertain publish outcomes require remote review.

Entrypoints select an agent/workflow. Bearer webhooks accept `{"task":"..."}` at
`/api/webhooks/<id>` with a random configured token of at least 24 characters.
Messaging uses `/api/channel-events/<id>` and owner-configured user mappings and
allowed channels. Local mappings use the Nexus identity `desktop`; team mappings
use actual Nexus IDs with current project access. Event IDs are durably
deduplicated. Signatures are verified before queuing; event-supplied Nexus IDs
and notification destinations are never trusted. Replies/approval notifications
are off by default and require a matching fixed-destination connection.

Register these URLs in the remote service yourself; saving a profile does not
change remote account settings. Telegram uses its secret-token header. Slack uses
raw-body HMAC and a five-minute timestamp window. Discord uses Ed25519 and the
same replay window. OpenClaw bridges send JSON fields `id`, `sender_id`, `channel`,
`text` or `callback_data`, with `X-Nexus-Timestamp` (Unix seconds) and
`X-Nexus-Signature: sha256=<HMAC-SHA256(secret, timestamp + '.' + raw_body)>`.
Approval codes are single-use, expire after 24 hours, and bind the platform user,
channel, Nexus identity and current workflow gate.

Team messaging connections default to personal credentials. An owner can select
**Workspace bot account** for a fixed channel and explicit service token reference;
model execution still uses the mapped user's credentials. Git accounts never use
that service-account exception.

## Workspace tools

* **Memory:** browse/edit/archive/export conversations; import supported Studio
  or ARC JSON exports. Administrator-configured Redis/filesystem sources require
  explicit legacy numeric user-ID mappings. Filesystem metadata without persisted
  transcript bytes cannot reconstruct ephemeral bot conversations.
* **Checkpoints:** inspect outputs and approvals; resume unfinished canvas or YAML
  work after reviewing the latest checkpoint digest. Desktop uses ARC SQLite
  snapshots; SQL mode stores snapshots in the shared state database.
* **Agents:** sequential/parallel delegation or autonomous specialist selection
  with a user-selected fallback, scoped provider calls and optional configured
  model routing. The separate external-agent library supports A2A 1.0 and 0.3
  JSON-RPC card discovery, send/poll, SSE streaming, input continuation and
  cancellation. A dropped stream with an acknowledged task ID recovers through
  GetTask; an unacknowledged send is never replayed. **Refresh remote status**
  reads a saved task after interruption. Cards cannot redirect credentials to
  another origin. Opt-in bearer entrypoints also expose the text gateway below.
  Push notifications and universal remote-mesh compatibility are not claimed.
* **Campaigns:** review and publish using existing ARC social logic.
* **Analytics:** metadata-only run/status/provider/duration reports; local files,
  SQL state or project-scoped configured Loki. Run details show provider, node,
  tool and guardrail traces. Reported token usage is retained only when returned
  by a provider; no invented cost/subscription-quota figures.
* **Transcription/Decisions:** existing Whisper API transcription and Jev typed
  choices/confidence/fallback; decision nodes also work in the canvas.
* **Controls:** personal prompt-character budgets, matching-task cooldowns,
  external-action review, output limits and forbidden-phrase guardrails. Guarded
  agent output is buffered before publication.

Evaluations accept up to 100 cases, JSON dataset import, contains/excludes checks,
exact output, JSON-path value equality and numeric ranges. Optional configured
rubric judges report their scores and reasons; malformed scores fail the check.
Experiment Lab compares explicit provider/model variants on the same cases.

## Files and storage

Artifacts accept text, PDF, common images and binary files up to **16 MiB** each.
Attachments pin artifact revisions, allow at most five files and cap combined
input bytes at 32 MiB. Text and PDF extraction work with text providers. PDF
extraction runs in a bounded subprocess and needs `nexus-arc[documents]`.
OpenAI API accepts original image/PDF inputs using its multimodal request schema;
other adapters do not silently pretend to have image vision.

File storage defaults to private ARC FilesystemStore bytes with metadata/checksums
in Studio state. Workspace tools → File storage adds filesystem/S3/GCS/Azure
profiles, tests a temporary object, and activates a verified profile for new
uploads. Install the corresponding optional extra. Credential fields are
environment references. Existing versions retain their original store; activation
is not a bulk migration. Old inline artifacts remain readable. Cloud account
access requires real deployment configuration and has not been live-tested here.

## Shared state and deployment

See [shared deployment](../deploy/studio/README.md). Existing Nexus sessions,
project access and credentials are reused; Studio adds viewer/operator/builder/
owner role checks and per-session CSRF. Private execution/history/artifacts stay
owner-scoped, with explicit owner inspection privileges. Revocation is rechecked
at execution, callbacks and credential resolution.

Optional private `--runtime-config` configures SQL state, worker count, legacy
memory mappings, model routing and Loki. PostgreSQL and SQLite share the state
contract: project transactions, durable JSON jobs, exclusive worker claims,
heartbeat leases, fencing of stale writes and interrupted-run recovery. Expired
attempts are not automatically replayed. `--worker-only` consumes SQL jobs without
an HTTP listener. Definitions, audio bytes and local artifacts need a shared
filesystem; cloud artifact storage removes only the artifact-byte requirement.

MQTT/Kafka carry status metadata, separately from the SQL job queue. Production
still requires real identity-provider/account setup, TLS/reverse proxy, broker and
DB/storage permissions, backups, retention and monitoring. These implementation
features do not establish full external enterprise equivalence or provision those
services automatically.

## Example consumer and verification

```sh
nexus-studio-workspace --dir ./studio-sample
nexus-studio-advanced --dir ./advanced-sample --run-local
```

The advanced consumer creates disabled Git/MCP/messaging/A2A configurations,
structured evaluations, example history/files, guarded agents and legacy parallel
YAML. Its optional local test executes a real registered tool, then inspects its
checkpoint and trace; it does not call an external account.

Focused checks cover shared authorization, signed channel events, single-use
approvals, real local MCP stdio calls, real PDF extraction, graph/YAML retries,
A2A message/task protocol, version activation, artifact stores, judges and traces.
Durable-state checks also run against an isolated real PostgreSQL 17 database.
Browser checks use the local preview; provider/remote-account tests otherwise use
injected clients. Live cloud accounts, production SSO/broker, and public deployment
have not been provisioned or certified by these tests.

## Phones, tablets and keyboard access

Studio uses the same routes and actions at every screen size. Below 768 CSS
pixels, **Menu** opens a navigation sheet containing every workspace section and
recent conversations. Selecting a destination closes it; Escape closes it and
returns focus to Menu. Focus stays inside the open sheet. Desktop navigation
returns automatically on wider screens.

Mobile controls use at least 44-pixel button targets and 16-pixel form text.
Tabs and action groups wrap, dialogs remain within the viewport with sticky
footer actions, and long output stays inside its own container. Chat follows the
visual viewport where supported and permits normal page scrolling on short
screens. Phone Enter inserts a newline; Send submits. Fine-pointer keyboards
retain Enter to submit and Shift+Enter for a newline; composition events are
ignored until text input completes.

Canvas background permits touch panning. Drag a node to move it; tap a node to
edit. Focused nodes also move with arrow keys (Shift moves farther). Graph width
stays inside the scrollable canvas instead of widening the page. A skip link
moves keyboard focus to the current content without changing the active route.

A responsive layout does not expose the local server to a phone. Use the existing
authenticated shared HTTPS deployment for access from another device; do not
replace project authentication with an unauthenticated LAN listener. Browser
viewport checks cover 320/390-pixel phones, 768-pixel tablets and desktop widths;
physical iOS/Android keyboard and browser-chrome behavior still warrant device QA.

Responsive verification: 55 route/viewport checks showed no document-wide
horizontal overflow at the tested widths. Mobile menu keyboard focus, Escape,
desktop resize, skip navigation, 320-pixel editing dialogs, canvas selection and
arrow-key movement were exercised in the browser. Existing Studio/Quick Build/
resource regression checks: **48 passed, zero skipped**. Changes retain the same
server access policy and do not open a LAN or public listener.


## Approval inbox, reconnect recovery and installation

**Approval inbox** combines pending workflow gates, publishing reviews, uncertain
external effects, interrupted workflows and remote input requests. List responses
contain summaries; details load on review. Team visibility follows existing owner
rules; an owner can inspect another user's item but cannot decide it from the inbox.
Decisions bind to a digest of the reviewed state and require confirmation. Changed
or resolved items cannot reuse an old decision. Campaign approval never publishes;
external-action retry permission never automatically replays the action.

Run monitors retry read requests with bounded backoff after network/server errors.
Page wake and connectivity events reconnect sooner. Monitoring resumes after reload
using account/workspace-scoped run IDs in session storage; prompts and outputs are
not stored there. Actual failures and permission denials remain distinct from
connection loss. Unconfirmed submissions are never automatically retried: inspect
Activity before repeating an action. Reconnecting also refreshes the session's CSRF
token without replaying a mutation. Bootstrap retries connection errors and clears
private views when sign-in expires.

**Install app** uses the browser's native installation prompt when available, or
shows Safari/Android instructions. HTTPS or localhost is required. The service
worker caches a fixed list of public interface assets only. APIs, auth routes,
downloads and mutations bypass it. Offline startup shows a reconnect notice;
private data, agents and approvals need a network connection. No offline action
queue exists. Updates are explicit and deferred while this tab monitors active
runs. Release packaging must bump the shell cache version when changing assets.

## Guided connection setup

Available integrations → **Set up connection** guides supported connections through
save disabled, read-only check, then explicit enable. Saved connections offer
**Setup guide** to resume. Only configuration references are stored, never entered
secret values. Git checks list issues, HTTP performs GET, SQLite inspects its schema,
and MCP discovers tools without calling them. Messaging checks confirm configuration
only; they do not send messages or certify remote authorization. Successful checks
bind to the exact configuration revision and account; editing settings invalidates
that proof. Team setup requires owner access and links to the existing Nexus login
and account service. Bot registrations and service/OAuth consent still happen in
the service's own settings. The guide does not silently register apps or grant scopes.

## Incoming A2A text gateway

For an owner-configured bearer webhook, explicitly enable **Expose as an incoming
A2A text agent** and the entrypoint itself. The existing random bearer token protects
both card discovery and RPC. The card is `/api/a2a/<id>/agent-card.json`; RPC is
`/api/a2a/<id>`. Configure that card path in an outgoing Studio profile. Requests use
`A2A-Version: 1.0` or `0.3` (omitted means 0.3). Calls execute as the entrypoint creator
and recheck project access. Client-supplied identity/provider overrides are ignored.

The gateway supports text sends, task reads, bounded SSE streams, subscriptions,
message-ID deduplication, authenticated cards and 1.0 task listing. Reusing a message
ID with changed content is rejected. Streams last up to 30 seconds; GetTask or a new
subscription continues observation without sending again. Workflow gates require
the Nexus UI; remote text cannot approve them. Gateway cancellation supports paused
approval workflows; running provider tasks report TaskNotCancelable. Files, push
notification configuration, general in-task chat continuation, gRPC and REST bindings
are outside this gateway's supported subset. Capabilities advertise streaming and
explicitly disable push. References: [A2A 1.0](https://a2a-protocol.org/v1.0.1/specification/)
and [A2A 0.3](https://a2a-protocol.org/v0.3.0/specification/).

## Deployment verification

**Workspace tools → Deployment checks** distinguishes configuration, a live database
read and unverified deployment checks. Owners can inspect team readiness. The
read-only [deployment verifier](../deploy/studio/verify.py) verifies real HTTPS,
anonymous API denial, existing Nexus sessions, installation assets and private
cache headers. Automated checks exercise an isolated trusted TLS endpoint, separate
worker processes, real MCP subprocesses and Studio-to-Studio A2A streams. These are
local integration checks; physical phone testing, production SSO, brokers, cloud
stores and backup restoration still require the actual deployment and accounts.


Latest follow-up verification: **146 Python regression tests passed, zero skipped**,
plus **5 Node browser-logic tests passed** for reconnecting, shared monitors,
permission failures, unconfirmed-submission handling and cache privacy. Browser
checks exercised sample approval → completed word count, guided connection
checking/enabling, cached offline startup and automatic recovery after restoring
the server. New inbox/deployment screens had no document overflow at 320, 390,
768 and 1280 pixels; the 320×568 MCP setup dialog stayed within the viewport.
A built wheel includes the manifest, icons, service worker and pooled MCP runtime.
These counts describe local checks, not production or physical-device certification.


For the single-owner personal VM plan and the latest validation evidence, see
[Personal VM setup](../deploy/studio/PERSONAL-VM.md). Hosted verification is now
reusable through `nexus.core.studio_deployment.verify`; the deployment script and
`studio_verify.py` example both consume it. Local broker checks found and fixed
MQTT authentication/reconnect handling; no production account was provisioned.
