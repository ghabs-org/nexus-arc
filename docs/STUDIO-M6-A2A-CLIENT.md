# Rich external-agent tasks and delivery health

Studio's outgoing A2A JSON-RPC client now sends structured input and selected
files, retains typed results and imports returned files into the existing private
artifact library. A2A 1.0 and 0.3, polling, streaming, continuation and read-only
refresh all use the same validators. Existing text-only requests remain valid.

## Use in Studio

Open **External agents → Run task → Files and structured input**. Choose existing
files, upload new files and optionally enter JSON. The form names the destination
before sending. The same controls are available under **Provide input** for a
paused remote task. Invalid JSON remains in the form; submissions are disabled
while uploading/sending to prevent accidental repeated clicks.

Task details show JSON and links to private, pinned file revisions. Files can be
previewed/downloaded through the existing artifact viewer and reused later.
Imported files also appear as artifact events in execution history. Plain `output`
is preserved for existing consumers; rich data is in `structured_content`.

Under **Entrypoints**, **Delivery health** shows callback configuration/task IDs,
delivery state, attempt count and next eligible attempt. Refresh only reads state;
it never retries or dispatches. The view is owner-scoped, including for team
administrators, and omits callback URLs, tokens, credentials and private payloads.
It reports disabled or changed configuration without a network probe. Ready means
local prerequisites are configured, not that the remote receiver is reachable.

Authenticated callback reception is now available as an explicit per-task opt-in;
see [receiver setup and recovery](STUDIO-M6-A2A-RECEIVER.md).

## Framework/API consumer

```python
run = studio.start_run({
    "kind": "external",
    "external_agent_id": "reviewer",
    "task": "Review this evidence",
    "external_input": {"city": "Rome"},
    "artifact_ids": ["existing-file-id"],
})
```

The same payload is accepted by `POST /api/runs`. The existing continuation route
accepts `task`, `external_input` and `artifact_ids`. Health is read through
`GET /api/studio/entrypoints/ID/a2a-deliveries?limit=50`, with `after` from the
returned `next_cursor`. Limit is bounded to 1–100.

Input files pin revisions before queueing and must belong to the sending user.
Workers recheck both current and pinned ownership. Only explicitly selected files
are sent; they are transported as bytes instead of being extracted into prompt
context. Total input is five files and 16 MiB combined. JSON input is bounded to
64,000 encoded bytes; 1.0 accepts any finite JSON value, while legacy DataPart
requires an object. Remote cards must advertise the selected JSON/file input mode.
Text-only legacy cards without modes remain usable for text.

Connection settings and owner are bound at queue time. A changed connection is
rejected before discovery or send. Continuation, refresh, downloads and result
publication also recheck the connection binding and current account permissions.
Changing credentials in the existing environment reference remains normal account
rotation; the client never falls back to another account or origin.

## Returned files and recovery

Supported result parts are text, finite JSON, inline files and absolute file URLs
on the selected agent's exact origin. Cross-origin URLs, redirects, unsafe names,
malformed/mixed parts and oversized content fail explicitly. Same-origin downloads
use the selected account's credentials, fresh authorization, bounded reads and no
ambient proxies. There is no credential forwarding to another origin.

File output is bounded to 16 MiB combined; task responses to 24 MiB; structured
content/text to 256 KiB total, with 128,000 encoded bytes per JSON part. Streaming
events are bounded to 24 MiB each and 64 MiB per connection. The complete result
validates before imported artifacts are created. Stable local IDs retain identical
file revisions across refresh, without duplicate artifact events.

Sends are never automatically retried. Once a remote task ID is acknowledged,
stream interruption recovers through GetTask. **Refresh remote status** reads that
existing task and can import repaired/available results without another send.
An explicit task/context change is rejected. For compatibility, a task read that
omits context retains the already known context. Without an acknowledged task ID,
the result stays uncertain and requires remote review before another submission.

The client currently selects JSON-RPC interfaces; it does not silently switch to
REST or gRPC. Operator approval gates remain controlled by the remote service.

## Repeatable local checks

`tests/test_studio_external_content.py` uses an independent real HTTP peer for
file/SQL state, both versions, polling/streaming, continuation, pinned uploads,
malformed results, same-origin downloads, bounds, revocation and no-resend recovery.
`tests/test_studio_a2a_delivery_health.py` checks health privacy, ownership and
pagination through the real Studio handler.

The example consumes the real framework client against a synthetic Postman peer:

```sh
postman simulation run postman/simulations/studio-a2a-client.sim.yaml --port 49650
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_client.py
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_client.py --sql --legacy
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_client.py --url-file
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_client.py --failure
```

The synthetic peer follows the public [A2A Part/Artifact specification](https://a2a-protocol.org/latest/specification/),
inspected 20 October 2026. It has no external owner/account; no hosted mock search,
capture or publishing is needed. Simulation logs prove actual HTTP calls; they
do not establish vendor-specific live compatibility.

No new dependencies or state rewrite are required. Rich run metadata, pinned
references and delivery attempt counters are additive. Older delivery rows may
lack historic attempt counts. Restart Studio to load the backend; shell cache
v28 carries the controls. Existing user/server instances are not restarted by
the local validation workflow.

## Remaining scope

Authenticated **callback reception**, sending and delivery health are available;
see [receiver setup and recovery](STUDIO-M6-A2A-RECEIVER.md).
Independent vendor accounts, personal HTTPS VM/TLS deployment and physical-device
checks remain pending. M06-05 and Milestone 6 remain open.
