# One personal VM

Use one Studio process, private filesystem state and one existing Nexus identity
bound as owner. A database server, cloud bucket and external event broker are
optional. The existing authenticated hosting mode works for a single owner; its
internal `team` name does not require multiple users.

1. Install this checkout on the VM and reuse your existing Nexus login service,
   auth database, encryption settings and project grant. Configure its normal
   `NEXUS_AUTH_ENABLED` and account setup. Studio does not create another identity
   store or collect subscription login tokens through its forms.
2. Copy `personal.example.json` to `/etc/nexus/studio-personal.json`, outside the
   project. Replace the HTTPS origin, project key and your existing Nexus ID.
   Keep a single owner binding. The explicit `memory` event transport is suitable
   for this one-process installation; run and checkpoint records stay on disk.
3. Create `/srv/nexus/project`, `/var/lib/nexus/studio-private` and
   `/var/lib/nexus/studio-tmp`, owned by the service account `nexus`, mode 0700.
   Put the same required auth/runtime environment as your existing Nexus service
   in `/etc/nexus/studio.env`, mode 0600, readable by the service. Prepared account
   directories must live under `/var/lib/nexus`, matching the existing account
   resolver configuration; the sample service hides ordinary home directories.
4. For a Linux/systemd VM, review `personal.service.example` and adapt its installation/account paths.
   It binds Studio to VM loopback. The separate temporary directory is visible to
   the Docker daemon for CLI scratch mounts despite systemd's private `/tmp`.
   Install/start the unit only on your chosen VM after these paths are configured.
   To run Studio in Docker instead, use `docker-compose.studio.yml` at the repository
   root: it builds `Dockerfile.studio`, binds Studio to loopback via host networking,
   and reads the same `studio.env`, team configuration and an optional SQL runtime
   configuration from a private configuration directory (see the file header).
5. Adapt `Caddyfile.example`: route `/login` and `/auth/*` to the existing Nexus
   login service, everything else to Studio. Use the real HTTPS hostname and a
   valid certificate. Keep both backend ports on loopback; expose only the HTTPS
   endpoint. For desktop-only access, an SSH tunnel can instead forward the
   ordinary loopback desktop server. Phone/PWA access uses the authenticated HTTPS
   address. Neither approach requires an unauthenticated network listener.

For Codex/Claude subscription execution in hosted mode, follow the pinned worker
image/account setup in [the shared deployment guide](README.md), then add its
reviewed `runner_image` digest to the personal configuration. The service needs
access to that dedicated worker runtime. Omitting it keeps hosted CLI execution
unavailable; configured API providers remain usable. Hosting with one owner does
not silently relax the existing CLI isolation policy. With the Docker deployment,
add `docker-compose.studio-workers.yml`: it gives the container a filtered Docker
socket through the `docker-proxy` service (see [the shared guide](README.md#docker-access-through-the-filtering-proxy))
and the per-user account homes at their host paths.

## Validate before relying on it

Use `deploy/studio/verify.py https://YOUR_HOST` with an existing Nexus session in
`NEXUS_STUDIO_CHECK_SESSION`. It checks TLS, login routing, anonymous private-route
denial, real installation assets and private cache policy. It never follows an
OAuth redirect or submits a task. A reachable login page is not proof that an
interactive OAuth sign-in completed. Then test your actual login/provider and
read-only connector access, and the phone flows described in `README.md`.

The local infrastructure suite is explicit, so normal tests do not unexpectedly
start Docker or quietly skip missing deployment dependencies:

```sh
python -m pytest -o addopts='' tests/studio_live_deployment_checks.py -q
```

Install the `postgres` and `mqtt` extras and cache `postgres:17-alpine` and
`eclipse-mosquitto:2` first. The suite uses generated container names, private
temporary test certificates and loopback-only random ports, and cleans up its own
containers. Image references can be pinned with `NEXUS_STUDIO_CHECK_POSTGRES_IMAGE`
and `NEXUS_STUDIO_CHECK_MQTT_IMAGE`. These checks cover a local broker and database;
they do not certify real cloud storage or production services.

## Back up the personal workspace

The framework now provides a checksummed stopped-workspace snapshot and isolated
inactive restore interface. See [Studio recovery](../../docs/STUDIO-RECOVERY.md)
for its commands, supported scope and separate provider/authentication backups.

Stop Studio and ensure no other process edits the project/state while taking a
consistent backup. Back up the entire project and private state together, including
artifact bytes, YAML, checkpoints and histories. Back up the existing Nexus auth
database, encryption key/configuration and prepared account directories through
the existing ARC deployment's backup procedure. Restrict and encrypt backups as
you do the source credentials; never place them in the shared project.

Restore a copy into separate directories with the same service ownership and
permissions, point an isolated loopback instance at it, and verify an artifact and
a paused workflow before replacing live data. Do not launch restored schedules,
messaging entrypoints or provider jobs against live accounts while checking a copy;
review those configurations first. The framework restore disables automatic
dispatch and public sharing in its review copy; manual full-filesystem restores
still require that review. These are stopped-workspace recovery procedures.
The local SQL check also
exercises `pg_dump`/`pg_restore` plus matching private files in a separate database
and proves the original paused run remains unchanged.

No VM, DNS record, firewall rule, account grant or system service is provisioned by
these example files. Current validation remains local until your host is supplied.


## Rehearse server recovery locally

The [isolated Linux gate](recovery-checks/README.md) now checks cooperative SIGTERM,
SIGKILL restart, single-writer ownership and inactive file/SQLite backup/restore.
It uses disposable loopback processes without host mounts or provider accounts.
The source fix needs a normal package upgrade and service restart on your chosen
host; this local drill does not deploy or certify that VM. See the
[scoped checkpoint](../../docs/STUDIO-SERVER-RECOVERY-CHECKPOINT.json).

## Check durable proxy storage on the host

For deployments using `docker-compose.studio-proxy-state.yml`, update the source
package and run this on the Docker daemon host with access to the selected
containers and their host mount paths. Replace names if your Compose project
uses different ones:

```sh
nexus studio-host-check \
  --studio-container nexus-studio-studio-1 \
  --proxy-container nexus-studio-docker-proxy-1 \
  --docker-context default
```

The command pins that Docker route, inspects only mount/state metadata and runs
a fixed read-only Python stat probe inside the explicitly selected proxy. It
never fetches environment/command/log data, reads ledger contents, creates
resources, fixes permissions or restarts services. Its JSON report omits names,
host paths and credentials, and hashes container identities. Exit 0 means every
scoped check passed; exit 1 means a failed or unavailable check; invalid arguments
use exit 2. `ledger: absent` describes an empty configured store, not successful
ownership recovery. Existing memory-only deployments do not need this command or
a migration; this check is for the opt-in durable layout.

Checks reject root proxy execution, unsafe directory/ledger modes, symlinks,
hard-linked ledger files, inaccessible/world-writable upstream sockets, shared
private storage and raw socket exposure. Nested mounts beneath private storage
fail too, since they could replace the ledger. Read-only Studio exposure still fails.
Canonical host paths plus device/inode ancestry detect source aliases and
hard-linked socket exposure. Host administrators remain trusted. Run on the
chosen daemon host: a remote client or Docker Desktop may not see its source
paths. Unresolvable source identity is explicitly `unavailable`, never inferred
from path strings. The check cannot prove host co-location automatically.

This is a point-in-time mount/permission preflight. It does not certify TLS/login,
network policy, running jobs, restart/upgrade/restore, certificate lifecycle or
resistance to later mount changes. See
[the host-check checkpoint](../../docs/STUDIO-HOST-CHECKS-CHECKPOINT.json).
