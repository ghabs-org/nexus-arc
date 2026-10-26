# Filtered Docker recovery contract

Transport acceptance: `STUDIO-SSH-PROXY-CHECKPOINT.json` and
`STUDIO-TLS-PROXY-CHECKPOINT.json` record actual local Docker CLI connections
through this same private filtering contract. The TLS fixture requires trusted
server identity and client certificates, rejects missing/untrusted clients and
untrusted/wrong-host servers, and retains cleanup uncertainty on disconnection.
Only the trusted proxy receives the raw daemon socket; the test gateways receive
its filtered Unix socket. They neither simulate Docker responses nor add a
production network listener. Personal VM provisioning and certificate lifecycle
remain separate. Existing request/response formats below do not change.

This is the deployment proxy's private Unix-socket Docker API, not a public
Studio route. Existing socket ownership, configured-image allowlists, create
hardening, mount checks and application permissions remain in force.

Deployed-controller acceptance is recorded in
`docs/STUDIO-DEPLOYED-WORKER-CHECKPOINT.json`. The explicit live gate
`tests/studio_deployed_worker_acceptance.py` verifies the configured CLI-runner
image through the controller's existing filtered socket, without credentials or
provider calls. It checks hardening denials, stopped inspection, static execution,
cleanup and retained absence. Use `docker container inspect` for absence: generic
`docker inspect` may try image inspection after a container 404 and receive an
independent image-allowlist denial. This gate does not restart the proxy, certify
interrupted ownership recovery or configure analysis workers.

## Scoped lookup

`GET /containers/json` (also with the Docker API version prefix) accepts only
`all=1` and one `filters` value containing one exact anchored name:

```json
{"name":["^/nexus-analysis-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa$"]}
```

The name must belong to a container created through the current proxy process
or restored from its configured private ownership ledger.
The proxy constructs an upstream filter with that name **and its full tracked
ID**. User-supplied ID filters, multiple/wildcard names, extra query options,
ordinary daemon listings and unknown ownership return 403. Legacy boolean-set
filter encoding remains readable. No successful empty list is manufactured when
the proxy cannot prove ownership.

## Stable mutation target and removal recovery

Authorized name and short-ID requests are rewritten to the resolved full ID
before forwarding. A container reusing an old name cannot inherit its authority.
Ambiguous names or short IDs are denied; a known full ID remains usable.

Deletion keeps bounded ownership tombstones for subsequent absence checks,
including lost replies and automatic removal. Only upstream 204/404 marks an
entry retired; a failed deletion keeps its active ownership. Retired entries
are evicted before live entries under the existing 512-record process limit.
The proxy never starts or reruns analysis code during cleanup.

Without configured persistence, ownership remains process-local. After restart
or record eviction, unknown containers require independent operator review.
The proxy returns 403 and Studio retains an unconfirmed cleanup journal; lost
authority never becomes proof of absence.

## Optional private restart authority

`PROXY_STATE_DIR` enables a proxy-only SQLite ledger. Its precreated service-owned
directory must use mode 0700 and remain outside every writable Studio mount;
the database uses 0600. Symlinks, hard links, unsafe ownership/permissions, corrupt
receipts, unsupported versions and another daemon/project binding fail closed.
The existing Nexus `project_key` supplies project scope. No browser request can
select the directory, binding or restored grants.

The ledger commits complete owned-ID receipts before forwarding create
acknowledgments. SQLite transactions retain an exclusive single-writer lock
throughout the proxy's lifetime. Receipt updates preserve the existing bounded
inventory and retired-entry policy. Storage failures block new creation and
execution; known-ID lookup, inspection and deletion remain available for review.

Restored receipts grant **lookup, inspection and deletion only**. They never grant
attach/start/wait/kill/resize or automatically dispatch old code. Each upstream
connection first reads the daemon identity with keep-alive, then forwards the
authorized request on that same verified connection. Project policy is checked
from the same configuration snapshot used to authorize that request. Clients
still cannot request `GET /info`; the identity probe is internal.

Receipt trust relies on the private mount, service ownership and daemon/project
binding. Host administrators remain trusted. Studio workspace backups and exports
must not contain this operational authority. Enabling persistence never adopts
containers created before it was enabled, and a crash before a receipt commits
can still require manual ownership review. No task or create mutation is replayed.

## Evidence and deployment

Framework policy lives in `nexus/core/studio_docker_proxy_policy.py`. The standalone
launcher loads the same stdlib-only module; Compose mounts it beside the launcher.
Update the launcher and framework policy together. Persistent deployments also
mount `studio_docker_proxy_state.py`; the optional
`docker-compose.studio-proxy-state.yml` provides the layout. No Studio state schema
or public Studio payload changes. See the deployment guide for setup and rollback.

Actual acceptance uses the real Docker CLI against a private Unix proxy socket
and the local daemon. It covers execution, stopped-container recovery, rejected
running-container deletion followed by explicit forced removal, same-name reuse,
and proxy restart with preserved uncertainty. Additional configured-ledger checks
cover graceful restart, SIGKILL recovery, restored-start denial and enabling
persistence without adoption. No substitute daemon, public TCP
port, account credential, image download or privileged container is involved.
Unit contracts additionally prove denial and canonical upstream targets.

See `docs/STUDIO-PROXY-RECOVERY-CHECKPOINT.json` and
`tests/studio_proxy_recovery_acceptance.py`. Docker documents the underlying
[container filters](https://docs.docker.com/reference/cli/docker/container/ls/).
The persistence follow-up is recorded separately in
`docs/STUDIO-PROXY-STATE-CHECKPOINT.json`. SQLite documents
[exclusive locking](https://www.sqlite.org/pragma.html#pragma_locking_mode).
Personal VM, remote/SSH daemon, daemon restart and installed OS acceptance retain
their separate scope.

## Linux private-mount acceptance

The local Linux follow-up runs the proxy and framework in separate
containers with the same uid/gid 1001. Only the proxy receives the private ledger
volume and real daemon socket. The controller retains a writable filesystem, so
its failed forgery is not merely a read-only-root check: its own file at the same
path cannot alter the proxy's separately mounted SQLite inventory. A private-volume
mount request through the Docker API is also rejected before worker creation.

Actual graceful/forced proxy restart recovers the original stopped container for
deletion, rejects start and leaves no extra run or artifact. The image's native
Linux storage/policy regression gate passes separately without any daemon socket.
See `docs/STUDIO-LINUX-PROXY-CHECKPOINT.json`. This named-volume proof does not
replace the personal VM's bind-path/UID/GID/authentication checks.

The gate also verifies the termination outcome. The former proxy ignored SIGTERM
as Linux PID 1 and required Docker's forced kill. Cooperative framework handling
now requests normal loop shutdown, closes the listener/ownership connection and
restores the prior signal policy. Normal stop exits 0; explicit SIGKILL exits 137
and retains committed recovery. No HTTP format or persisted receipt changes.
