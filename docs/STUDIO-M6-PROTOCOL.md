# Shared A2A task bindings

Update, 20 October 2026: rich gateway outputs and opt-in authenticated callback
delivery now share all three bindings. See [setup, limits and recovery](STUDIO-M6-A2A-RESULTS.md).
Earlier text-only/push-unavailable checkpoint descriptions below are historical.

The HTTP+JSON binding is available at `/api/a2a/{entrypoint}/rest`. It uses the
same task service and persisted mappings as JSON-RPC 0.3/1.0 and the MCP gateway.
Entrypoints must already be explicitly enabled and authorized with their existing
bearer reference. A bearer runs as the entrypoint creator; it cannot approve
operator gates or access another entrypoint's tasks.

| Operation | HTTP+JSON route |
| --- | --- |
| Send / continue | `POST /message:send` |
| Send with events | `POST /message:stream` |
| Inspect task | `GET /tasks/{id}` |
| List / filter tasks | `GET /tasks` |
| Request cancellation | `POST /tasks/{id}:cancel` |
| Subscribe to running task | `POST /tasks/{id}:subscribe` |
| Authenticated discovery | `GET /extendedAgentCard` |

Routes are relative to that base. Send requests contain the message/configuration
directly, without a JSON-RPC envelope. Responses use `application/a2a+json`;
streams contain `StreamResponse` objects in SSE data frames. HTTP binding accepts
version 1.0 (default); legacy 0.3 remains on its existing JSON-RPC endpoint.

Task IDs and message deduplication are shared across bindings. Retrying the same
message through another binding returns the same task. Reusing a message ID with
different content rejects. Polling/subscription never submit another task. Input
continuation preserves the original run and paused graph; bearer messages cannot
approve operator gates. File and SQLite fixtures verify shared task identity.

GET filters use camelCase (`contextId`, `status`, `pageSize`, `pageToken`,
`historyLength` where applicable). Repeated/unknown parameters and malformed
integers reject. A cancellation URL's ID cannot be overridden in its body.

Errors use HTTP status plus a `google.rpc.Status` JSON representation. A2A-specific
errors include an ErrorInfo reason and `a2a-protocol.org` domain. Cross-entrypoint
lookups return TASK_NOT_FOUND. Completed cancellation returns TASK_NOT_CANCELABLE;
running cancellation reports actual local state and retains the warning that
external effects may still complete. It never promises an effect was undone.

Subscriptions recheck authorization during delivery and close on revocation.
Connections are bounded to the existing 30-second subscription window; reconnect
by task ID. Terminal/paused tasks use normal GetTask rather than a new subscription.
No automatic external action or run resubmission occurs on reconnect.

Agent cards advertise JSONRPC and HTTP+JSON. Optional gRPC is advertised only
while an explicitly started listener serves that entrypoint. Push delivery remains
unimplemented and unadvertised. Output advertisement is restricted to text/plain,
matching gateway negotiation; existing run output remains text even when it
contains serialized JSON. Images are not advertised until selected provider/model
support can be established. Existing explicit attachment handling is unchanged.

The binding follows the [official A2A specification, sections 5.4 and 11](https://a2a-protocol.org/latest/specification/),
inspected 19 October 2026. The contract is
[studio-a2a-http.yaml](../postman/contracts/studio-a2a-http.yaml).
These bindings satisfy clause M06-06 locally, not full Milestone 6 acceptance.

## Reproduce

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_http.py
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_http.py --sql
# Optional temporary localhost server for independent protocol clients:
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_http.py --serve --sql --port 5107
```

The example performs REST send, same-message JSON-RPC retry, legacy 0.3 retrieval,
REST listing and completed-task cancellation rejection. It uses real local tools
and a synthetic localhost token, with no provider account or external action.
Saved Postman assertions cover collection/task identity and conservative discovery.
The token in the example is a disposable fixture, not production configuration.

Migration is additive: no task, message, graph or SQL record rewrite. Older
JSON-RPC URLs and request formats stay readable. The new binding runs inside the
existing Studio server, with no new listener, authentication database or scheduler.

Milestone 5 remains open. At the user's direction, work has moved to other
milestones rather than treating its remaining external composition questions as a
blocker. OAuth/OIDC, push delivery, richer output interoperability,
Teams and remaining connector/account acceptance are still tracked separately.


## Optional gRPC

Install `nexus-arc[studio-grpc]`, which pins the official A2A SDK gRPC extra at
1.1.5. It remains optional: normal Studio startup imports no SDK or gRPC packages.
The generated `lf.a2a.v1.A2AService` wire contract calls the same shared task
operations as HTTP and JSON-RPC. No second task store or authentication service is
created. Explicit client requests can use the listener's existing bearer identity.

```python
from nexus.core.studio_a2a_grpc import StudioA2AGRPC

listener = StudioA2AGRPC(studio, existing_entrypoint_id)  # loopback, ephemeral port
# listener.address is the generated client's channel target.
# Studio shutdown closes it; earlier explicit stop is also supported.
listener.close()
```

Non-loopback binds require caller-supplied gRPC TLS server credentials; the default
is loopback. Each listener serves one existing entrypoint. Unary and streaming
calls recheck authorization, report standard gRPC statuses with typed A2A ErrorInfo,
and never resubmit on subscriber disconnect. Push configuration RPCs explicitly
return PUSH_NOTIFICATION_NOT_SUPPORTED. Native A2A 0.3 JSON-RPC remains supported;
the optional gRPC listener implements 1.0.

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_http.py --grpc --sql
python -m pytest tests/studio_a2a_grpc_acceptance.py -q -o addopts=''
```

Ten explicit optional tests use official generated protobuf clients, real gRPC
channels and both state backends. They cover task identity shared with REST,
registration/removal in discovery, authorization, typed missing/terminal errors,
streaming, unsupported push, continuation approval protection, SQLite restart and
shutdown, including blocked sends and subscriptions that release without cancelling tasks. These ten tests are reported separately from required regressions;
missing optional prerequisites are not hidden as pytest skips. Real TLS/remote-VM
operation remains live validation pending.

Subsequent account checkpoint: delegated MCP OAuth/OIDC and the three-transport
authentication/recovery matrix now have scoped local acceptance. This supersedes
the earlier OAuth gap above; other M6 requirements remain open. See
[setup, migration and client limits](STUDIO-M6-OAUTH.md).
