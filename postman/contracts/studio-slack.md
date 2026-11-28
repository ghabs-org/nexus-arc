# Slack selected-channel mutations

## Opt-in threaded agent conversations

Slack entrypoints may explicitly enable `threaded_conversations: true` for an
agent target. Missing/false retains existing behavior; non-boolean values and
workflow targets with this option fail validation. Existing signed-event and
user/channel mapping checks remain authoritative. Only ordinary verified message
events gain thread state; challenge and approval callbacks retain their format.

The parent `thread_ts`, or root message `ts`, and message `ts` must be valid
timestamp strings. The signed workspace ID, entrypoint ID/revision, channel,
sender, mapped Nexus identity and target form a versioned binding to one private
Nexus conversation. Runtime session IDs are never accepted from the event.
Different users, threads, workspaces or entrypoint revisions get separate history.
Archived, missing-binding or mismatched conversations fail rather than being
silently adopted or replaced.

Events within a binding wait in receipt order while earlier work runs. Existing
Nexus session/SQL queue guards serialize model calls. Duplicate event IDs retain
one durable receipt. Pending thread events use `slack_received`, `slack_launching`
and `slack_running` states so older readers ignore them instead of sending a
channel-level fallback. An uncertain launch is never automatically replayed;
inspect Activity before an owner explicitly saves a new entrypoint revision and
sends a new message. Entry changes/revocation suppress stale dispatch and replies.
Startup recovery marks interrupted `slack_launching` receipts `uncertain`.

Pause opted-in entrypoints before downgrade or mixed-version deployment. Older
binaries ignore versioned pending states but may handle newly arriving messages
without threads because they do not understand the new flag. Retain session and
receipt metadata; resume with the new reader after review.

`send` additionally accepts a validated optional `thread_ts`. The SDK uses the
selected channel, parent timestamp and `reply_broadcast:false`. Gateway replies
use only their verified binding. New threads and follow-ups reuse existing state,
jobs, conversations and credentials. No external conversation history is fetched.
Other mapped users in the same Slack thread cannot inherit private Nexus history;
outgoing replies remain visible to members of the selected Slack channel/thread.

Authority: [message events](https://docs.slack.dev/reference/events/message/)
and [thread replies](https://docs.slack.dev/reference/methods/chat.postMessage/).
Real Slack permissions/delivery, Socket Mode, outbound files and workflow-thread semantics
remain outside this acceptance slice.

Studio reuses the framework Slack notification adapter and the installed
`slack-sdk` client. Existing `send` and `update_message` actions, credential
references, Nexus permissions, disabled defaults and explicit review remain.
No new endpoint, account, credential format or installation hook is added.

- `send` uses `POST https://slack.com/api/chat.postMessage`, bearer authorization,
  the configured channel, text fallback and Markdown sections. Sections split at
  3000 characters while retaining exact text, within Studio's existing 16000
  character bound. Buttons remain separate action blocks.
- `update_message` uses `POST https://slack.com/api/chat.update`. `message_id`
  accepts a decimal timestamp string or `configured_channel:timestamp`. A foreign
  channel prefix, missing/invalid type or malformed timestamp fails before adapter
  construction or network access. The adapter's standalone cross-channel syntax
  remains available outside Studio; Studio enforces its selected destination.
- The framework adapter disables SDK automatic retries. Sends/updates that fail
  or lose their response require inspection of the remote outcome before a new
  explicitly reviewed attempt. Rate limits and HTTP-200 `ok:false` failures do not
  replay. Send/update provider errors are excluded from adapter error logs and Studio
  errors. This does not undo an effect completed before a lost response.
- Model-generated confirmation cannot approve a write. Disabled connections,
  unselected actions and account/project revocation retain their existing guards.

Sources: [message sending](https://docs.slack.dev/reference/methods/chat.postMessage/),
[message updates](https://docs.slack.dev/reference/methods/chat.update/) and
[section limits](https://docs.slack.dev/reference/block-kit/blocks/section-block/).
Selected SDK runtime: `slack-sdk==3.45.0`. Earlier Orbit discovery returned unrelated providers. Follow-up discovery found
`urn:orbit:endpoint:v1:aGr0AU43bLMOIL3xTeahF5HUoMzixI4h:slack:chat-postmessage`;
its integrated brief omitted `thread_ts` and the `/api` path. The implementation
therefore uses official Slack docs and the installed SDK contract.
Published owner-mock discovery requires Postman login and was unavailable. The
local mock is derived from those public methods and handles only this slice.

This covers outgoing text/updates, opt-in agent threads and scoped private inbound files. Frozen
Slack entries also promise workflow-thread semantics, outbound files,
rich artifact handling and gateway interactions. Those need independent native
implementation and acceptance; this slice does not complete either gateway entry.

## Private inbound hosted files

`capture_slack_files: true` requires a Slack agent entrypoint with
`threaded_conversations: true` and a saved fixed-channel Slack connection.
The connection must remain enabled and at its captured revision. Existing
personal/service credential scoping and project permissions apply. False/missing
preserves old behavior; malformed flags and unsupported targets fail validation.

Signed message/file_share payloads provide file IDs only. The worker calls
`GET https://slack.com/api/files.info?file=ID` with bearer authentication and
checks `ok`, matching ID, selected-channel shares, hosted/non-external mode,
filename and bounded integer size. Original bytes come only from the returned
`url_private_download` at HTTPS files.slack.com/files-pri/. Redirects, proxies,
automatic retries, event URLs and alternate hosts are rejected. The same bearer
header is used for that exact private origin. Never create public file links.

Metadata: 256 KiB maximum. One to five distinct files, 16 MiB each, 32 MiB total.
Actual downloaded bytes must match metadata exactly. No files are saved until
all downloads succeed. Native private artifacts and expected revisions are passed
through normal provider attachment handling. Image capture does not certify a
particular provider's vision support. File-only events receive a default task.

New receipt states are slack_files_pending/fetching/ready/launching/running, using
the full slack_files_ prefix for each. A nonce fences capture commits. Recovery
makes fetching/launching uncertain; late workers cannot overwrite recovery or
save files. No ambiguous capture or model dispatch is automatically repeated.
Native file/SQL snapshots retain bytes and references, with entrypoints disabled
after restore. Pause entrypoints before downgrade/mixed-version deployment.

Sources: [files.info](https://docs.slack.dev/reference/methods/files.info/),
[file object](https://docs.slack.dev/reference/objects/file-object/), and
[file_share](https://docs.slack.dev/reference/events/message/file_share/).
Orbit integrated `urn:orbit:endpoint:v1:aGr0AU43bLdFfAqJhB7t5PkyIXDmBi9H:slack:files-info`
and confirmed metadata/auth/scope. Its partial fit omits the actual download
operation; official file-object documentation supplies that contract. Published
owner mock discovery remains unavailable while signed out; existing local Slack
mock extends these public shapes. No live Slack workspace is used.
