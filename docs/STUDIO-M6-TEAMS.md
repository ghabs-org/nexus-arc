# Microsoft Teams in Nexus Studio

Microsoft Teams appears in the integration catalog and connector editor. Select
one team UUID and channel ID, a token environment reference, allowed actions and
explicit enabling. Credentials resolve through the executing Nexus identity.

Supported actions:

- `status`: read channel ID/display name; used for explicit resource health.
- `query`: read a bounded page of channel posts, one message or its replies.
  `limit` is 1–50. Pass returned `@odata.nextLink` as `next_page`; the client checks
  the fixed Graph origin, selected channel/message path and cursor parameters.
- `send`: send text, optionally with `reply_to` for a reply.
- `update_message`: edit text using `message_id`.

Writes reuse Nexus explicit confirmation and workflow approval gates. An uncertain
write is never automatically repeated. Plain text is sent as text, not HTML.
Arbitrary URLs, headers, buttons and unsupported arguments are rejected. Errors do
not expose token/provider response bodies or fall back to another account.

Use a Microsoft Graph delegated work/school token with the required permissions
and channel membership. Personal Microsoft accounts are unsupported for these
channel APIs. Sends use ChannelMessage.Send; reads use ChannelMessage.Read.All;
editing one's own messages uses ChannelMessage.ReadWrite. Tenant consent may be
required. Nexus stores credential references only; expired tokens fail visibly.
This connector consumes a configured access token; it does not add an identity
provider or silently create subscriptions/change-notification endpoints.

See Microsoft's [channel reads](https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-1.0),
[channel sends](https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0)
and [message updates](https://learn.microsoft.com/en-us/graph/api/chatmessage-update?view=graph-rest-1.0).
Stable v1.0 definitions were inspected on 20 October 2026. Orbit provided related
beta read contracts; the stable vendor contracts govern implemented endpoints.

`examples/nexus-bot/src/studio_extended_integrations.py` exercises actual framework
calls through `postman/simulations/studio-m56.sim.yaml`. Its synthetic Graph peer
is mapped only inside the example's injected request client. Production URLs
remain fixed to Microsoft Graph. No real tenant or external message was used.

Existing connections remain unchanged. Shell cache v30 includes the editor.
Backend restart is required; local validation does not restart an existing server.
Real delegated-account acceptance remains pending.

## Incoming channel tasks

Entrypoints now include Microsoft Teams. Configure the selected team/channel,
tenant ID, verification secret environment reference, reader credential reference,
remote Microsoft-user → Nexus-user mapping, and command prefix (default `/nexus `).
Allowed channels must contain exactly the selected channel. The Graph reader uses
the entrypoint owner's configured account; the mapped sender's current Nexus
permissions control execution. Local users map to `desktop`.

1. Save the entrypoint disabled; subscription ID may be blank at this stage.
2. Register a **basic** Graph channel subscription with `includeResourceData: false`,
   `changeType: created`, and notification URL `/api/channel-events/ENTRYPOINT_ID`
   on your HTTPS host. Set `clientState` to the server's verification secret.
   Nexus echoes Graph's validation token as plain text during this disabled setup.
3. Copy Graph's returned subscription ID into the saved entrypoint, then explicitly
   enable it. Registration, consent and renewal are performed by the operator;
   browsing or saving never changes a Microsoft subscription.

Microsoft requires subscription renewal; follow its documented expiry and lifecycle
requirements. Nexus does not claim to renew subscriptions automatically. Until your
personal HTTPS VM is deployed, local tests use synthetic peers only.

Receipts verify the tenant, subscription, fixed channel resource and secret before
saving a durable message hint. Resource-data text, URLs and claimed Nexus identities
are ignored. The existing scheduler fetches the authoritative message from fixed
Graph endpoints. Only new user messages/replies beginning with the command prefix
start tasks. HTML is converted to text without loading resources. Duplicate delivery
starts one task; edits/deletes never repeat it. Configuration or permission changes
revoke pending work. Failed/uncertain dispatch is visible and never automatically
repeated. Both file and SQLite state use existing messaging recovery and task gates.

Results, input continuation and approvals stay in Studio. Automatic channel replies,
in-channel approval buttons, encrypted rich notifications, direct chats and bot-JWT
integration are not supported by this incoming adapter. Outgoing reviewed messages
remain available through the separate Teams connector. No existing entrypoint is
converted or enabled during upgrade. Shell v30 includes the new setup form.

The documented notification and handshake formats follow Microsoft's
[channel message notification contract](https://learn.microsoft.com/en-us/graph/teams-changenotifications-chatmessage)
and [webhook delivery contract](https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks).
Local HTTP fixtures cover both storage backends, restart, forged payloads, reply
reads, atomic batch preflight, duplicates and mapped-reader/sender role revocation.
The Postman framework example also receives one duplicate hint and launches exactly
one task after a real local Graph-shaped GET. Actual tenant/VM acceptance remains pending.
