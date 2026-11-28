# Studio assistant and server workspace files

All routes use existing Nexus authentication, project access and CSRF controls.
No arbitrary host path or executable is accepted from a browser or model.

- `GET /api/studio/assistant`: built-in help, live agent/workflow names and UI links.
- `POST /api/studio/assistant/answer` with `question`: bounded local help retrieval,
  no provider invocation or file-content ingestion.
- `POST /api/runs` with `studio_assistant: true`: explicit AI conversation using
  the selected existing provider, built-in instructions and a read-only help tool.
  Cannot combine with a custom agent or a non-chat run. Billing remains provider-owned.
- `GET /api/studio/files?root=files&path=`: roots and a bounded one-level listing.
- `GET /api/studio/files/preview?root=files&path=example.txt`: bounded inert text
  preview and SHA-256 revision; binary files return metadata without executable preview.
- `POST /api/studio/files/upload`: `root`, directory `path`, `filename`,
  `content_base64`. Create only, up to 16 MiB, no overwrite. Writable roots only.
- `POST /api/studio/files/folder`: `root`, directory `path`, `name`. Create only.
- `POST /api/studio/files/snapshot`: `root`, file `path`, SHA-256 `revision`.
  Recheck revision and save an owner-scoped immutable artifact using existing
  storage. Return its existing authenticated download/preview links. This is the
  explicit bridge for agent attachments and knowledge project documents.

Workspace filesystem access requires builder/owner in shared mode, including
registered read-only tools. Exposed folders are workspace-shared for these roles;
private uploads belong in Library artifacts instead. Agents/workflows are
read-only; edits use existing validated editors. The managed `files/` root is
writable. Additional administrator-configured roots default read-only; writable aliases of
agent/workflow directories reject before use. Hidden
paths, traversal, symbolic links, hard-linked files, special files, private state
and common credential filenames are excluded. File contents remain untrusted.
Listing/preview never sends files to a model. Snapshotting does not run an agent.
No delete, arbitrary YAML edit, shell execution or provider auto-fallback exists.
