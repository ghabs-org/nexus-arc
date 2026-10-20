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
add `docker-compose.studio-workers.yml` to give the container the Docker socket and
the per-user account homes at their host paths.

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
