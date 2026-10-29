# Continue a shared snapshot privately

Open an accessible snapshot and select **Continue as a private conversation**.
Sign in to Studio first when using a shared team server. Public snapshots remain
readable without login, but anonymous visitors cannot create private records.

Studio copies message text and explicitly shared files into your account, then
opens the new conversation. Selected files retain the snapshot's exact bytes;
source edits do not change them. Copied files can be previewed from the conversation
and attached explicitly from Library → Artifacts. Copying never starts AI or
attaches file content to a model automatically. Sending your next message includes
the copied message history as normal conversation context.

The new conversation does not inherit the source agent, provider credentials,
project membership, run IDs, tool calls, recipient grants or public bearer token.
Only user/assistant message text transfers. The source snapshot stays unchanged.
Current grant, expiry/revocation and the public-sharing setting are checked at copy
time, including explicit retries. A completed private copy remains yours after
source revocation, like a file already downloaded from a snapshot.

A UUID request ID prevents repeated clicks or explicit transport-error retries
from creating duplicate copies. No mutation is retried automatically. Messages
are bounded to 2000 entries and 1 MiB encoded data; selected file bytes total at
most 32 MiB. All source files are read before private catalog writes. The session
is published last. On filesystem state, interruption can leave private artifact
copies visible in your Library; an unchanged retry reuses them. SQL state uses
its existing workspace transaction. No original-owner permissions are granted.

The additive API is documented in `postman/contracts/studio-sharing.md`.
The local example is `examples/nexus-bot/src/studio_snapshot_continuation.py`.
No persisted-data migration, provider account or new dependency is needed.

## Activity artifact-version audit

Captured artifact events already retain `artifact_revision`; replay opens
`/api/catalog/artifacts/{id}/preview?revision={revision}`. Tests verify captured
revision 1 remains readable after later replacement/archival. Document previews
already support immutable page/section navigation.

Legacy events without recorded revision cannot reconstruct their original bytes.
Their button now says **View current artifact (revision not recorded)**. This
labels the fallback honestly rather than claiming historical precision. The audit
covers pinned preview navigation, not a new jump-to-producing-session feature.
