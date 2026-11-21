# Host bridge / Docker Telegram feedback recovery

The single-host Compose deployment keeps the command bridge on systemd. The
bridge writes `router_feedback_tokens.json` under `/var/lib/nexus/state`; the
Telegram consumer must see the same directory. Mounting `/home/ubuntu` alone
does not share this state. If `DATA_DIR` is customized, change both bind paths
to the actual state directory before deployment. The bind deliberately refuses
to create an empty source directory.

Exactly one Telegram long-poller may run for a bot token. Running both
`nexus-telegram.service` and the Compose `telegram` container produces Telegram
`Conflict: terminated by other getUpdates request` errors and nondeterministic
callback delivery. Do not solve token misses by selecting the newest decision
or accepting a different user's token.
Disabling a systemd unit does not stop an already-running process; check its
active state as well as enablement.

## Read-only diagnosis

- Inspect selected container mounts and the imported core module path, not the
  complete Docker inspect/environment output (which may contain credentials).
- Compare the token-store existence, counts and modification times on the host
  and inside the container. Do not call `resolve_feedback_token` on production
  just to inspect: it refreshes the token TTL and writes the file.
- Check both Docker logs and `journalctl -u nexus-telegram.service` for polling
  conflicts. Check `systemctl is-active` and `systemctl is-enabled` separately.
- Open the router SQLite database with `mode=ro`; confirm token decision IDs
  exist in `routing_decisions`. The router uses SQLite, not host PostgreSQL.

## Authorized immediate recovery: keep systemd as sole poller

If the existing host bot and bridge already share the correct state and the
host bot is running, the smallest reversible operational repair is:

```sh
docker stop nexus-host-telegram-1
systemctl is-active nexus-telegram.service nexus-bridge.service
```

This does not restart either host service or touch database services. Expect
polling to recover after the current retry/backoff, commonly within about
35 seconds. Docker's `unless-stopped` policy respects a manual stop. Do not
start the container again while the host bot is polling. The missing-token UI
fix is not required to recover valid existing cards in this mode.
If the host unit is disabled, this is only a current-boot recovery; explicitly
choose and authorize its enablement before adopting systemd permanently.

## Authorized durable Docker deployment

1. Record the current image ID, Compose file, host unit active/enabled states,
   current mounts, and source hashes. Save private rollback copies of the two
   changed runtime files before editing them. Preserve unrelated local edits.
2. Apply only the candidate `docker-compose.host.yml` bind and
   `src/telegram_bot.py` missing-token condition. The host layout loads the
   example module from `/home/ubuntu`, but core comes from image site-packages.
   This patch changes no core module and requires no image build. A running
   Python process will not reload the example module merely because it changed.
3. Confirm `/var/lib/nexus/state` already exists and is accessible by the
   configured container UID/GID; verify the host bridge's effective state path.
   Render only the Telegram mount configuration, never an unfiltered rendered
   Compose environment.
4. Stop and disable the duplicate host poller, then recreate **only** Telegram:

```sh
bot_image=$(docker inspect --format '{{.Image}}' nexus-host-telegram-1)
docker image inspect "$bot_image" --format '{{.Id}}'
sudo systemctl disable --now nexus-telegram.service
NEXUS_BOT_IMAGE="$bot_image" docker compose \
  -f /home/ubuntu/git/ghabs/nexus-arc/examples/nexus-bot/docker-compose.host.yml \
  --env-file /home/ubuntu/git/ghabs/nexus/.env.db \
  up -d --no-deps --force-recreate --pull never telegram
```

Do not run a whole-stack `up`, `down`, build, PostgreSQL start, or database
migration. Expected interruption: Telegram polling only, usually a few seconds
plus any existing retry/backoff. Bridge and database services stay running.
Verify no external supervisor is configured to start the host poller again.
Pinning the currently running image is important: the `nexus-bot:local` tag may
already point to a newer, unverified image. `--pull never` alone does not pin it.

## Acceptance: actual persisted user feedback

Offline tests do not prove live Telegram success. After an authorized repair:

1. Observe a genuine routed turn and its real feedback card. Have its owner
   click Correct, or complete Wrong/task/model. Do not send synthetic production
   feedback or call Telegram APIs from a verification script.
2. Confirm no polling conflict, missing token or router error for that click.
3. Read SQLite using a read-only connection; find the matching row by exact
   decision ID and user/message correlation. Verify the submitted verdict,
   corrected task/model verdict, source fields and a post-click `created_at`.
   `route_feedback.id` is the API's `feedback_id`. A successful HTTP response or
   updated card alone is insufficient. Example parameterized read:

```sql
SELECT id, created_at, decision_id, verdict, corrected_task, model_verdict,
       source_surface, source_channel, source_message_id, source_user_id
FROM route_feedback
WHERE decision_id = ? AND source_user_id = ?
ORDER BY created_at DESC;
```

## Rollback without reintroducing two pollers

Stop the Docker Telegram container first. Restore the saved Compose/example
files if needed, preserving any newer unrelated edits. Start the existing
`nexus-telegram.service` and restore its recorded enablement state; keep the
container stopped. The host bot can use the unchanged shared state. Do not
delete state files or roll back the router database. If returning to Docker
later, stop the host poller before starting the container.
