# Shared Studio deployment

For a single personal VM, start with [the one-owner setup](PERSONAL-VM.md).

This mode reuses the existing Nexus auth database, `AuthManager` sessions,
project grants and `build_execution_env` credential resolution. It adds explicit
project role bindings; it does not create another login database or OAuth client.

1. Run the existing Nexus login service with its normal database, Git OAuth,
   `PROJECT_CONFIG_PATH`, `NEXUS_AUTH_ENABLED` and provider setup. Studio must use
   the same auth database, encryption settings and private runtime directories.
   Complete each user's Git/provider setup through that service.
2. Copy `team.example.json` **outside the shared project**. Replace origin,
   login URL, project key, existing Nexus IDs, broker and worker image. Keep this
   administrator-owned file and the auth environment inaccessible to builders.
   `viewer` reads shared definitions and their own records; `operator` also runs
   tasks and approvals; `builder` also edits definitions/resources; `owner` also
   manages connectors/entrypoints and can inspect all private records.
3. For subscription execution, build a worker with exact versions of the
   official CLIs and a reviewed Node image digest:

   ```sh
   docker build -f deploy/studio/Dockerfile.worker \
     --build-arg NODE_IMAGE=node:22-bookworm-slim@sha256:YOUR_BASE_DIGEST \
     --build-arg CODEX_VERSION=YOUR_TESTED_CODEX_VERSION \
     --build-arg CLAUDE_CODE_VERSION=YOUR_TESTED_CLAUDE_VERSION \
     -t YOUR_REGISTRY/nexus-studio-worker:YOUR_VERSION .
   ```

   To include Google Antigravity (`agy`), also pass `AGY_URL` and `AGY_SHA512`
   from the official release manifest for the worker architecture, e.g.
   `https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_arm64.json`
   (the `url` and `sha512` fields; the URL is version-specific). Users connect
   it with **Connect Antigravity Account** in Nexus account setup; set
   `AGY_CLI_PATH` for that login service to the same pinned version.

   To include OpenCode (`opencode`), also pass `OPENCODE_URL` and `OPENCODE_SHA256`
   for the versioned Linux archive, e.g.
   `https://opencode.ai/files/bin/2.1.2/opencode-linux-arm64.tar.gz` (the
   checksum is in the `anomalyco/homebrew-tap` `opencode-v2` formula). OpenCode
   runs only with the user's own **OpenCode API key** from Nexus account setup:
   the key is passed as `OPENCODE_API_KEY`, and OpenCode state lives on the
   run's tmpfs. Its free tier refuses requests that do not come from the
   OpenCode app.

   Publish through your normal image process, obtain its repository digest and
   put that exact `name@sha256:...` in `runner_image`. Pull it on the worker host
   before use; runs use `--pull never`. Validate the required CLI flags against
   these selected versions. See [official Codex installation](https://github.com/openai/codex)
   and [official Claude Code installation](https://code.claude.com/docs/en/setup).
   API-only installations can omit `runner_image`.
4. Install the broker extra (`nexus-arc[mqtt]` or `nexus-arc[kafka]`) along with
   the extras already used by your auth service. Supply broker secrets through
   environment references. MQTT requires verified TLS; Kafka requires SSL or
   SASL_SSL. Use broker ACLs for the project-specific status topic shown in
   Workspace. `memory` provides local in-process events for a single-process installation.
5. Create a private state directory owned by the service account (mode 0700),
   outside and separate from the project and access config. File-backed mode requires **one Studio process per state directory**. For multiple controllers/workers, use SQL state as described below.

   ```sh
   nexus studio --dir /srv/nexus/project --port 5003 \
     --team-config /etc/nexus/studio-team.json \
     --state-dir /var/lib/nexus/studio-private
   ```

6. Route `/login` and `/auth/*` to the existing auth server, all other routes to
   Studio, under the same HTTPS origin. `Caddyfile.example` shows that routing.
   Configure the existing OAuth callback/public URL for this origin. Preserve
   `Host`, cookies and Origin; do not publish the HTTP backend ports. Existing
   Nexus sessions supply HttpOnly cookies. Studio verifies session expiry,
   current project grant, explicit role and per-session CSRF on mutations.

Shared CLI workers mount only that user's prepared homes and an empty read-only
scratch workspace. No project, private Studio state or host-wide credential
folder is mounted. Codex ignores user config/rules, uses read-only sandboxing and
ephemeral sessions; Claude disables tools and external MCP settings. These shared
runs operate on submitted prompt/resource content. They do not edit the server
project. The desktop runtime retains its ordinary local coding-agent behavior.
AI and Git calls have no server-account credential fallback. API runs receive the user's resolved
key explicitly. Owners can separately configure a fixed-channel messaging service bot;
its verified incoming events still launch models under the mapped Nexus user. User homes may be updated by the official CLIs for login refresh.
The service account needs Docker access; keep it limited to this dedicated worker
host and restrict outbound network through your infrastructure policy if needed.

Schedules and bearer-authenticated entrypoints execute as their creator and
recheck current project access/role. Revoked or incomplete provider setup fails
closed. Private conversations, builds, evaluations, outputs and artifacts are
filtered by owner; project definitions and other catalogs are shared. Owners can
inspect private records. External broker messages contain execution metadata,
not prompts, output or credentials. Broker traffic is event transport. Distributed job execution uses the separate transactional SQL queue described below. Full external enterprise equivalence is not claimed.

The ordinary regression checks use a fake existing-auth facade, local HTTP/HTTPS servers and mocked
Docker/broker clients. The explicit local-infrastructure suite additionally exercises
a real authenticated TLS MQTT broker and PostgreSQL backup/restore. No live production auth database, public
HTTPS endpoint or external broker was provisioned. Durable-state checks additionally
use an isolated temporary PostgreSQL container; it is unrelated to production data.

## Transactional state and separate workers

Install `nexus-arc[postgres]`. Copy `runtime.example.json` outside the project,
set mode **0600**, replace the namespace/mappings and supply the database URL
through its referenced environment variable. Keep role bindings, account settings
and runtime configuration identical across controllers and workers. Provision the
PostgreSQL database/user through your normal infrastructure process; Studio creates
its own `nexus_studio_records`, `nexus_studio_jobs` and `nexus_studio_locks` tables.
Use a dedicated database account and TLS settings in the URL.

```sh
nexus studio --dir /srv/nexus/project --port 5003 \
  --team-config /etc/nexus/studio-team.json \
  --runtime-config /etc/nexus/studio-runtime.json \
  --state-dir /var/lib/nexus/studio-private
```

`state.workers: 0` creates a controller that queues but does not consume jobs.
A runtime configuration with a positive worker count can run without an HTTP port:

```sh
nexus studio --worker-only --dir /srv/nexus/project \
  --team-config /etc/nexus/studio-team.json \
  --runtime-config /etc/nexus/studio-worker-runtime.json \
  --state-dir /var/lib/nexus/studio-private
```

State records, jobs, YAML runtime, approvals and checkpoints persist in SQL. All
replicas sharing a namespace serialize project mutations. Workers claim jobs
exclusively, renew 60-second leases and fence stale writes. Expired jobs become
interrupted, rather than replaying remote mutations. Review external actions and
checkpoint state before resuming. Interrupted campaign/remote-agent attempts are
marked uncertain. This is recovery with explicit effect review, not an exactly-once
promise for third-party APIs.

Mount the **same project definitions and private byte storage** on every worker:
YAML, audio uploads, file audit logs and filesystem artifacts are shared-volume
requirements. S3/GCS/Azure can replace artifact bytes, but do not replace YAML or
audio storage. Auth DB/encryption/account directories must also remain consistent.
Do not mount private state into per-user coding CLI containers. Back up database,
YAML and object/byte stores together; configure retention externally. SQL activation
does not silently import old file-backed state. Export/archive the existing desktop
workspace before an explicitly planned deployment/migration.

`memory_sources` maps administrator-owned Redis or filesystem source environment
references to each user's actual legacy numeric identity. It never accepts a Redis
URL/key or server file path from the browser.

Optional runtime keys:

```json
{
  "router": {"url": "https://router.example.com", "token_env": "NEXUS_ROUTER_TOKEN"},
  "loki": {"url": "https://logs.example.com", "token_env": "NEXUS_LOKI_TOKEN", "namespace": "project-one"}
}
```

Merge those keys into the runtime file; they are not a replacement for its state
settings. Model-router tokens are resolved per user in shared mode, and routing
only overrides unpinned specialist models. HTTP service URLs require an explicit
`allow_private: true` setting. Redirects are refused. Loki uses an administrator
service token and a fixed namespace selector; Studio applies user visibility again
to returned events. Each environment reference must exist on the relevant worker.

Collect metadata-only `audit-events/*.jsonl` through your log collector. The
`alloy.example` file demonstrates labels matching Studio's Loki query; replace
paths/namespace and configure your service's authentication/TLS. Deploy one
collector per shared audit path to avoid duplicate ingestion. SQL analytics can
query its audit rows without Loki. The collector/service itself has not been
provisioned or integration-tested against a live Loki deployment here.

Register Telegram/Slack/Discord/OpenClaw event URLs under your public HTTPS origin.
The proxy must preserve raw bodies and platform signature headers. Channel events
have their own signature verification; ordinary browser mutations retain Nexus
login, Origin and per-session CSRF checks. Do not add a proxy-wide exemption that
bypasses Studio's own route checks. Bot/webhook registrations and OAuth app scopes
remain explicit account setup actions, never a side effect of saving a profile.


## Verify the deployed origin

The production server image includes `studio-oauth` explicitly. MCP SDK 2 uses
`httpx2`; that package does not provide the separate `httpx` imported by delegated
OAuth. A reachable Studio page or installed MCP SDK alone therefore cannot prove
OAuth transport prerequisites. Enabling a real provider still requires explicit
account setup and consent.

Open **Workspace tools → Deployment checks** as an owner to inspect configuration.
Then set `NEXUS_STUDIO_CHECK_SESSION` to an existing test user's Nexus session in
your shell and run:

```sh
python deploy/studio/verify.py https://studio.example.com
```

Use `--ca-file /path/to/private-ca.pem` only for your organization's trusted CA.
The verifier never disables certificate/hostname verification, follows redirects,
launches agents or writes to accounts. Its report omits session tokens. A missing
or invalid session fails the identity check. An isolated TLS server with a test
identity facade and separate-process durable workers are covered automatically;
actual production login, broker failover, cloud storage and backup restore still
need validation against your deployment.

On a physical iPhone and Android phone, verify installation, login expiry,
keyboard opening, menu focus, approval review, connection loss during a long task,
app reopening and recovery through Activity. Confirm the task starts only once.
The installable app caches public interface files only and queues no offline
mutations. Keep login routes outside any proxy/static offline cache. Bump the
service worker's shell cache version when releasing changed interface assets.

For incoming A2A, expose only the chosen bearer entrypoint. Both card and RPC
require its token; browser login is not a substitute for that scoped token.
The existing HTTPS proxy can route `/api/a2a/*` to Studio. Preserve authorization,
A2A version headers and unbuffered SSE responses. Do not remove Studio's host or
Origin checks. Outgoing MCP pools live on each worker independently and do not
make stdio servers available over a network.

## Isolated analysis and vision workers

See [the data-worker guide](../../docs/STUDIO-DATA-WORKERS.md) for explicit image
builds, bounded chart profiles, selected Docker contexts and optional CPU vision
with installed read-only model weights. These workers never run while browsing
the catalog and do not inherit Studio credentials.

## Docker access through the filtering proxy

After enabling the opt-in private ownership store, use `nexus studio-host-check`
on the daemon host to inspect the selected Studio/proxy mount layout and private
store permissions without reading secrets or changing services. The command
requires explicit container/context names and reports inaccessible host sources
as unavailable. See [the host preflight procedure](PERSONAL-VM.md#check-durable-proxy-storage-on-the-host).

`docker-compose.studio-workers.yml` never mounts the Docker socket into Studio.
Its `docker-proxy` service holds the socket and serves a filtered one at
`/var/lib/nexus-studio/docker-proxy/docker.sock` (mode 0600, Studio's UID), which
Studio reaches through `DOCKER_HOST`. `docker_proxy.py` allows only what the CLI
worker and analysis paths send: ping/version, inspect of the configured
`runner_image` and `analysis_workers` images, and create/attach/start/wait/kill/
delete for `nexus-studio-<hex>` and `nexus-analysis-<hex>` containers it created.
Creates must keep the hardening those paths request: the configured non-root user,
read-only root, `CapDrop=ALL`, `no-new-privileges`, `--rm`, memory/CPU/pids limits
and the expected network. Bind mounts may come only from per-user homes under
`${NEXUS_RUNTIME_DIR}/auth`, read-only prompt/attachment scratch dirs in Studio's
TMPDIR, or configured model files. Any HostConfig field it does not check must be
empty. Exact cleanup lookups are allowed only for one analysis name already
tracked by the proxy, with its full ID added to the upstream filter. Everything
else (unscoped `ps`, `exec`, pulls, builds, privileged or host-namespace
containers, other images or mounts) gets 403 and a `deny` line in
`docker logs nexus-studio-docker-proxy-1`.

Mount sources must be real paths without symlinks, checked at create and again at
start. Studio can write to those directories, so a compromised Studio could still
try to swap a checked directory for a symlink in the moment between that check
and the daemon's mount. Even then the container runs as Studio's UID with no
capabilities, so the swap gives the access of Studio's UID, not root. Close it fully with
rootless Docker or root-owned parents for the credential homes. Analysis model
files must also be mounted into the proxy at their host path, or those creates are
refused.

Recovery uses the shared stdlib-only framework policy in
`nexus/core/studio_docker_proxy_policy.py`. Compose mounts that file beside
`docker_proxy.py`; standalone deployments must update both files together.
Authorized name/short-ID operations are forwarded using their full tracked ID,
so a reused container name cannot inherit authority. Removal failures and lost
replies retain bounded ownership for later ID-scoped absence checks. Retired
entries are evicted before live entries under the existing 512-record limit.

Ownership remains process-local unless the private ledger below is configured.
Without it, a proxy restart or record eviction denies old ownership and Studio
keeps cleanup unconfirmed. Review those containers independently; restart does not
prove they disappeared. This is separate from restarting Studio while the proxy
remains alive.

The repeatable [proxy recovery example](../../examples/nexus-bot/src/studio_proxy_recovery.py)
uses disposable local containers, a private Unix socket and an installed image:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_proxy_recovery.py --image sha256:INSTALLED_IMAGE_ID --context default --mode recovery
```

Additional modes are `failed-remove`, `alias` and `restart`. The explicit
`tests/studio_proxy_recovery_acceptance.py` gate requires the same image/context
environment variables as other Docker acceptance checks. It never pulls an
image or modifies existing Docker contexts. See
[the checkpoint](../../docs/STUDIO-PROXY-RECOVERY-CHECKPOINT.json); actual personal
VM, remote daemon and installed-platform verification remain separate.

### Private ownership across proxy restarts

The optional `docker-compose.studio-proxy-state.yml` retains trusted cleanup
receipts across proxy restart, including forced termination. It reuses the
existing Nexus `project_key`, binds receipts to the Docker daemon identity and
restores only lookup/inspect/delete authority. It cannot start old work.

Precreate a canonical, service-owned mode-0700 directory such as
`/var/lib/nexus-studio-proxy`. Keep it **outside every writable Studio mount**,
including `NEXUS_STUDIO_DATA_DIR` and `NEXUS_RUNTIME_DIR/auth`. Never mount this
directory into Studio, an agent container or an analysis worker. The proxy's
private mount and service ownership protect these receipts; host administrators
remain trusted. This is operational authority, not ordinary workspace content.

After preparing that directory with the same mapped service UID/GID:

```sh
export NEXUS_STUDIO_PROXY_STATE_DIR=/var/lib/nexus-studio-proxy
docker compose --env-file compose.env \
  -f docker-compose.studio.yml -f docker-compose.studio-workers.yml \
  -f docker-compose.studio-proxy-state.yml up -d
```

The override refuses to create a missing host directory. It mounts the private
directory only into the proxy and adds the stdlib-only
`studio_docker_proxy_state.py` helper. Existing deployments without this override
retain their memory-only behavior. Update matching launcher/policy/state files
together; no image dependency or workspace schema migration is needed.

SQLite commits receipts before returning create acknowledgments and retains one
exclusive writer lock. The file uses mode 0600; permissions, links, receipt format,
daemon and project mismatches fail closed before the proxy replaces its listener.
The proxy also verifies identity on the same upstream connection used for each
forwarded request. Corrupt storage is never treated as a successful empty list.

A restored receipt permits lookup, inspection and deletion, with current project
scope checked again. Start/attach and other execution actions stay blocked. Failed
storage blocks new creates/starts while preserving known-ID cleanup. Already
unconfirmed creates without a committed receipt, evicted records and containers
created before enabling persistence still require independent review; there is
no adoption by name and no automatic task retry.

Back up this directory separately with its private permissions while the proxy is
stopped. Restore only to the same daemon/project; a mismatch refuses startup.
For rollback, stop proxy-backed work and remove the optional override, retaining
the ledger unchanged. Memory-only rollback cannot recover those receipts; work
created during rollback is not adopted when persistence is enabled again.
Do not delete or reset the ledger to bypass a binding error.

Repeatable local proof uses the existing installed test image:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_proxy_recovery.py --image sha256:INSTALLED_IMAGE_ID --context default --mode crash --durable
```

The explicit acceptance suite also checks the memory-only compatibility path and
enabling a fresh ledger without adopting older containers. See
[the persistence checkpoint](../../docs/STUDIO-PROXY-STATE-CHECKPOINT.json).
Actual VM mount isolation, daemon restart, remote/SSH and native OS deployment
retain separate acceptance gates.

Local Linux container isolation and normal/forced proxy termination now have
separate evidence in [the Linux checkpoint](../../docs/STUDIO-LINUX-PROXY-CHECKPOINT.json).
The normal-stop drill reproduced ignored SIGTERM at Linux PID 1; the shared
framework now handles it cooperatively, closes ownership normally and exits 0.
Update the launcher and policy together before restarting the chosen deployment.
This source fix was not deployed to your VM or built into an installer.
# Hosted data/media analysis profiles

The existing analysis worker image supports Python, CSV/JSON-to-SQLite, read-only
SQL, JQ, Plotly chart export and ImageMagick. Install a reviewed immutable image
on the selected daemon before enabling these profiles. A missing image is an
execution error; Studio never pulls it or switches to host execution.

Merge this fragment into the private runtime file; preserve existing state,
workspace-folder, speech and other settings. Replace each image placeholder with
the same installed immutable ID or reviewed image digest:

```json
{
  "analysis_workers": {
    "data": {
      "image": "sha256:INSTALLED_IMAGE_ID",
      "timeout": 60,
      "memory_mb": 768,
      "pids_limit": 256,
      "open_files": 256
    },
    "python": {
      "image": "sha256:INSTALLED_IMAGE_ID",
      "timeout": 60,
      "memory_mb": 768,
      "pids_limit": 256,
      "open_files": 256
    },
    "media": {
      "image": "sha256:INSTALLED_IMAGE_ID",
      "timeout": 60,
      "memory_mb": 768,
      "pids_limit": 256,
      "open_files": 256
    }
  }
}
```

Hosted profiles inherit the controller's filtered `DOCKER_HOST`. Do not select
the `default` Docker context inside these profiles: explicit context selection
overrides that host and can point at an unavailable raw socket. Local desktop
profiles may explicitly select their intended installed context.

Workers retain the framework's networkless, non-root, read-only sandbox with
bounded temporary storage and explicitly selected artifact inputs. These limits
do not grant host mounts or credentials. The three profiles enable six built-in
data/media actions; object detection still requires a separately selected vision
image and installed model. API image-generation and speech profiles are separate.

Stop the selected Studio writers, verify a private backup and stage the runtime
change with existing validators before activation. Preserve mode 0600. If the
runtime file is bind-mounted individually, atomic replacement leaves existing
mounts attached to the old inode: recreate its consumers, or stop both consumers
and preserve the inode during a synchronized write with an exact rollback copy.
Do not restart unrelated Nexus services or overwrite live SQL during rollback.

The live VM gate and compatibility limits are recorded in
[the analysis checkpoint](../../docs/STUDIO-VM-ANALYSIS-CHECKPOINT.json).
The thin `examples/nexus-bot/src/studio_recovery_identity.py` consumer checks host
identity across independent installed Python processes without exposing its value:

```sh
docker exec -i <studio-container> python - < examples/nexus-bot/src/studio_recovery_identity.py
```

On slim Linux images, the validated boot-ID fallback is stable within a host
boot. Changed boot/hostname and legacy random-node receipts require explicit
review. This does not certify host reboot/power-loss recovery or authenticated
tool execution. No installer build is needed to configure hosted workers.
