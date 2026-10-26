# Isolated Linux server recovery checks

This explicit acceptance image runs the existing source runtime on Linux with
the same pinned Python 3.13 base as `Dockerfile.studio`. It is a test image,
not a replacement production image or a desktop installer. Building downloads
Python test dependencies; execution requires no external account or network.

From the repository root:

```sh
docker build -f deploy/studio/recovery-checks/Dockerfile -t nexus-studio-recovery:local .
docker run --rm --init --pull never --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 768m --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=384m \
  -e PYTEST_ADDOPTS=-pno:cacheprovider nexus-studio-recovery:local
```

No host folders, credentials, Docker socket, published ports or persistent
volumes are mounted. Tests run as uid/gid 65534. Disposable loopback listeners
are reachable only inside the test container. The build prepares the test
bootstrap configuration before switching to the non-root user. Store console
output outside the container if required; temporary files vanish on exit.

The gate checks file and SQLite state through four real server launches each:
normal SIGTERM, SIGKILL and restart, then an inactive restored copy. Every live
launch rejects a second writer. Definitions, paused approval, old/current
artifact bytes, pinned citation revisions and run identity survive. Separate
controller/worker drills instrument the real `StudioService.close` method to
verify it finishes even after a second SIGTERM during cleanup. Existing backup,
SQL restore, workspace lock and parent-lifetime regressions run in the same gate.

For source checks without Docker, install the package, pytest, pytest-asyncio,
SQLAlchemy and pypdf, then run the test paths listed in the Dockerfile. The thin
consumer also runs directly on POSIX:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_server_recovery.py
PYTHONPATH=. python examples/nexus-bot/src/studio_server_recovery.py --sql
```

No persisted format or migration changes are needed. Upgrade the installed
package and restart the chosen service normally to pick up cooperative SIGTERM.
Keep the service manager's stop timeout; unresponsive external dependencies and
SIGKILL cannot be turned into guaranteed graceful completion.

This local Linux/arm64 container proof does not certify a personal HTTPS VM,
systemd deployment, Nexus account login, PostgreSQL host, device or native app.
Those acceptance gates remain separate. See
[the checkpoint](../../../docs/STUDIO-SERVER-RECOVERY-CHECKPOINT.json).

## Private proxy mounts inside Linux containers

`Dockerfile.private-proxy` extends the existing check runtime with the same
pinned Docker CLI used by the production image. It copies current framework
source, standalone proxy helpers and a thin acceptance consumer. Build this test
image explicitly; tool execution never pulls worker images.

```sh
docker build -f deploy/studio/recovery-checks/Dockerfile.private-proxy \
  --build-arg CHECK_IMAGE=nexus-studio-recovery:local \
  -t nexus-private-proxy-checks:local .
export NEXUS_ACCEPTANCE_PROXY_IMAGE="$(docker image inspect nexus-private-proxy-checks:local --format '{{.Id}}')"
export NEXUS_ACCEPTANCE_WORKER_IMAGE=sha256:YOUR_INSTALLED_PYTHON_IMAGE_ID
export NEXUS_ACCEPTANCE_DOCKER_CONTEXT=default
python -m pytest -o addopts='' tests/studio_linux_proxy_acceptance.py -q
```

The explicit gate creates private, socket and Studio named volumes. Proxy and
controller both run as uid/gid 1001. Only the trusted proxy gets the private
ledger and real Docker socket; it has a read-only root, no capabilities and no
network. The controller has a writable root, ordinary Studio data and the filtered
socket. It cannot read the real ledger, and its fake ledger at the same path
remains isolated. A request to mount the known private volume through the proxy
is rejected. Graceful and forced proxy termination retain cleanup-only receipts;
old containers never start. Normal stop must exit 0; the forced case must exit
137. This caught the prior PID 1 SIGTERM bug, where Docker escalated normal stop
to SIGKILL. The proxy now handles SIGTERM cooperatively and closes its listener
and ledger. Test containers/volumes are removed afterward.

This local Docker Desktop Linux/arm64 fixture uses socket group 0. The personal
VM's real bind paths, mapped UID/GID and Docker socket group still need their own
deployment check. Named-volume isolation is not certification of a particular
host bind mount or a complete authenticated Studio deployment. No HTTPS endpoint,
account credentials, privileged container or desktop installer is involved.

This gate also runs the framework host preflight through its real CLI and thin
example consumer. The fixed in-proxy metadata probe verifies actual UID, private
storage modes, ledger link/type/ownership and upstream socket access. When run
from Docker Desktop, Linux named-volume host paths remain inaccessible: all
three source identity/isolation checks must report unavailable instead of a
guessed deployment pass. Host-filesystem alias/exposure rules have separate
fixtures. The explicit POSIX stat/socket cases run with:

```sh
python -m pytest -o addopts='' tests/studio_host_probe_acceptance.py -q
```

These use temporary Unix sockets and may require the test runner to permit
local socket binding. They are explicit rather than silently skipped on an
unsupported runtime. See [the host-check checkpoint](../../../docs/STUDIO-HOST-CHECKS-CHECKPOINT.json).

The same image can run the storage/policy checks without Docker access:

```sh
docker run --rm --pull never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  nexus-private-proxy-checks:local -m pytest -o addopts= -p no:cacheprovider -q \
  tests/test_studio_docker_proxy_state.py tests/test_studio_docker_proxy_policy.py
```

See [the Linux isolation checkpoint](../../../docs/STUDIO-LINUX-PROXY-CHECKPOINT.json)
for image/source hashes, scoped test results and remaining deployment gates.

## Real SSH disconnection through the filtering proxy

`Dockerfile.ssh-proxy` extends the private-proxy check image with OpenSSH. Build
it explicitly after building the current private-proxy image. This downloads OS
packages, not worker images, and does not build an application or installer.

```sh
docker build -f deploy/studio/recovery-checks/Dockerfile.ssh-proxy \
  --build-arg CHECK_IMAGE=nexus-private-proxy-checks:local \
  -t nexus-ssh-proxy-checks:local .
export NEXUS_ACCEPTANCE_SSH_IMAGE="$(docker image inspect nexus-ssh-proxy-checks:local --format '{{.Id}}')"
export NEXUS_ACCEPTANCE_WORKER_IMAGE=sha256:YOUR_INSTALLED_PYTHON_IMAGE_ID
export NEXUS_ACCEPTANCE_DOCKER_CONTEXT=default
python -m pytest -o addopts='' tests/studio_ssh_proxy_acceptance.py -q -s
```

The test creates an internal network with no published ports and separate
temporary client/server keys. It never reads the operator's SSH configuration.
The trusted proxy alone receives the raw Docker socket and private ledger.
The non-root SSH gateway receives only the filtered socket. Its key-only login
forces Docker `system dial-stdio`, disables forwarding/PTY/password login and
cannot open a shell. The controller receives only its private client identity
and Studio data, without either Docker socket or the proxy ledger. Strict host
key checking stays enabled. Every container drops capabilities and has bounded
resources; application and gateway roots are read-only.

The existing named-context snapshot path performs a healthy analysis, then an
acknowledged long-running analysis. Killing the SSH gateway severs the real
transport. Owner-stdin EOF stops the analysis before reconnection or explicit
cleanup. Studio reports failure and retains its acknowledged cleanup journal
while SSH is unavailable. Restarting the gateway with the same host key allows
explicit cleanup to confirm absence and retire the receipt. Proxy logs must show
exactly two creations and two starts: cleanup never replays generated code.
Temporary containers, key/data volumes and the internal network are removed.

This proves source runtime behavior through a local Linux/arm64 SSH connection.
It does not certify the personal VM, changed DNS/SSH aliases or credentials,
Docker daemon restart, host power-loss or installed desktop builds. Existing
deployment/authentication settings and persisted formats stay unchanged. See
[the SSH checkpoint](../../../docs/STUDIO-SSH-PROXY-CHECKPOINT.json).

## Real Docker mutual TLS

`Dockerfile.tls-proxy` extends the SSH fixture and reuses its isolation and
disconnect harness. It adds a test-only byte-forwarding TLS gateway, not a
second Docker implementation or a production listener. The Docker CLI and real
daemon remain unchanged. Build the current private-proxy and SSH images first:

```sh
docker build -f deploy/studio/recovery-checks/Dockerfile.tls-proxy \
  --build-arg CHECK_IMAGE=nexus-ssh-proxy-checks:local \
  -t nexus-tls-proxy-checks:local .
export NEXUS_ACCEPTANCE_TLS_IMAGE="$(docker image inspect nexus-tls-proxy-checks:local --format '{{.Id}}')"
export NEXUS_ACCEPTANCE_SSH_IMAGE="$NEXUS_ACCEPTANCE_TLS_IMAGE"
export NEXUS_ACCEPTANCE_WORKER_IMAGE=sha256:YOUR_INSTALLED_PYTHON_IMAGE_ID
export NEXUS_ACCEPTANCE_DOCKER_CONTEXT=default
python -m pytest -o addopts='' tests/studio_ssh_proxy_acceptance.py \
  tests/studio_tls_proxy_acceptance.py -q -s
```

An isolated initializer generates one-day client/server certificates and a
separate untrusted authority. CA private keys exist only in its temporary
filesystem. The non-root gateway mounts its server identity, trusted CA and the
filtered Docker socket. The controller mounts only its client identity and
Studio data. Both roots are read-only; no raw socket, private proxy ledger or
server key reaches the controller. No host ports, personal certificates, public
TLS endpoints or account services are used. Client/server volumes stay separate
and are removed with the test network and containers.

The real Docker client rejects an untrusted server and wrong hostname. The
gateway rejects absent and untrusted client certificates before forwarding
HTTP. The test requires precise server-side TLS rejection reasons because
Docker can expose TLS 1.3 rejection as EOF or connection reset. Healthy analysis
uses the existing private named-context snapshot, retaining verification and
client credentials. Gateway termination stops an acknowledged running analysis
before reconnect; explicit cleanup remains pending while unreachable and clears
after reconnect. Exactly two creates and two starts are permitted per transport.
SSH is rechecked through the same updated harness.

TLS semantics follow the
[Docker certificate-authentication documentation](https://docs.docker.com/engine/security/protect-access/).
No runtime profile, public API, receipt format or authentication setting changes.
This is local source-runtime TLS proof; actual VM provisioning, certificate
renewal/revocation, daemon restart/power-loss and native installed builds retain
separate gates. See [the TLS checkpoint](../../../docs/STUDIO-TLS-PROXY-CHECKPOINT.json).

## Linux MCP descendants outside the SDK process group

`Dockerfile.mcp-supervision` reuses the Linux check base and installs the fixed
MCP 2.3.0 SDK. It runs only controlled stdio fixtures, without host mounts, Docker
socket, credentials, ports or network. Build explicitly:

```sh
docker build -f deploy/studio/recovery-checks/Dockerfile.mcp-supervision \
  --build-arg CHECK_IMAGE=nexus-studio-recovery:local \
  -t nexus-mcp-supervision-checks:local .
docker run --rm --pull never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 128 --memory 768m --cpus 2 \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=256m \
  nexus-mcp-supervision-checks:local -p no:cacheprovider \
  tests/test_mcp_bridge.py tests/test_studio_mcp_lifecycle.py \
  tests/test_studio_mcp_supervision.py tests/test_studio_desktop_bundle.py \
  tests/studio_linux_mcp_acceptance.py -s
```

The test process stays alive after each Studio owner stops, so container exit
cannot manufacture successful process cleanup. Both one-shot and persistent
sessions must stop a double-forked SIGTERM-resistant daemon after ordinary close
and after owner SIGKILL. The kernel pidfd exit check also verifies that an unrelated
process stays alive. The thin example, already-closed owner and backpressured stdin
cases run in the same gate. Missing Linux kernel features fail explicitly.

This trusted test tmpfs uses `exec` because packaging unit fixtures check the
executable bit. Without it Linux `os.access(X_OK)` correctly fails even after
chmod. No production worker setting changes; analysis filesystems remain
restricted as before. No actual desktop executable, application or DMG is built.
Test reports written to tmpfs disappear when the container stops; capture console
output before exit when retaining evidence. See
[the Linux MCP checkpoint](../../../docs/STUDIO-LINUX-MCP-CHECKPOINT.json).
# Check an existing controller's worker boundary

`tests/studio_deployed_worker_acceptance.py` is an explicit live gate. Run it
inside the selected Studio controller, using its existing filtered Unix socket,
the already configured pinned `runner_image`, and the worker's non-root UID:GID.
It requires `/bin/sh` in that image. It never mounts account homes or workspace
files, invokes a provider CLI, pulls an image, changes settings or restarts a
service. Run it during a quiet period so its final resource checks are easy to
distinguish from user activity.

From the daemon host with this checkout available, replace each angle-bracketed
value before running:

```sh
docker --context <daemon-context> exec -i \
  --env NEXUS_ACCEPTANCE_WORKER_IMAGE='<configured-image>@sha256:<digest>' \
  --env NEXUS_ACCEPTANCE_FILTERED_SOCKET='unix:///var/lib/nexus-studio/docker-proxy/docker.sock' \
  --env NEXUS_ACCEPTANCE_WORKER_USER='<uid>:<gid>' \
  <studio-container> python - < tests/studio_deployed_worker_acceptance.py
```

The controller must not have the raw socket or the private proxy store. The
gate rejects unscoped listing before any mutation, then checks denied create
options, owned stopped inspection, one static shell invocation, unstarted-worker
cleanup and retained absence. Its successful run creates two disposable workers.
Cleanup targets only the gate's acknowledged IDs or its own uncertain create
names. A failed or interrupted check requires cleanup review before repeating it;
do not delete unrelated workers or treat lost acknowledgments as empty success.

Normal regression discovery excludes this prerequisite-dependent file. Missing
configuration or services fail the explicit gate rather than produce a skip.
Signed-in identity, actual provider inference, analysis-profile readiness and
proxy/daemon restart recovery remain separate checks. See
`docs/STUDIO-DEPLOYED-WORKER-CHECKPOINT.json` for the selected VM evidence.
