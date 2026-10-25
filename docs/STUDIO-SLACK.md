# Slack messaging and gateway scope

Studio's Slack connection currently supports reviewed text sends and message
updates in one configured channel. Configure it through the integration catalog,
keep its credential as an environment/account reference, select the actions and
enable it after review. Coding subscriptions do not supply a Slack bot account.

The connection sends the configured channel independently of model arguments.
An update accepts the Slack message timestamp or that same channel followed by
`:` and the timestamp. A foreign channel prefix is rejected before account
resolution or HTTP, including approved workflow actions. The standalone ARC
notification adapter still supports an explicit cross-channel update when used
outside Studio's selected-channel connection.

Messages retain their text fallback and split Markdown sections within Slack's
3000-character section limit. Callback buttons remain separate action blocks.
This preserves text rather than truncating it; it does not implement complete
Slack-specific Markdown or artifact formatting.

The adapter disables automatic SDK retries. A rate limit, provider error or lost
response returns a failure. A lost response can follow a completed remote send;
inspect Slack before an explicitly reviewed retry. The code does not claim to
undo that effect. Send/update error logs and Studio errors omit provider error
values. This changes previously implicit SDK retry behavior and requires no
schema, secret or resource migration.

## Opt-in threaded agent conversations

Under **Entrypoints**, choose Slack and an agent target, then enable **Keep each
person's conversation in its Slack thread (agents only)**. Configure the existing
signed-event secret, allowed channel and user mapping. To send replies, select a
Slack reply connection fixed to that same channel and enable automatic replies.
Saving an entrypoint does not register remote subscriptions or change Slack.

Verified root messages and follow-ups share a native private Nexus conversation.
The binding includes the signed Slack workspace, channel, parent timestamp,
Slack sender, mapped Nexus owner, agent and entrypoint revision. Each sender has
separate Nexus history, even within a shared Slack thread. Replies still remain
visible to members of the Slack channel/thread. No Slack history is fetched and
no external session ID is trusted. Workflow targets cannot enable this option.

Follow-ups wait in server receipt order. Native jobs, session permissions, file
state and SQL locks provide restart recovery and two-controller serialization.
Duplicate event IDs do not start another run. Archived, tampered or missing
historical sessions fail rather than being replaced. Mapping changes and revoked
project access suppress pending work and stale replies. Replies use the verified
parent timestamp and explicitly disable channel-wide reply broadcasting.

An interrupted launch is uncertain and never automatically repeated. Inspect
Activity and any remote effects first. Saving the entrypoint creates a new
revision and starts fresh history on the next message; prior private conversations
remain readable. This is an explicit owner recovery action, not a retry of the
uncertain run.

### Upgrade and downgrade

The flag is optional and defaults off. Existing entrypoints keep their behavior.
New sessions add `slack_thread_binding` with version `nexus-slack-thread/v1`.
Pending receipts use `slack_received`, `slack_launching` and `slack_running`;
older readers ignore these states. New startup recovery marks an interrupted
`slack_launching` receipt `uncertain`, blocking implicit thread continuation.

**Pause opted-in entrypoints before downgrade or mixed-version deployment.**
An older binary ignores the opt-in flag on newly received messages and could
handle them without thread continuity. Keep receipts and session metadata intact.
Resume with a new reader after review; resaving creates a new revision and fresh
history. File mode remains single-process; shared-controller acceptance uses SQL.

## Opt-in private inbound files

Enable **Attach Slack files to agent messages** under a Slack agent entrypoint.
Threaded conversations must also be enabled. Select an enabled Slack connection
for the same channel; capture reuses that connection's account/environment
reference with the existing personal/service credential policy. The account needs
`files:read`. Capture does not require enabling outgoing sends or automatic replies.
Existing entrypoints default to no file capture. Coding subscriptions do not
provide Slack credentials.

This explicitly permits source files to reach the selected agent/provider through
the ordinary attachment path. Studio accepts signed ordinary messages and
`message`/`file_share` events. It keeps only one to five distinct file IDs, then
acknowledges the durable receipt before any network call. Empty file-only messages
use “Inspect the attached files.” as the task. Existing plain-text messages and
approval callbacks retain their behavior.

The event worker claims capture under the native workspace/SQL lock, releases
that lock during network I/O, resolves `files.info` and downloads private hosted
bytes. Metadata must name the same file, show a share in the selected channel,
and provide a valid filename, exact size and supported private download URL.
Only HTTPS `files.slack.com/files-pri/` URLs from freshly fetched metadata are
accepted. Event URLs, public links, remote/external files, redirects and other
hosts are never used. Authentication remains in the header, never in the prompt
or receipt. Proxies and automatic retries are disabled. Metadata is bounded to
256 KiB; files to 16 MiB each and 32 MiB combined. Declared and actual bytes are
checked. Connection/read timeouts bound stalled requests.

Only after every download succeeds does Studio save owner-scoped native artifacts.
The run receives expected immutable artifact revisions; edited files fail preflight
rather than substituting new content. CLI providers get temporary original files;
API providers retain existing format/vision restrictions. Local text execution and
private image capture have actual HTTP evidence; live vision providers are separate.
Files are untrusted source material. No document content is silently copied to an
unselected API provider.

Entry/configuration revisions, user mappings, project permissions and the selected
connection revision are checked again before capture, private save and dispatch.
A capture claim invalidated by recovery cannot commit a late download. Failures
start no run. Partial artifacts from a storage failure remain private and inspectable.
Sending a new platform message is an explicit new capture; no failed or ambiguous
capture automatically retries.

### File receipt compatibility and restore

File receipts use `slack_files_pending`, `slack_files_fetching`,
`slack_files_ready`, `slack_files_launching` and `slack_files_running`. The
`slack_files` manifest is versioned `nexus-slack-files/v1`; it stores file IDs and
connection ID/revision, never credentials or provider download URLs. Native
`slack_artifact_refs` store pinned artifact references. No new record collection,
job family, storage backend or authentication service is introduced.

Earlier readers ignore these new pending states. Pause opted-in entrypoints before
any downgrade or mixed-version deployment: older readers can still accept newly
arriving events without understanding file capture. Interrupted fetching/launching
becomes `uncertain` on new-reader startup and blocks implicit thread continuation.
Review Activity and private Library files before saving a new entrypoint revision
and sending a fresh message. File/SQLite restart and stopped backup/restore retain
manifest, private bytes and references. Restores keep external entrypoints disabled.
Outbound Slack uploads, external-file providers, workflow attachments and real
Slack account delivery remain outside this slice.

## Local validation

With the existing Slack optional dependency installed (`slack-sdk==3.45.0` was
tested), start the synthetic dependency separately:

```sh
postman simulation run postman/simulations/studio-slack.sim.yaml --port auto --no-history --output ndjson
```

Use its printed port for the local member URL:

```sh
export NEXUS_ACCEPTANCE_SLACK_URL=http://127.0.0.1:PORT/slack
PYTHONPATH=. python examples/nexus-bot/src/studio_slack.py --base "$NEXUS_ACCEPTANCE_SLACK_URL"
PYTHONPATH=. python examples/nexus-bot/src/studio_slack_threads.py --base "$NEXUS_ACCEPTANCE_SLACK_URL"
PYTHONPATH=. python examples/nexus-bot/src/studio_slack_files.py --base "$NEXUS_ACCEPTANCE_SLACK_URL"
PYTHONPATH=. python -m pytest tests/studio_slack_acceptance.py tests/studio_slack_files_acceptance.py -q -o addopts=''
```

The consumer makes ten real SDK requests covering sends, updates, long sections,
buttons, HTTP-200 provider errors, rate limits and server errors. Review, model
self-approval, disabled connection and destination guards fail without traffic.
An additional direct local HTTP peer applies one synthetic effect and closes its
socket; the real SDK sends once and reports uncertainty. A proxy can convert a
dropped upstream socket into HTTP 502, so that direct peer is deliberately separate
from the Postman stand-in. Missing optional prerequisites fail rather than skip.
The threaded consumer also sends signed events through a real local Studio HTTP
server and makes three real SDK thread replies. Alice retains history, Bob gets
separate history, duplicate events and stale mappings produce no extra sends.
The additional file consumer makes eleven metadata/download requests, verifies
private text/image artifacts and one pinned text run, and blocks redirects,
provider failures, oversized metadata sizes and truncated downloads. All four
explicit SDK/HTTP tests pass. No real Slack account, user channel or
message is used.

## Remaining frozen gateway coverage

Source inspection at frozen commit `7599710a38318a336c47cf605e4774d4add24d8e`
shows that both Slack catalog aliases represent gateways, not only notification
clients. Earlier generic local-acceptance flags overstated this coverage. Both
entries are now partial; the outgoing SDK evidence above is retained separately.

The shared native gateway still needs these source-defined behaviors and their
acceptance checks:

- Workflow-thread semantics beyond the verified agent conversation path.
- Outbound artifact delivery/downloads and broader workflow file semantics.
- Rich structured updates and artifact-aware reply formatting.
- Slack feedback controls connected to existing Nexus feedback/evaluations.
- Task cancellation and artifact/help command interactions.
- A verified Socket Mode path; signed HTTPS events currently provide the native
  incoming route, while the local-only transport remains unimplemented.

Implement one shared gateway for both aliases. Reuse existing Nexus identity
bindings, private artifacts, sessions, jobs, approvals, feedback and cancellation.
External identity-provider and event-broker services stay outside the agreed
scope. Existing signed-event verification and durable receipt deduplication remain
useful foundations; a URL challenge fixture does not certify complete gateway
behavior. Real account scopes, Socket Mode, personal HTTPS VM and physical devices
remain separately unverified. See [outgoing acceptance](STUDIO-SLACK-CHECKPOINT.json) and
[thread acceptance](STUDIO-SLACK-THREADS-CHECKPOINT.json).

See [private inbound file acceptance](STUDIO-SLACK-FILES-CHECKPOINT.json).
