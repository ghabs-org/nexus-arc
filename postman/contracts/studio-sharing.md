# Conversation snapshot file selection

Studio reuses `POST /api/studio/shares`; the interface is unchanged.

- `session_id` identifies an accessible conversation.
- `artifact_ids` is optional and defaults to an empty list. Up to ten accessible,
  non-archived library files may be included explicitly.
- The server freezes each file's latest revision at creation. The response's
  `artifacts` entries contain `id`, `revision` and `filename`.
- `recipients` contains existing workspace account IDs. `public` defaults to
  false; public links require the workspace owner to enable them separately.
- `expires_hours` defaults to 168 and accepts integers from 1 to 8760.
- Private snapshots require authentication and a matching owner/recipient grant.
  File downloads additionally require membership in the snapshot's file list.
- Replacing the original file does not change a snapshot download. Expiry and
  revocation prevent subsequent snapshot and file retrieval.

The Studio dialog starts with no files checked. It retains the original
conversation ID while open, rejects more than ten selected files without closing,
and shows frozen revisions in the success receipt. Loading the library must
succeed before the dialog opens; an error never creates a snapshot.

`studio-sharing.assertions.js` checks creation against a disposable workspace
with conversation `sharing-demo` and selected files `file-0` and `file-1`.
Never run this synthetic payload against a personal workspace.

Start the disposable fixture with:

```sh
PYTHONPATH=. python tests/studio_sharing_acceptance.py
```

Open the printed `/acceptance` URL to exercise production dialog functions and
styles with the real local sharing API. Substitute its origin below:

```sh
postman request POST http://127.0.0.1:PORT/api/studio/shares \
  --header 'Content-Type: application/json' \
  --header 'X-Nexus-Studio-Token: synthetic-sharing' \
  --body '{"session_id":"sharing-demo","artifact_ids":["file-0","file-1"]}' \
  --script-post-request @postman/contracts/studio-sharing.assertions.js \
  --no-report-events
```

The fixture uses only a temporary workspace and deletes it on shutdown. It does
not enable public sharing or contact any model provider.

The full-app team fixture and completed local browser matrix are documented in
[the sharing guide](../../docs/STUDIO-SHARING.md). Its separate
`studio-sharing-viewer.assertions.js` checks private recipient/public read-only
responses without owner, recipient, session or token-hash metadata. Viewer link
changes and known expiry clear previous content and invalidate pending downloads;
returning from the background or browser cache rechecks access using GET.

## Continue as a private conversation

`POST /api/studio/shares/continue` accepts only `snapshot` (private snapshot ID or
public bearer token), `visibility: private|public`, and a UUID `request_id`.
Existing authenticated workspace session, operator access and CSRF are required.
Desktop uses its existing local authorization. Anonymous public viewers can read
but cannot create private records. The response is `201 {session_id, artifact_refs}`.

The server rechecks the source grant, expiry/revocation and global public-sharing
setting, then copies user/assistant message text only. Selected files are read at
the snapshot's exact revisions and copied to new recipient-owned artifact IDs.
No owner ID, recipient grants, tokens, original session/run references, tool calls,
agent instructions, credentials or project authorization transfer. Original text
is untrusted conversation history, never system instructions. Choosing files for
a later model request remains explicit; creating the copy starts no provider.

Copies are limited to 2000 messages/1 MiB of encoded message data and 32 MiB of
selected files. All source files are read before catalog writes. A repeated request
ID for the same user and snapshot returns the same conversation. Authorization
is rechecked even on repeat. The session is published last; on nontransactional
storage a failed copy can leave private artifact copies, reused by an unchanged
retry. SQL uses the existing workspace transaction. A completed private copy is
independent: later source edits/revocation do not erase it or grant source access.
