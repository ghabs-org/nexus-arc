# Authenticated external-agent callback reception

Studio can request callback notifications when sending an external A2A task.
Enable **Receive callback updates** in **External agents → Run task**. The agent
must advertise `pushNotifications`; unsupported providers fail before task send.
Existing text/file/JSON tasks and continuation remain compatible. Continuations
retain the original task's callback registration; they do not create another grant.

The callback is a hint. Its task/context identity is checked, then the existing
scheduler reads the acknowledged task through the configured agent's `GetTask`
(`tasks/get` for 0.3). Callback-supplied output and artifacts are never imported.
Notification recovery cannot submit tasks, approve actions or establish an unknown
remote task ID. The active task monitor keeps ownership until it stops. Completed,
cancelled and remote-terminal tasks ignore later hints.

## Setup and access

Configure Nexus's existing `NEXUS_CREDENTIALS_MASTER_KEY` with a valid 32-byte key.
Credentials use existing Nexus encryption. Each initial task receives a random,
24-hour Bearer credential; it is sent in the A2A configuration and encrypted in
private `external-callbacks` state. It does not appear in run payloads, status APIs,
configuration exports or backup/restore. An account losing execution access, a
changed/disabled agent profile, expiry or revocation makes the grant unavailable.

For the personal HTTPS VM, Studio uses the configured Nexus public origin. Local
Studio uses its checked loopback Host and actual listener port. Forwarded headers
and caller-supplied `_callback_origin` do not choose a destination. A loopback
address only works with an agent able to reach that host. No tunnel, hosted
webhook or public exposure is created automatically.

Under **Remote tasks → Callback updates**, review status and expiry, refresh the
read-only view or revoke callback access. Revocation clears the local encrypted
credential and stops queued callback reads. It does not cancel a remote task or
promise removal of the remote provider's registration. Status and revocation are
owner-scoped even for Nexus team administrators; normal session/CSRF rules apply.

Framework consumers can supply a trusted local origin explicitly:

```python
run = studio.start_run({
    "kind": "external",
    "external_agent_id": "reviewer",
    "task": "Review this evidence",
    "external_callbacks": True,
}, callback_origin="http://127.0.0.1:5004")
```

A framework host may instead set `external_options={"callback_origin": origin}`
on `StudioService`. In team mode the configured Nexus public URL takes precedence.
`POST /api/runs` uses the server's canonical origin, with the same boolean opt-in.

## Receipt and recovery contract

- `POST /api/a2a-callbacks/GRANT` requires that grant's Bearer credential, JSON and
  a body at most 24 MiB. It accepts A2A 1.0 task/status/artifact/message envelopes
  and corresponding 0.3 task/status/artifact events with bounded task identity.
- A valid receipt is durably acknowledged with 202 before any network read.
  `X-Nexus-Delivery-ID` deduplicates deliveries; without it, the canonical JSON
  digest is the ID. Reusing an ID with different content is rejected. The last
  128 IDs are retained; an older replay may cause another harmless task read.
- Only identity, digest and receipt time are stored. The latest hint coalesces
  pending deliveries. A durable 60-second read claim is shared across SQL workers.
  A failed read retries after 5 and 10 seconds, at most three attempts per hint.
  Lost claims recover after their lease; there is no task resubmission.
- Early notifications wait for authoritative Send acknowledgement. A missing ID
  remains uncertain; an early notification cannot supply it. Mismatched early
  hints are ignored after acknowledgement. Unavailable or exhausted reads remain
  visible for manual inspection.
- Profile/access/claim/cancellation are checked again before publishing downloaded
  artifacts and results. Revocation during a read prevents publication. Existing
  reconnect logic continues to read saved state without replaying actions.
- `GET /api/studio/external-runs/RUN/callbacks` exposes safe status.
  `POST /api/studio/external-runs/RUN/callbacks/revoke` revokes locally.

## Validation and migration

The local independent HTTP peer covers early delivery, duplicate/conflicting IDs,
wrong credentials/task/context, bounded receipts/body size, current team ownership
and role changes, cancellation, expiry/profile changes, concurrent claims,
revocation during artifact download, file/SQLite restart, credential-free restore,
legacy event shapes and bounded read retries. Optional A2A SDK acceptance parses
both emitted configuration versions with independent protocol schemas.

`examples/nexus-bot/src/studio_a2a_receiver.py` consumes the real framework and
local HTTP receiver. Run against `postman/simulations/studio-a2a-receiver.sim.yaml`
on port 49652. The synthetic sender delivers before Send acknowledgement; final
output comes only from GetTask. No provider account, hosted capture or publication
is used. This establishes local contract behavior, not vendor/HTTPS VM acceptance.

Existing workspaces need no destructive upgrade. New private records are created
only for explicit opt-in; old tasks report callbacks off. Restored copies omit
grants and cannot receive notifications under the old credentials. Frontend shell
cache advances to v28; locally packaged assets still require no frontend build.

M06-05 remains partial pending independent remote-provider, personal HTTPS VM/TLS
and real-device validation. Broader Milestone 6 and full plan remain incomplete.
