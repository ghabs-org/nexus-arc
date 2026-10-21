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
