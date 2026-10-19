# Knowledge project settings and portable packages

The existing reference-only `GET /api/studio/projects/{id}/export` and
`POST /api/studio/projects/import` version-1 format remain compatible. Configuration exports now include the optional
`retrieval_mode`; older imports without it retain lexical search.
Additive portable format: `nexus-knowledge-package/v1` through
`GET /api/studio/projects/{id}/package` and the same import route.

A package contains only project name, instructions, default agent, default search
method, and selected attached document bytes at their immutable revisions. Each
file has filename, SHA-256, base64 content and source artifact ID/revision as
provenance. It excludes chats, identities/grants, indexes, credentials, provider
endpoints and executable installation hooks. Import creates fresh project and
artifact IDs under the authenticated current workspace owner; source IDs never
address or overwrite target resources. Default agents must exist in the target.

Both paths authorize every referenced revision. Import validates the complete
structure, supported fields, all filenames/encodings/hashes, the project settings
and size limits before saving anything. Limits: 100 documents, 16 MiB combined
file bytes, 24 MiB encoded JSON. New documents use the existing background index
jobs; imported text is never sent to a cloud model automatically. Storage/runtime
failures are reported; partial private artifact objects can require cleanup after
a process crash. This is not a full workspace backup or a transactional remote
object-store restore.

Project `retrieval_mode` accepts lexical, semantic or hybrid; defaults to lexical.
Queued runs pin the selected settings and retrieved revisions when submitted.
Selecting an embedding search mode never configures an embedding service or
implicitly consents to a cloud provider.

Project details expose related conversations and preserve their original citations
when reopened. A failed extraction/index can be retried explicitly using
`POST /api/studio/projects/{project}/documents/{document}/reindex` (empty JSON).
It rejects another project's document, live duplicate jobs and already-ready
revisions. Retry uses the same immutable input revision and existing job service.

New questions use the highest ready revision per artifact. If replacement
indexing or publication fails, the previous ready revision stays searchable.
Pinned old conversations retain their earlier references. Retrieval and exports
recheck current and revision ownership, including archived source revisions;
revoked files are omitted. SQL controller restart, expired indexing leases,
cancellation/retry and late-thread publication now have explicit fixtures. Queued
runs revalidate current authorization while retaining the original permitted
source revision and settings. See [retrieval and recovery contracts](STUDIO-KNOWLEDGE-INDEX.md).
Abrupt subprocess exits cover three index-publication boundaries; package
storage-failure cleanup and size preflight have fixtures. Hosted identity, real
models and VM checks remain separately pending.
