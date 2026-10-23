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
