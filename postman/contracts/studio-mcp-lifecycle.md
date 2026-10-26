# Connector shutdown ownership

Existing connector configuration and `POST /api/connectors/{id}/test` inputs do
not change. A direct request and a workflow invocation retain selected actions,
selected tools, read-only/approval rules, account isolation and no automatic
mutation replay. No new endpoints or persisted records are introduced.

Studio now owns direct connector tasks through its existing execution tracking.
On shutdown, new connector dispatch is rejected before remote work. Existing
request contexts are cancelled once and allowed to settle within the bounded
close window. Calls inside an already owned run are not independently cancelled
a second time, including child tasks inheriting that run context.

Persistent MCP sessions begin their SDK teardown immediately when Studio closes,
rather than waiting five seconds for an active call. One-shot transport cleanup
and pooled transport cleanup run concurrently. The SDK owns termination of each
stdio server's process group. An interrupted call is never automatically replayed;
shutdown does not undo an external effect that already happened.

Acceptance uses an independent local newline-delimited JSON-RPC server with a
SIGTERM-resistant child. Source checks cover one-shot and persistent calls. A
frozen backend must pass protected HTTP invocation, explicit parent shutdown and
abrupt launcher death, with no surviving owned fixture processes, no replay and
a released workspace lock. No external accounts, public endpoints or credentials
are involved; this is a real stdio process fixture, not an HTTP service stand-in.
Docker/container cleanup and daemonized descendants outside the SDK process
group remain separate gates.
