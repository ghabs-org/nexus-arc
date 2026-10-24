# Reviewed builds, resource bindings and recoverable releases

This checkpoint advances Milestone 5.

## Build and refine

Quick Build now reviews an exact proposed revision before saving. The review lists
changed resource paths and component dependencies. Save creates an immutable
configuration release, updates the build and keeps its components inactive.
The existing apply request format still works; it now also saves inactive.
Reopen, refine, review and save use the same configuration validators as import
and CLI operations. Unsupported resource fields fail rather than disappear.

The authoring fixture creates and updates agents, a workflow, prompts, skills,
model profiles, toolsets, connectors, entrypoints, schedules, evaluations and
external-agent profiles. Binary artifacts retain their explicit upload/attachment
path; they are not embedded in configuration exports. A build keeps an optional
primary workflow as its test target and supports up to twelve additional named
child workflows in `proposal.workflows`. Local IDs resolve across the complete
prospective release, including resource targets. All children share review,
inactive save, activation, refinement and rollback. Cycles, unavailable references,
incompatible child graph versions and nesting limits fail before publication.
The CLI/configuration validators use the same named-workflow preflight; external
conversion retains a diagnostic for each invalid graph.

Agents can reference model profiles, skills, toolsets and selected connections.
Supported runtime settings include timeout, total model-call and tool-call limits,
output schema and routing capability metadata. Queueing resolves model/skill/toolset
settings once. Advanced settings and named child definitions can be edited in
the existing Quick Build UI; invalid JSON remains editable and sends no review
request. Basic agent edits are preserved when advanced settings change.
Credentials and connection permissions are checked at execution;
credential values never enter snapshots. Imported MCP allowlists can narrow an
existing connection's read-only tools, never widen them or authorize mutations.
Raw agent-card routing skills and bounded provider/version/documentation metadata
are preserved in source export and advertised by authenticated A2A discovery for
the exposed agent target. Entry identity, security and transport capabilities stay
server-controlled. Declarative `spec.skills` now maps to these same routing
capabilities, while `additionalConfigurations.skills` continues to mean knowledge
bundles. Optional capability IDs receive stable content-derived local IDs; original
source is retained unchanged for export. Dedicated `spec.toolsets` aliases require
explicit bindings to existing Nexus toolsets. No extra tools or transport modes
are granted through descriptive capability metadata.

Automatic delegation uses the queued agent's capability descriptions and model
settings. Later profile edits cannot replace an in-flight selection's hints. The
coordinator's existing description limit remains 2,000 characters; full capability
metadata remains available in the authenticated A2A card.

## Activate, deactivate and roll back

Owner-scoped lifecycle operations use the existing Nexus account/project policy:

| Operation | Route |
| --- | --- |
| Review draft | `POST /api/studio/builds/{id}/review` |
| Inspect components, dependencies and releases | `GET /api/studio/builds/{id}/lifecycle` |
| Review activation/deactivation/rollback | `POST /api/studio/builds/{id}/plan` |
| Apply the exact plan | `POST /api/studio/builds/{id}/transition` |
| Explicit read-only connectivity check | `POST /api/studio/builds/{id}/health` |

The existing `/api/builds/{id}/apply` accepts optional `review_id` and `digest`.
Plans pin build state, definitions, resource records, lifecycle markers and shared
dependency revisions. Drift requires a new review. Activation also rechecks the
original build owner's credential environment; an administrator cannot substitute
their own account credentials. Existing configuration inventory checks provider
setup and MCP executable presence without running an installation command.

Connectors, schedules, entrypoints and external agents require explicit enabling.
Missing credentials, inactive shared dependencies, missing/cyclic references,
unreviewed edits or revoked execution access block activation. New chat/workflow,
delegation, evaluation and external-agent launches honor component markers.
Connection invocation, entrypoint authorization and scheduled dispatch honor their
own markers too. Individual enabled toggles cannot bypass build activation.
An inactive schedule retains its due time until its build can activate.

Refinement retires removed external exposures in the same release. Rollback
restores a selected saved proposal and its configuration as a new inactive build
revision; extra external resources stay disabled. Data remains recoverable.
Rollback never reverses already completed external effects.

Configuration inspection stays passive. Owners can explicitly check managed
SQLite connections (SELECT 1), SQL connections (inspect_schema) and MCP connections
(discovery only, if permitted). The check does not enable a connection, invoke its
tools, install software or run an agent. It uses the original build owner's access
and credential environment. Other component types have no live probe yet.

Results use `nexus-resource-health/v1` records and pin build revision, release,
component digest and lifecycle marker. They survive restart. Configuration or release
changes make results stale, including changes during a probe. No workspace lock is
held during I/O; adapters compare and use the captured configuration. Stored and
returned results contain metadata only, without response bodies or exception text.
A passed probe establishes that specific operation at that time, not quota, model
quality or future availability. These additive records are included in existing
whole-state backup/restore. No record rewriting migration is required.

## Refresh from workspace and delete a build

Agents, workflows and resources saved by a build can still be edited in their own
editors or on disk. A managed edit then blocks launch and activation with
`unreviewed_changes`, and a reopened draft can no longer be saved. **Refresh from
workspace** (`POST /api/studio/builds/<id>/refresh`) is the review for those edits:

1. A request with `{revision}` previews the change list and returns a `digest`.
   Nothing is written.
2. A request with `{revision, digest}` applies exactly that preview. It is refused
   if the workspace changed in between.

Confirming replaces the build proposal with the current definitions and resources,
drops resources that were deleted, and increments the build revision. A draft
is re-pinned to the current files, so it can be saved again; unsaved draft edits
are discarded. Changed resources become inactive at their new content, and changed
connections, entrypoints, schedules and external agents are disabled, so they run
again only after **Review activation**. Refresh never rewrites a workspace file:
if a definition uses settings the proposal format cannot express (for example
`max_retries` or extra step fields), the refresh is refused and names it.

**Delete build** (`POST /api/studio/builds/<id>/delete` with `{revision}`) removes
only the build record. Its agents, workflows and resources stay as ordinary
workspace items: their lifecycle markers are removed, so they are no longer gated
by this build's activation review. Release history is kept. Deleting a build with
saved resources requires the owner role in shared mode; a build that is still
generating cannot be deleted.

When `research.search` is not configured in shared mode, activation treats
`:web_search` and `:research` as covered for agents on Codex, Claude Code or OpenCode
workers, which use their built-in web search (see
[data workers](STUDIO-DATA-WORKERS.md#search-without-a-configured-account)).
Toolsets defer this check to the agents that use them; agents on other providers,
and workflow tool nodes, still require the search account.

## Release recovery and migration

Release journals cover definitions, catalog updates, build state, markers and
transition receipts. File state restores the previous records after interruption.
SQL state commits those records together; file-journal finalization waits for the
outermost SQL transaction to commit. A crash before that commit rolls back, even
if an inner release function had returned. A crash after commit preserves the
acknowledged release and recovery finalizes its pending journal. Retrying a
committed transition returns its receipt without another release.

Eight abrupt process-exit fixtures cover four publication boundaries on file and
SQLite-backed SQL state. This is local recovery evidence, not PostgreSQL/VM crash
certification. Existing metadata-only schema upgrades remain unchanged; new record
families have explicit `v1` formats. Older resources and saved builds are not
rewritten or deactivated during upgrade. Their next save adopts inactive-by-default
build releases. Existing queued runs retain their pinned definitions/settings;
connection disablement and account revocation still take effect at execution.

Back up the stopped workspace using the existing documented snapshot procedure.
An interrupted configuration journal must recover before a snapshot is accepted.
Do not delete a journal to bypass recovery or overwrite an unrelated manual edit.

## Native decisions and collection templates

Direct OpenAI text decisions use an explicit `openai` backend and `gpt-6-luna`
model binding. The adapter maps native choice/predicate/score arrays into the
shared typed decision runtime. It uses the selected account's separate API key;
Codex subscription credentials are not substituted. The dependency contract is
derived from [official OpenAI documentation](https://developers.openai.com/api/docs/guides/decisions).
Orbit discovery returned HTTP 403; a maintained owner mock could not be searched
without Postman login. The committed simulation was built from that official
contract. No workspace publication or real provider request occurred.

Fifteen HTTP simulation cases cover typed results, low-confidence review,
transient recovery, refusal, malformed/duplicate answers, incorrect score labels,
bounded bodies, timeouts, redirects and hard authentication/quota failures.
Provider bodies and credentials are excluded from failure messages and history.

Map templates can be shared by multiple maps. Body `when` conditions run before
resolving arguments; skipped map items retain an ordered null result. Pruned loop
bodies retain the loop summary and a null final-body alias. Distinct persisted
controller/item scopes prevent one map from consuming another map's checkpoint.

Image decisions and their image limits, routing inside collection bodies,
switch-as-body, concurrent shared loops, cross-role collection/exit templates and broader expression-engine
compatibility remain unfinished. Unsupported constructs still produce preflight
diagnostics and no saved configuration. Source-backed export refuses a stale
native graph/source mismatch; arbitrary native changes use native export or an
explicit source reconversion.

## Reproduce locally

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_build_lifecycle.py
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_configuration.py --controls
postman simulation run postman/simulations/openai-decisions.sim.yaml --port 49643
# In another terminal, while that local simulation is running:
NEXUS_NATIVE_DECISION_SIM_URL=http://127.0.0.1:49643/native/v1/decisions \
  python -m pytest tests/studio_openai_decision_http_acceptance.py -q -o addopts=''
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_decisions.py \
  --native --http http://127.0.0.1:49643/native/v1/decisions
```

The framework example's `--serve` opens a disposable loopback Studio. Phone,
tablet and desktop checks exercise the real review/lifecycle controls. Real
accounts, independent external runtime round trips, physical devices and a personal
HTTPS VM remain separate live checks.

## Shared exit phases and continuation handoff

One external exit-handler template may now serve multiple outcome phases. `always`
runs first, followed by the matching success, failure or cancellation phase. Each
intentional invocation has its own durable checkpoint; the final successful
invocation supplies the template's workflow-wide output. A failed phase stays
nonfatal and does not prevent a later matching phase. Approval/input gates and
pinned named children remain independent for each invocation.

For reused handlers, scopes include the phase, for example
`root/interop_exit/cleanup/always` and `root/interop_exit/cleanup/on_success`. Existing
unique-handler paths remain unchanged, so old paused runs retain their receipts.
Source export preserves the original shared `on_exit` mapping. Collection-body
and exit-handler cross-role template reuse remains unsupported.

Approval, input and cancellation continuations now wait until the finishing
attempt releases its execution slot. File workers retain a pending continuation
until their callback finishes. SQL workers pin that request to the current job's
lease token, then atomically queue it at completion; an obsolete worker cannot
publish it. This closes the boundary where a visible paused run still had a
running durable job. These additive `nexus-workflow-resumption/v1` records use the
existing state/job services and require no SQL column migration.

An expired lease never automatically dispatches a pending continuation. File
startup discards unfinished handoff receipts and marks the run interrupted;
explicit checkpoint recovery remains required. SQL lease recovery retains the
existing interrupted/effect-review behavior and clears stale receipts on explicit
resubmission. Whole-state backups include the additive record collection.

Validation includes file/SQLite-SQL restart between shared phases, deleted child
source with pinned recovery, nonfatal failures in either phase, cancellation,
deterministic pause/finalizer races, obsolete worker fencing and expired handoff
recovery. The new framework consumer runs both storage modes without accounts:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_shared_cleanup.py
PYTHONPATH=. python examples/nexus-bot/src/studio_shared_cleanup.py --sql
```


## Ordered shared loop templates

Two or more external loops may reference the same body template when dependencies
establish their execution order, including transitive dependencies. Each controller
has its own iteration count and checkpoint scopes. The next controller inherits
the latest shared body output as its first `previous` value. Each controller pins
that initial value before dispatch and retains it across approval/restart; later
iterations cannot accidentally seed a resumed controller from its own later output.
The final body alias and original external source survive export/reimport.

Concurrent shared loops reject with a diagnostic at the conflicting `/node` path.
Mixed map/loop ownership and cross-role collection/exit templates remain unsupported.
Native graph validation also checks alias ordering, preventing configuration imports
from bypassing this restriction. No process-wide ordering is inferred.

Migration is additive: reused controllers store `scope/controller/interop_initial` in
existing graph-run outputs. Existing distinct-loop paths and previous-value behavior
remain unchanged. SQLite and file recovery fixtures pause during the second
controller, delete its source child definition, and resume the pinned revision
without repeating earlier tool calls. Static frozen Python source backs shared
node-output semantics; independent live external interoperability remains pending.

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_shared_loops.py
PYTHONPATH=. python examples/nexus-bot/src/studio_shared_loops.py --sql
```


## OpenAPI connectivity checks

In the OpenAPI connection editor, enable the operation, then choose it under
**Connectivity check**. Only GET, HEAD and OPTIONS operations without required
parameters or request bodies qualify. Save the connection through normal review;
request connectivity from its owned build's health control. Browsing, import,
review and save never contact the service. No endpoint is chosen automatically.

Checks use the server-reviewed `health_operation`, existing read permission,
original owner's credential references, destination policy and expected connector
snapshot. Client-supplied operation/arguments cannot replace it. Results retain
status and release/revision metadata only; response bodies, credentials and remote
error messages are discarded. Redirects are not followed; unsuccessful statuses,
network failures and responses over 256 KiB fail without retry or another provider.
Changing the selection makes earlier health evidence stale. Checks do not enable
the connector or activate its build.

Migration is additive. Omitted/empty `health_operation` preserves older normalized
data exactly and offers no live check. Existing `nexus-resource-health/v1` records
remain readable. Frontend public-shell cache advances to v24.

```sh
# From repository root, requires installed Postman CLI; synthetic loopback only.
PYTHONPATH=. python tests/studio_openapi_health_http_acceptance.py
# Or run the simulation and example separately:
postman simulation run postman/simulations/studio-openapi-health.sim.yaml --port 49645
PYTHONPATH=. python examples/nexus-bot/src/studio_openapi_health.py
```

The example owns its simulated service and contract; no vendor service or account
is represented. Five observed dependency requests cover success, 403, 503,
redirect and oversized response. Separate fixtures prove argument-dependent/write
selections reject, old data remains unchanged, health payload overrides are ignored,
and release evidence becomes stale. Phone editor saves/removals/reopens and
390/820/1440 layouts pass; physical-device and personal HTTPS VM checks are pending.

Direct routing from collection templates remains rejected: frozen docs require
switch targets to depend on the switch while prohibiting template IDs in other
nodes' dependencies. This composition needs independent behavior evidence;
explicit named nested workflows remain available. No additional parity acceptance
is inferred from this investigation.
