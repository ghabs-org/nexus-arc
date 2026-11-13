# Analysis container shutdown

Analysis workers now keep a dedicated lifetime channel open after sending their
bounded JSON input. The container reads one newline-framed request, then watches
the remaining stdin connection. EOF or a stop byte exits its PID 1 process; Docker
then stops the container namespace, including detached children, and `--rm`
removes it. This covers owner death and Docker-client/process-group termination
on the verified local Docker engine without relying on a surviving host cleanup
thread.

The monitor uses a raw file descriptor, so it cannot hold Python's buffered-stdin
lock during interpreter shutdown. The generated program receives `/dev/null` as
stdin, preserving the previous noninteractive behavior. Fork/exec and resource
limits are set before the monitor thread starts.

Studio also tracks its active container clients. Closing starts their cancellation
before waiting for executor threads and alongside MCP teardown. Cleanup targets
only each launch's generated container name and Docker context; it never sweeps
other containers. Launch is rechecked after image inspection so shutdown can win
that race. Direct container execution is rejected after the worker closes, and
late results cannot publish artifacts after Studio starts closing.

Each job snapshots its Docker environment. An explicit context, including the
special `default` context, removes ambient host/context/TLS overrides so image
inspection, launch and cleanup cannot be redirected by `DOCKER_HOST`. Implicit
context selection retains the caller's environment. No environment is passed into
the analysis program beyond its existing fixed local variables.

## Compatibility and recovery

Public execute inputs/results, stored profiles and existing Docker images are
unchanged. Only the internal stdin transport is framed; the current framework
supplies the matching wrapper at invocation, so worker images need no rebuild.
Network isolation, immutable image IDs, no automatic pulling, read-only roots,
non-root users, capability restrictions, resource limits and model-only mounts
remain in place. User/generated code runs only inside its container.

Normal and cancellation paths perform bounded cleanup. If daemon removal fails,
a read-only query checks for that exact name on the launch context. A daemon
failure is reported as unconfirmed cleanup, not successful removal. Studio logs
that condition; execution is never replayed to compensate for a cleanup failure.

## Durable cleanup recovery (26 October)

Studio now creates a stopped container first and starts it exactly once, by its
acknowledged immutable ID, after checking shutdown and routing again. A failed,
interrupted or unacknowledged create never triggers an automatic start or retry.
A delayed create can therefore leave a stopped object; it cannot run the analysis
program. The wrapper also has a 15-second initial-frame deadline, so an incomplete
late attachment cannot leave it waiting indefinitely for owner input.

Before creation, Studio writes a versioned private ownership record under
`<private-state>/analysis-workers/<generated-name>/record.json`. It uses the
existing workspace record writer and existing kernel file lock. Records contain
only the generated name, optional context name, routing and host fingerprints,
creation uncertainty and an acknowledged ID when available. They contain no code, input
files, credentials or environment values. The format is host-local even when
workspace records use SQL. No schema rewrite or persisted worker-profile change
is required. Existing workspaces gain records only when they dispatch a worker.

An operator embedding Studio can explicitly request a bounded recovery pass:

```python
report = studio.data_tools.sandbox.recover_cleanup(timeout=8)
```

Recovery processes at most 32 records within the selected time budget (greater
than zero, at most 30 seconds). It skips records from another host and live
kernel-lock owners, verifies the
original routing fingerprint, and checks the exact container name, ownership
label and immutable ID before removal. Changed routes and mismatched ownership
remain pending; recovery never guesses a replacement context or sweeps containers.
It performs only inspection and removal, never create, start or task replay.
The owner action below exposes this same recovery pass. No background scheduler
was added.

After an unknown create, absence returns `awaiting_creation` and retains its
record: an outstanding daemon request might still finish later. Once an owned
late container appears, recovery acknowledges its ID before removal. A lost
removal reply can then be resolved by absence during the next pass. This also
works after process restart. A permanently absent, unacknowledged create remains
pending until an operator independently resolves the original request; no timeout
is treated as proof that a remote mutation never happened.

The existing owner-only Deployment checks surface active/unresolved record counts
without contacting Docker. Backup and restore exclude `analysis-workers`, so a
restored workspace cannot take over the original server's cleanup authority.
Kernel lock markers remain after record removal to avoid inode replacement races.
For rollback, stop workers and retain original-host journals for explicit recovery;
older runtimes ignore the new records and cannot resolve them automatically.

Local acceptance covers actual unreachable Docker transport, stopped late creation,
restart recovery and the input deadline. Remote/SSH disconnection, an actual daemon
restart, concurrent context reconfiguration during dispatch and native
Windows/frozen/installed acceptance remain separate. Host identity uses hashed
local host/node identity; a renamed host or changed node identity requires
operator review rather than silently taking over its records. Readiness and API compatibility
are checked locally; this change does not certify those deployment scenarios.

## Retry cleanup from Studio

Open **Workspace tools → Deployment checks** and choose **Retry container
cleanup**. In shared mode only the current owner can use it. Local mode retains
its existing local-session and CSRF protections. The button checks the saved
records with a fixed eight-second budget and shows each inspected outcome:
removed, active worker, another server, changed Docker connection, mismatched
ownership, unconfirmed creation/removal, or a record needing review.

Active work is left alone. A positive remaining count keeps uncertainty visible;
cleanup never restarts an analysis task. Repeated clicks are disabled while the
request is pending. Concurrent requests to the same server process receive 409.
Lost replies are not automatically retried; refresh Deployment checks before an
explicit retry. The last confirmed results stay visible if readiness refresh fails.
A closing Studio returns 503 without dispatching cleanup.

`POST /api/workbench/readiness/cleanup` accepts `{}` only, plus server-injected
identity metadata. It rejects caller-selected contexts, worker IDs, commands and
timeouts. Existing session, current owner role, project access, CSRF, trusted-host
and origin checks apply. GET readiness remains read-only and adds passive
`analysis_cleanup` availability/count metadata. No saved-record migration or
worker-image change is required. Existing clients retain all previous fields.

The API contract and saved assertions are in
`postman/contracts/studio-container-cleanup.yaml` and
`postman/assertions/studio-container-cleanup.js`. UI, real HTTP/auth, Postman and
actual local Docker evidence is recorded in
`STUDIO-CONTAINER-CLEANUP-UI-CHECKPOINT.json`. Physical phones, the personal VM
and rebuilt/installed desktop validation remain separate.

## Proof and use

The old wrapper left an active container running after its Docker client was
killed. The lifetime prototype stopped and removed containers after both EOF and
client death. Framework acceptance now covers normal Studio close, abrupt owner
death and whole-process-group termination with a detached, SIGTERM-resistant child
inside the container. Existing SQL/JQ/chart/image/private-artifact workloads and
MCP cleanup also pass.

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_docker_lifecycle.py --image sha256:INSTALLED_IMAGE_ID --context default --mode close
NEXUS_ACCEPTANCE_WORKER_IMAGE=sha256:INSTALLED_IMAGE_ID NEXUS_ACCEPTANCE_DOCKER_CONTEXT=default PYTHONPATH=. python -m pytest tests/studio_docker_lifecycle_acceptance.py -q -o addopts=''
```

Use an actual installed immutable image ID; the example does not fetch one.
Process-group acceptance is explicitly POSIX-only. Native Windows, remote daemon
and frozen/installed distribution acceptance for this change remain separate.
Original lifetime evidence is in `STUDIO-DOCKER-SHUTDOWN-CHECKPOINT.json`; the
recovery follow-up is in `STUDIO-CONTAINER-RECOVERY-CHECKPOINT.json`.

Per the user's instruction, no DMG was rebuilt. Previously built DMGs contain the earlier runtime;
this source change creates none. Recorded checksums are retained. The DMG files
are currently absent from `releases/`, so their hashes were not reverified. A new
DMG requires an explicit request.

## Context changes while work is running

Analysis operations now keep one Docker connection from image inspection through
create/start and cleanup. Updating a named context or switching the default
selection cannot redirect an in-flight operation after preflight. Explicit
recovery also uses one connection for its entire inspect/remove sequence.

Default contexts bind the resolved host. Named contexts are exported and imported
by Docker into a private temporary configuration. TLS/SSH settings and configured
daemon HTTP headers are retained; registry login settings are not copied. The
original context and global Docker configuration are unchanged. Preparation
rejects a context changed during export/import. Normal completion/errors remove
temporary files; abrupt process death may leave a private snapshot under the
host's temporary-file retention policy. The existing v1 journal keeps route/host
hashes only and remains compatible with earlier records. No schema migration,
worker image rebuild, public API change or automatic task retry is required.

Run the explicit local race drills using an installed immutable image:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_docker_route.py --image sha256:INSTALLED_IMAGE_ID --context default --mode dispatch
PYTHONPATH=. python examples/nexus-bot/src/studio_docker_route.py --image sha256:INSTALLED_IMAGE_ID --context default --mode selection
PYTHONPATH=. python examples/nexus-bot/src/studio_docker_route.py --image sha256:INSTALLED_IMAGE_ID --context default --mode recovery
```

These drills modify only disposable configurations. The explicit
`tests/studio_docker_route_acceptance.py` also checks real CLI TLS/SSH context
roundtrips, using a temporary synthetic certificate and no remote connection.
Docker and OpenSSL are required; missing prerequisites fail instead of skipping.
See [the checkpoint](STUDIO-DOCKER-ROUTE-CHECKPOINT.json). Live remote/SSH service
operation, actual daemon restarts, changes to DNS/SSH settings outside Docker
contexts and installed/frozen platform validation remain pending. No DMG rebuilt.
