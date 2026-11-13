# Microsoft Teams channel connector contract

Microsoft Graph stable v1.0; configured team UUID and one channel only. Nexus
credential references resolve per execution identity. Supported selected actions:
`status` (GET channel metadata), `query` (GET messages, one message or replies),
`send` (POST message/reply), `update_message` (PATCH own message body).
Writes use existing Nexus explicit-confirmation/workflow approval path. No retry.
Only text bodies, bounded page sizes, fixed Graph origin and checked pagination
paths. Unknown fields, transport/auth overrides and arbitrary URLs fail preflight.

Credentials: delegated work/school Graph access token. ChannelMessage.Read.All for
reads, ChannelMessage.Send for sends, ChannelMessage.ReadWrite for updating own
messages. Appropriate admin consent/membership may be required. Personal Microsoft
accounts and generic application tokens cannot be advertised as delegated sends.
Expiry is reported as a failed operation; no silent account/provider fallback.

Evidence: Microsoft Learn channel-get, channel-list-messages,
channel-post-messages and chatmessage-update, inspected 20 October 2026. Orbit
search/integrate supplied related beta read contracts; stable vendor definitions
are authoritative for implemented paths. No tenant/account was contacted.
