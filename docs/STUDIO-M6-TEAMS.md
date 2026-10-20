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

Existing connections remain unchanged. Shell cache v29 includes the editor.
Backend restart is required; local validation does not restart an existing server.
Real delegated-account acceptance remains pending.
