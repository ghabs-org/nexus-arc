# Analysis worker lifetime contract

The original lifetime change preserves task input/result schemas, credential
scopes and stored worker profiles. The later owner cleanup route is specified below. Existing analysis run/graph requests retain their authorization and
artifact ownership rules. During shutdown, new worker dispatch is rejected and
late results cannot become private artifacts. No task or external action is
replayed as part of cleanup.

The Docker stdin transport is internal: one bounded newline-framed JSON request
followed by an open lifetime channel. The wrapper watches that channel and exits
on EOF/stop. Generated programs remain noninteractive and receive `/dev/null`.
Container PID 1 exit closes its namespace, including detached descendants.

Owner-side cleanup uses only the generated name and launch context. Explicit
context selection overrides ambient Docker host/TLS flags. Failure to reach the
daemon is unconfirmed cleanup, never proof of removal. No global container sweep,
image pull, host code execution, network access or new host mount is introduced.

Acceptance uses the real local Docker engine and explicitly installed pinned
images. No HTTP stand-in would establish namespace/attach semantics. Separate
fixtures inject daemon/launch failures; source API/workflow compatibility tests
exercise the existing job and artifact pipeline. Remote daemon recovery and native
Windows/distribution checks remain visible as separate prerequisites.


## Cleanup recovery extension

Execution now uses `create` followed by one acknowledged-ID `start --attach
--interactive`. Recovery never repeats either command. Studio writes host-local
versioned ownership records before creation, using existing state/locking helpers.
Only an explicit bounded `studio.data_tools.sandbox.recover_cleanup(timeout=8)`
pass attempts unresolved removal. The later owner action below exposes this same
operation through the existing protected workbench routes.

Recovery skips other hosts, active locks and changed routing, validates exact name, label and
immutable ID, then removes by ID. Lost create acknowledgments remain uncertain
when absent; they do not expire into success. Lost remove replies settle only
with later authoritative absence. Backup/restore excludes cleanup authority.
Owner-only deployment readiness counts records without making Docker requests.

Docker is a local CLI/daemon dependency, not an HTTP member of a Postman
simulation. The acceptance consumer runs real Docker clients and owned containers,
including an actual unreachable Unix-socket transport. Independent process-command
fixtures cover lost replies, timeout, changed routing and concurrency. No stubbed
HTTP result is presented as engine acceptance. Remote/SSH, actual daemon restart
and installed OS checks remain separate.

## Owner cleanup action

`POST /api/workbench/readiness/cleanup` accepts an empty JSON object. Existing
Nexus session/desktop session, same-origin and Studio CSRF checks apply. Shared
workspaces additionally require the current owner role. Local single-user mode
uses its existing local-session/CSRF protections. Caller-selected worker IDs,
contexts, commands or timeouts are rejected; the server uses saved ownership
records and its fixed eight-second budget.

A confirmed HTTP 200 response contains `checked_at`, `pending` and `items`, each
with `name` and the existing recovery status. A pending count of zero means no
ownership records remain, not that an external action was undone. Busy requests
return 409; closing services return 503. These outcomes are never automatically
retried. Existing GET readiness remains read-only. The UI shows one result per
inspected record and preserves uncertainty, skipped owners/hosts/routes and
uninspected remaining counts.

## One connection throughout dispatch and cleanup

Docker context names are mutable. A fingerprint check alone cannot prevent a
context from changing between that check and the next CLI command. Studio now
pins one connection before image inspection and retains it through create,
acknowledged-ID start and active cleanup. Explicit journal recovery pins its
connection before listing, inspecting or removing a known-owned container.

The default context uses its resolved explicit host and captured TLS environment.
A named context uses Docker's own export/import operations to create a private
temporary configuration, retaining its TLS/SSH settings and selected daemon HTTP
headers. Registry login settings and unrelated proxy/container settings are not
copied into named-context snapshots. Source contexts and global CLI selection are
never modified. Snapshot preparation fails closed if copied metadata differs.

Private directories use mode 0700 and files 0600. Normal completion and errors
remove the snapshot. An abruptly killed owner can leave a private temporary
directory for the host's temporary-file retention policy; no journal or exported
workspace contains those copied TLS keys or headers. Recovery still rechecks the
original host and route fingerprint and never trusts a temporary path from a
browser or restored snapshot. Previously saved v1 journal hashes remain valid.

No HTTP request, permission, result, timeout or uncertainty status changes.
Real local fault injection covers named-context mutation after preflight,
implicit default-selection changes, and context mutation just before recovery
listing. Real CLI roundtrips additionally preserve synthetic TLS certificates,
SSH settings and daemon headers without contacting a remote service. Live
remote/SSH sessions, DNS/SSH configuration changes outside Docker contexts,
daemon restart and installed/frozen OS acceptance remain separate.

Docker's documented [context export](https://docs.docker.com/reference/cli/docker/context/export/)
and [context import](https://docs.docker.com/reference/cli/docker/context/import/)
commands provide the snapshot format; the CLI documents
[configuration and daemon headers](https://docs.docker.com/reference/cli/docker/#custom-http-headers).
See `docs/STUDIO-DOCKER-ROUTE-CHECKPOINT.json` for scoped implementation evidence.
