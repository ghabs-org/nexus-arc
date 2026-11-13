# Rich A2A results and authenticated callbacks

The shared JSON-RPC, HTTP+JSON and optional generated gRPC task service now
returns typed results and explicitly published file artifacts. Existing task IDs,
plain `output`, bearer authentication and JSON-RPC 0.3 remain supported.

## Rich results

Workflow v2 final values now persist bounded `structured_content` beside plain
output. Providers can return `metadata.structured_content` parts of type `text`,
`markdown`, `json` or `artifact`. File parts must explicitly name `artifact_id`
and a positive `revision`. Input attachments are never implicitly published;
arbitrary JSON keys, paths and URLs are never interpreted as file instructions.

The gateway maps JSON to `data` and pinned files to inline `raw`, `filename` and
sniffed `mediaType`. Legacy 0.3 uses `kind` and inline `file.bytes`; non-object
JSON values are wrapped in `{ "value": ... }` for legacy DataPart. Both current
and pinned artifact ownership must match the entrypoint creator. Missing,
reassigned or oversized revisions fail with `INVALID_AGENT_RESPONSE`; the gateway
never substitutes a newer revision or exposes storage keys/credentials.

Binary output is limited to 16 MiB total, JSON parts to 128,000 encoded bytes
each, and combined structured text/JSON to 1 MiB. Provider content validation
also applies. `acceptedOutputModes` accepts up to 20 MIME preferences from the
card; preferences do not strip complementary text/files. Unknown file formats
use octet-stream. Advertised formats describe transport support, not a guarantee
that the selected model can generate each format.

All inline files validate before persistence: at most five files, 16 MiB each,
32 MiB combined; structured input is bounded to 64,000 encoded bytes. HTTP/gRPC
reception additionally limits the whole base64/JSON request to 24 MiB. Remote file
URLs are never fetched. A2A 1.0 accepts all finite JSON input values. Continuation
context must match its task; approval gates still require Nexus approval.

## Callback setup

Install `nexus-arc[studio-a2a-push]` and configure the existing private
`NEXUS_CREDENTIALS_MASTER_KEY` with a 32-byte key through Nexus credential setup.
Keep that key stable across restart. Optional gRPC additionally requires
`nexus-arc[studio-grpc]`.

In **Entrypoints → Edit**, enable A2A and open **A2A callback notifications**.
Add exact trusted callback URLs. Blank destinations disable callbacks. Equivalent
entrypoint configuration:

```json
{
  "a2a_enabled": true,
  "a2a_push": {
    "destinations": ["https://receiver.example/callback"],
    "allow_private": false
  }
}
```

HTTPS is required; plain HTTP is allowed only on loopback with explicit private
allowance. URLs cannot contain credentials, query strings, fragments or trailing
slashes. Clients can select only a preapproved exact URL. Cards advertise push
only when destination policy, optional dependencies and encryption are ready.
This does not enable a disabled entrypoint or grant access to other users' tasks.

For A2A 1.0, put this shape in `configuration.taskPushNotificationConfig` when
sending a message; omit `taskId` until the task exists:

```json
{
  "id": "reports",
  "url": "https://receiver.example/callback",
  "authentication": {"scheme": "Bearer", "credentials": "RECEIVER_TOKEN"}
}
```

Or POST it to `/api/a2a/ENTRY/rest/tasks/TASK/pushNotificationConfigs`. GET the
collection to list; GET or DELETE `/pushNotificationConfigs/ID` to inspect or
remove. Body and path IDs must agree. Standard JSON-RPC/gRPC create/get/list/delete
methods share these records. Legacy `tasks/pushNotificationConfig/*` and send
`pushNotificationConfig` use `authentication.schemes: ["Bearer"]`.

Only Bearer authentication is supported for callbacks. Credentials are encrypted
in workspace/user/entrypoint/task/config scope, omitted from reads/exports and
excluded from backup/restore. Restored workspaces must register again. Identical
configuration IDs are idempotent; delete before replacing. Ten configurations per
task maximum. Retrying a task message does not create another run or callback.

## Delivery, recovery and revocation

The existing Studio scheduler claims one due delivery per tick. Embedders can use
`studio.operations.start()` or explicitly call `studio.a2a.push.tick()`. There is
no new scheduler, queue or authentication system.

Callbacks POST an A2A 1.0 `StreamResponse` containing a full `task` snapshot with
Bearer authentication and `Content-Type: application/a2a+json`, including for
legacy registrations. `X-Nexus-Delivery-ID` remains stable across retries.
Optional `token` is sent in Nexus's `X-A2A-Notification-Token` correlation header.
Receivers must authenticate, validate the expected task, deduplicate delivery IDs,
and return 2xx only after acknowledgement.

Transient states can coalesce between ticks. Each snapshot receives up to three
attempts with 5/10-second backoff, a 10-second request timeout, no ambient proxies
and no redirect following. Failed notifications never rerun tasks or mutations.
After three failures that snapshot is marked failed; a changed snapshot can
receive a new delivery ID.

Claims commit before HTTP I/O. A crash after receipt but before acknowledgement
leaves an uncertain delivery: after the 60-second lease, recovery retries the
same snapshot and delivery ID. File state supports one controller; SQL state
coordinates claims across controllers. Deletion, owner/target/destination-policy
changes and access revocation fence subsequent dispatch. Re-registering an ID
cannot authorize an older claim. A callback already in flight may complete;
revocation cannot undo a received notification or an external effect.

Private framework records report pending/delivering/retrying/delivered/failed/
blocked status. A read-only owner-scoped Studio delivery-health view is now available;
see [rich client and health setup](STUDIO-M6-A2A-CLIENT.md).
Configuration reads expose neither credentials nor private retry payloads.

## Local validation and migration

Required fixtures: `tests/test_studio_a2a_content.py`,
`tests/test_studio_a2a_push.py`, `tests/test_studio_a2a_http.py` and
`tests/test_studio_a2a_extended.py`. The separate explicit optional gRPC suite is
`tests/studio_a2a_grpc_acceptance.py`; prerequisites are not hidden behind skips.

Run the real sender against a local synthetic receiver:

```sh
postman simulation run postman/simulations/studio-a2a-push.sim.yaml --port 49648
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_push.py
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_push.py --sql
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_push.py --failure
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_push.py --redirect
```

The fixture derives from [A2A specification](https://a2a-protocol.org/latest/specification/)
sections 4.1, 4.3 and 13.2, inspected 20 October 2026. All credentials and receiver
data are synthetic. No external mock owner exists for this receiver; hosted
webhook capture and publishing were not used.

No persisted-record rewrite is required. Workflow metadata and callback rows are
additive. Restart Studio for backend changes; shell cache v26 carries controls.

This implements rich **gateway** outputs and callback sending. Studio's outgoing
client now supports typed input/results, file upload/download and delivery health;
see [the follow-up guide](STUDIO-M6-A2A-CLIENT.md). Authenticated callback reception now has a separate
[setup and recovery guide](STUDIO-M6-A2A-RECEIVER.md).
M06-05 and Milestone 6
remain open. Independent vendor/client accounts, HTTPS VM/TLS delivery and real
mobile devices need live validation; browser width checks do not prove device support.
