# Filtered Docker recovery contract

This is the deployment proxy's private Unix-socket Docker API, not a public
Studio route. Existing socket ownership, configured-image allowlists, create
hardening, mount checks and application permissions remain in force.

## Scoped lookup

`GET /containers/json` (also with the Docker API version prefix) accepts only
`all=1` and one `filters` value containing one exact anchored name:

```json
{"name":["^/nexus-analysis-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa$"]}
```

The name must belong to a container created through the current proxy process.
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

Proxy ownership is still process-local. After proxy restart or record eviction,
old containers require independent operator review. The proxy returns 403 and
Studio retains an unconfirmed cleanup journal; it does not treat lost authority
as proof of absence. This change adds no persistent ownership trust store.

## Evidence and deployment

Framework policy lives in `nexus/core/studio_docker_proxy_policy.py`. The standalone
launcher loads the same stdlib-only module; Compose mounts it beside the launcher.
Upgrade both files together. No state schema or public Studio payload changes.

Actual acceptance uses the real Docker CLI against a private Unix proxy socket
and the local daemon. It covers execution, stopped-container recovery, rejected
running-container deletion followed by explicit forced removal, same-name reuse,
and proxy restart with preserved uncertainty. No substitute daemon, public TCP
port, account credential, image download or privileged container is involved.
Unit contracts additionally prove denial and canonical upstream targets.

See `docs/STUDIO-PROXY-RECOVERY-CHECKPOINT.json` and
`tests/studio_proxy_recovery_acceptance.py`. Docker documents the underlying
[container filters](https://docs.docker.com/reference/cli/docker/container/ls/).
Personal VM, remote/SSH daemon, daemon restart and installed OS acceptance retain
their separate scope.
