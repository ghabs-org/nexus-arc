# Studio OpenClaw operator contract (version 1)

`POST /api/openclaw/{entrypoint_id}` accepts JSON signed using the existing
OpenClaw channel HMAC: `X-Nexus-Timestamp` and
`X-Nexus-Signature: sha256=HMAC(secret, timestamp + '.' + raw_body)`.
The timestamp must be within five minutes. The configured secret must contain
at least 32 characters. Browser origins are rejected.

The saved, enabled `type: openclaw` entrypoint must explicitly list
`operator_targets`, such as `agents:reviewer` and `workflows:review`. Its existing
`identity_map` and `allowed_channels` resolve the caller; caller-provided Nexus
identities, roles, credentials and notification destinations are never accepted.
Current project access and target scope are checked on every request.

Envelope: `version: 1`, `entrypoint_id`, `id` (stable request ID), `sender_id`,
`channel`, `session_id`, `operation`, `arguments` (object). Identity strings are
nonempty and bounded. The entrypoint ID in the signed body must match the route.

| Operation | Arguments | Result |
| --- | --- | --- |
| `discover` | none | Scoped agents/workflows and supported operations |
| `runs` | none | Up to 50 recent runs belonging to this channel sender |
| `schedules` | none | Visible schedules for allowed targets; no scheduling writes |
| `submit` | `target` (`agents:id` or `workflows:id`), `task` | Durable receipt and run; agent turns reuse the bound conversation |
| `status` | `run_id` | Current run, Studio route, and bound approval/input token when waiting |
| `receipt` | `request_id` | Inspect a previous mutation without submitting it again |
| `artifacts` | `run_id` | Run-produced artifact IDs, exact revisions and authenticated download routes |
| `artifact` | `run_id`, `artifact_id`, `revision` | Bounded original bytes as base64; only an artifact referenced by this run |
| `reply` | `token`, `decision` (`approve`/`reject`) or `values` (input-ID map) | Continue the existing checkpoint; never launch another run |
| `cancel` | `run_id` | Existing cancellation semantics; external effects are not undone |

Successful responses use HTTP 200 and include `version: 1`. Input errors return
400, scope/authentication failures 403, missing resources 404, and request-ID
conflicts or uncertain prior outcomes 409. Error bodies do not expose raw
provider exceptions or credentials.

File-backed mutations persist a receipt before dispatch. SQL mode atomically
commits the receipt and queued job; an uncommitted handoff rolls back both before
workers can execute it. An identical request ID and body
returns the existing run, including after restart; different arguments under the
same ID return 409. An interrupted receipt remains uncertain and never dispatches
again automatically. A caller with a timeout must retain the same ID and inspect
its receipt or recent runs; it must not create a new ID to retry a mutation.
Approval/input tokens expire after 24 hours and bind the sender, channel,
entrypoint, Nexus identity, run and exact pending gates. A fresh request cannot
reuse a consumed token. Agent tools cannot approve gates; replies require the
authenticated user command. Artifact access rechecks current ownership and the
recorded revision. Existing channel notifications use their configured fixed
destination, with no new outbound connection created by this integration.
