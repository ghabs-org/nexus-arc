# Studio OpenClaw Integration

## Overview

Nexus Studio integrates with OpenClaw via `@nexus-arc/openclaw-plugin` (ADR-087 Path A). Human operators and authorized agents can discover permitted agents/workflows, launch durable runs, inspect progress and retrieve output artifacts. Human operators answer approval and input gates through authenticated commands; the agent tool cannot reply to gates.

## Architecture & Configuration

The OpenClaw plugin extends the existing `/nexus` command surface with a `studio` subcommand namespace and optionally provides the `nexus_studio` agent tool.

### OpenClaw Plugin Configuration

Add these fields to the existing OpenClaw plugin configuration:

```json
{
  "plugins": {
    "entries": {
      "nexus-arc": {
        "config": {
          "studioUrl": "http://127.0.0.1:5003",
          "studioEntrypoint": "chat-ops",
          "studioSecretEnv": "NEXUS_STUDIO_SECRET",
          "timeoutMs": 15000
        }
      }
    }
  }
}
```

- `studioUrl`: Studio base URL. Must use HTTPS or loopback HTTP (`localhost`, `127.0.0.1`, `[::1]`). No query strings, hashes, or inline credentials.
- `studioEntrypoint`: ID of the configured Studio entrypoint (catalog kind `entrypoints`, type `openclaw`).
- `studioSecretEnv`: Name of the environment variable containing the shared HMAC signing secret (minimum 32 characters).
- `timeoutMs`: Request timeout in milliseconds (1000–120000 ms, default 15000 ms).

Set the same signing secret in the Studio server and OpenClaw gateway environments.
Configure the saved agents' selected providers and accounts separately. The plugin
does not create provider credentials or change provider billing.

Agent tools require OpenClaw's version 2 tool-factory context with
`requesterSenderId`, `nativeChannelId`, `deliveryContext.channel` and
`assertInvocationCurrent`. The tool refuses to load without that trusted context.
Allowlist the optional `nexus_studio` tool using OpenClaw's `tools.allow` setting.
Local checks use Node 26 and a runtime API harness; an installed OpenClaw release
and live channels require their own compatibility check.

### Studio Entrypoint Configuration

Save an agent named Reviewer (`reviewer`) first. Then use this catalog entrypoint
payload. Replace the platform sender/channel IDs with the IDs supplied by the
gateway. In team mode, map the sender to an existing Nexus identity with current
project access and at least the operator role; `desktop` applies to local mode.

```yaml
id: chat-ops
name: OpenClaw Chat Ops
data:
  type: openclaw
  target_kind: agent
  target_id: reviewer
  enabled: true
  token_env: NEXUS_STUDIO_SECRET
  identity_map:
    "telegram:alice": desktop
  allowed_channels:
    - telegram:room-dev
  operator_targets:
    - agents:reviewer
  auto_reply: false
  approval_notifications: false
```

The default target is required by the shared channel entrypoint contract.
`operator_targets` separately grants access to the listed saved agents/workflows;
add `workflows:<saved-workflow-id>` only for workflows this caller should use.
No operator targets are granted by default.

For proactive notifications, first configure a saved, enabled OpenClaw connection
with the same fixed channel. Set `notification_connector_id` to that connection's
ID, then explicitly enable `auto_reply` and/or `approval_notifications`.

## Security & Protocol Specification

### Protocol Version & Wire Envelope

All communication between OpenClaw and Studio uses HTTP POST to `/api/openclaw/<entrypoint_id>`.

Headers:
- `Content-Type`: `application/json`
- `X-Nexus-Timestamp`: Current Unix timestamp in seconds (integer string).
- `X-Nexus-Signature`: `sha256=${HMAC_SHA256(secret, timestamp + "." + body)}`

Body Schema (Version 1):
```json
{
  "version": 1,
  "entrypoint_id": "chat-ops",
  "id": "<request_id>",
  "sender_id": "telegram:alice",
  "channel": "telegram:room-dev",
  "session_id": "<sha256_caller_session>",
  "operation": "<operation_name>",
  "arguments": {}
}
```

### Supported Operations

| Operation | Arguments | Description |
|-----------|-----------|-------------|
| `discover` | `{}` | Lists agents and workflows allowed by `operator_targets`, plus supported operation names. |
| `runs` | `{}` | Returns up to 50 recent runs for this mapped sender and channel, across threads. |
| `schedules` | `{}` | Lists caller-owned schedules for allowed targets, including their enabled flags; no scheduling writes. |
| `status` | `{run_id: string}` | Queries execution status, outputs, and waiting gates for a run. |
| `receipt` | `{request_id: string}` | Inspects submission outcome by original request ID. |
| `artifacts` | `{run_id: string}` | Lists artifacts produced by the specified run. |
| `artifact` | `{run_id: string, artifact_id: string, revision: integer}` | Returns original bytes as base64 for an exact run-produced revision, up to one MiB. Larger files use the authenticated Studio download route returned by `artifacts`. |
| `submit` | `{target: string, task: string}` | Launches a saved agent or workflow. Target syntax is `agents:<id>` or `workflows:<id>`. Idempotent by envelope `id`. |
| `cancel` | `{run_id: string}` | Cancels an active or waiting run. |
| `reply` | `{token: string, decision: "approve"\|"reject"}` or `{token: string, values: object}` | Continues the existing checkpoint. Input values are keyed by the requested input IDs. |

### Operator Slash Commands

Operators interact through the following command forms. API operation names and
slash command names differ:

- `/nexus studio agents`: List permitted saved agents.
- `/nexus studio workflows`: List permitted saved workflows.
- `/nexus studio runs`: View caller's runs.
- `/nexus studio schedules`: View scheduled recurring jobs.
- `/nexus studio status <run_id>`: Check run status and awaiting approval gates.
- `/nexus studio receipt <request_id>`: Check idempotent submission state.
- `/nexus studio artifacts <run_id>`: View run artifact summaries.
- `/nexus studio run agent <id> <task>`: Launch a saved agent.
- `/nexus studio run workflow <id> <task>`: Launch a saved workflow.
- `/nexus studio cancel <run_id>`: Cancel run.
- `/nexus studio reply <token> approve`: Approve the pending gate.
- `/nexus studio reply <token> reject`: Reject the pending gate.
- `/nexus studio reply <token> input <JSON input-ID map>`: Provide requested input values.

When a channel supplies no stable message ID, append `--request-id <unique-id>`
to `run`, `reply` or `cancel`. For example:

```text
/nexus studio run agent reviewer Review the release notes --request-id release-review-1
/nexus studio reply <token> input {"question":"Evidence"} --request-id release-input-1
```

Use a new ID for new work. After a timeout, inspect `receipt <original-id>` and
retain the original ID when recovering that exact request. Never create a new ID
to retry an uncertain mutation. The `artifact` API is available to the optional
agent tool; human commands return artifact metadata and authenticated download links.

### Invariants & Safety Fences

1. **HMAC Signature & Freshness**: Studio rejects requests with timestamp drift exceeding 300 seconds or invalid HMAC signatures. Identical signed mutation deliveries within that window return their existing receipts; changing arguments under the same ID is rejected.
2. **Deterministic Caller Identity**: Channel, sender, and thread identifiers are strictly mapped and hashed from authenticated OpenClaw session context.
3. **Agent Tool Authorization**: The `nexus_studio` agent tool never exposes `reply_token`. Only human operators can reply to approval gates via slash command.
4. **Idempotent Mutations & Uncertainty**: Mutations (`submit`, `reply`, `cancel`) require a unique request ID. If an HTTP transport error or timeout occurs, the client refuses automatic retry and directs the operator to `/nexus studio receipt <request_id>`.
5. **Payload Limits & Secret Redaction**: Upstream response bodies exceeding 2 MiB are rejected. Raw upstream error HTML or stack traces are redacted to prevent secret leakage.
6. **No Redirects**: OpenClaw refuses HTTP redirects without forwarding credentials.

File-backed mutations persist a dispatch claim before enqueueing; an ambiguous
claim stays uncertain. SQL mode commits the receipt and queued job atomically,
so a failed dispatch transaction rolls back before any worker sees it. Approval
tokens bind the sender, channel, entrypoint, Nexus identity, run and pending gates;
they expire after 24 hours and cannot be consumed again under a new request ID.
OpenClaw owns conversation context and wake-ups; Studio owns run execution,
checkpoints, artifacts and its own configured schedules.

## Verification & Rollback

### Acceptance Suite

From the repository root, build the plugin before running either HTTP consumer:

```sh
npm --prefix packages/nexus-arc ci --ignore-scripts
npm --prefix packages/nexus-arc run build
npm --prefix packages/nexus-arc test
python -m pytest tests/test_studio_openclaw.py tests/test_studio_messaging.py -o addopts="" -q
python tests/studio_openclaw_http_acceptance.py
PYTHONPATH=. python examples/nexus-bot/src/studio_openclaw.py
```

The fault runner requires Postman CLI 1.70.0 or later with working local dataset
support. A startup failure is reported as a failure, with its original diagnostic;
it never becomes a passing transport check. Local provider fixtures and the
OpenClaw runtime harness do not establish live gateway/channel compatibility.

If Postman's dataset engine cannot start, the same saved HTTP fixture can be run
explicitly through Node:

```sh
python tests/studio_openclaw_http_acceptance.py --transport node
```

This runs the actual compiled plugin client over HTTP, checks all eight fixture
requests and a refused connection, and verifies that redirects and mutations are
not retried. It reports `transport: node`; it does not certify Postman simulation
support. There is no automatic fallback from a failed Postman run.

See the [wire contract](../postman/contracts/studio-openclaw.md). Live Telegram/Discord
gateway and personal HTTPS VM drills remain separate pending checks.

### Rollback
To revoke Studio operator access, clear the entrypoint's `operator_targets` or
disable the entrypoint. Remove `nexus_studio` from the gateway's tool allowlist and
remove the three Studio plugin configuration fields. Retain receipt and binding
records for review and recovery; do not delete them to retry uncertain work.
Existing ARC bridge commands (`/nexus`, `/chat`, `/chatagents`) remain independent.
